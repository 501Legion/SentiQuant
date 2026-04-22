# Design Ref: §2.6 — RedditPortfolio: 포지션 관리 + Gap Down + commission + 상태 저장
# Plan SC FR-10: 날짜별 portfolio_state.json 저장
# Plan SC FR-16: 전략별 별도 가상 포트폴리오 (strategy_key로 파일 분리)
# Plan SC FR-18: Stop-Loss(-7%), Trailing Stop(-5%), Gap Down 즉시 청산
import json
import logging
import math
import os
from dataclasses import dataclass, field, asdict
from datetime import date

import config
import wsb_state
from position_sizer import PositionSizer

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """단일 보유 포지션."""
    symbol: str
    entry_date: str
    entry_price: float
    shares: int
    highest_price: float   # 보유 이후 최고 종가 (Trailing Stop 추적용)


class RedditPortfolio:
    """
    Reddit Forward Testing 전용 포트폴리오.

    - 전략별 strategy_key("{model}_{ranking}_{sizing}")로 파일 분리
    - Gap Down: 09:35 ET 시가 확인 → stop_loss 범위 초과 시 시가 청산
    - 수수료: max(거래대금 × 0.25%, $2.0) per leg
    - 날짜별 상태를 data/reddit/YYYY-MM-DD/portfolio_state.json에 저장
    """

    def __init__(
        self,
        strategy_key: str,
        initial_cash: float = None,
    ):
        """
        Args:
            strategy_key: "finbert_mentions_equal" 형태
            initial_cash: None이면 config.INITIAL_CASH 사용
        """
        self.strategy_key = strategy_key
        self.cash = initial_cash if initial_cash is not None else config.INITIAL_CASH
        self.positions: dict[str, Position] = {}
        self.trade_log: list[dict] = []  # 전체 거래 기록

    # ------------------------------------------------------------------
    # 하루 처리
    # ------------------------------------------------------------------

    def process_day(
        self,
        date_str: str,
        top_n: list[str],
        exit_signals: dict[str, str],   # symbol → reason (wsb_signal_engine.check_exit)
        ohlcv: dict[str, dict],          # symbol → {open, close, prev_close}
        sizer: PositionSizer,
        scored: dict[str, dict] = None,  # 감성 점수 (SentimentSizer용)
        atr_cache: dict[str, float] = None,  # ATR (VolatilitySizer용)
        position_scores: dict[str, dict] = None,  # wsb_state position_scores (entry_score 저장)
    ) -> dict:
        """
        하루 포트폴리오 처리.

        처리 순서:
          1. Gap Down 체크 → 시가 즉시 청산 (09:35 ET 모의)
          2. exit_signals에 있는 포지션 종가 청산
          3. highest_price 업데이트 (매도 전에 업데이트하면 안 됨 — 청산 후 업데이트)
          4. 빈 슬롯에 top_n 신규 매수 (내일 09:35 ET 시가 기준 모의)

        Returns:
            {"buys": [...], "sells": [...], "daily_pnl": float, "cash": float, "total_value": float}
        """
        scored = scored or {}
        atr_cache = atr_cache or {}
        position_scores = position_scores if position_scores is not None else wsb_state.load_position_scores()
        daily_buys = []
        daily_sells = []

        # --- Step 1: Gap Down 즉시 청산 (시가) ---
        for symbol in list(self.positions.keys()):
            sym_ohlcv = ohlcv.get(symbol, {})
            open_price = sym_ohlcv.get("open")
            prev_close = sym_ohlcv.get("prev_close")
            if open_price is None or prev_close is None or prev_close <= 0:
                continue

            gap_pct = (open_price - prev_close) / prev_close * 100
            if gap_pct <= config.WSB_GAP_DOWN_PCT:  # -5% (WSB V3)
                trade = self._sell(symbol, open_price, date_str, reason="gap_down")
                if trade:
                    daily_sells.append(trade)
                    wsb_state.remove_position_score(position_scores, symbol)
                    logger.info(
                        f"[{symbol}] Gap Down 청산: gap={gap_pct:.2f}%,"
                        f" open={open_price:.2f}"
                    )

        # --- Step 2: 청산 신호 포지션 종가 청산 ---
        for symbol, reason in exit_signals.items():
            if symbol not in self.positions:
                continue
            sym_ohlcv = ohlcv.get(symbol, {})
            close_price = sym_ohlcv.get("close")
            if close_price is None:
                continue
            trade = self._sell(symbol, close_price, date_str, reason=reason)
            if trade:
                daily_sells.append(trade)
                wsb_state.remove_position_score(position_scores, symbol)

        # --- Step 3: highest_price 업데이트 (청산 후) ---
        for symbol, pos in self.positions.items():
            sym_ohlcv = ohlcv.get(symbol, {})
            close_price = sym_ohlcv.get("close")
            if close_price and close_price > pos.highest_price:
                pos.highest_price = close_price

        # --- Step 4: 신규 매수 ---
        open_slots = config.MAX_POSITIONS - len(self.positions)
        candidates = [s for s in top_n if s not in self.positions]

        for symbol in candidates[:open_slots]:
            sym_ohlcv = ohlcv.get(symbol, {})
            open_price = sym_ohlcv.get("open")
            if open_price is None or open_price <= 0:
                logger.warning(f"[{symbol}] 시가 없음 — 매수 건너뜀")
                continue

            # PositionSizer kwargs 구성
            kwargs = {}
            sym_scored = scored.get(symbol, {})
            if sym_scored:
                kwargs["bullish_ratio"] = sym_scored.get("ratio", 0.5)
            if symbol in atr_cache:
                kwargs["atr"] = atr_cache[symbol]
                prev_close = sym_ohlcv.get("prev_close")
                if prev_close:
                    kwargs["prev_close"] = prev_close

            shares = sizer.calc_shares(self.cash, open_price, **kwargs)
            if shares <= 0:
                logger.warning(f"[{symbol}] 매수 주식 수 0 — 현금 부족 또는 가격 이상")
                continue

            trade = self._buy(symbol, open_price, shares, date_str)
            if trade:
                daily_buys.append(trade)
                # Design Ref: §wsb-signal-v3 §4.3 — 매수 시 entry_score 저장
                today_score = scored.get(symbol, {}).get("score")
                if today_score is not None:
                    wsb_state.upsert_position_score(
                        position_scores, symbol, entry_score=today_score
                    )

        wsb_state.save_position_scores(position_scores)

        # 총 평가액 계산
        total_value = self.cash
        for symbol, pos in self.positions.items():
            sym_ohlcv = ohlcv.get(symbol, {})
            close_price = sym_ohlcv.get("close", pos.entry_price)
            total_value += pos.shares * close_price

        self.save_state(date_str, ohlcv)

        return {
            "buys": daily_buys,
            "sells": daily_sells,
            "cash": round(self.cash, 2),
            "total_value": round(total_value, 2),
        }

    # ------------------------------------------------------------------
    # 매수 / 매도
    # ------------------------------------------------------------------

    def _buy(self, symbol: str, price: float, shares: int, date_str: str) -> dict | None:
        """포지션 기록 + 현금 차감 + 수수료 공제."""
        trade_value = price * shares
        commission = self._calc_commission(trade_value)
        total_cost = trade_value + commission

        if total_cost > self.cash:
            logger.warning(
                f"[{symbol}] 현금 부족: 필요={total_cost:.2f}, 보유={self.cash:.2f}"
            )
            return None

        self.cash -= total_cost
        self.positions[symbol] = Position(
            symbol=symbol,
            entry_date=date_str,
            entry_price=price,
            shares=shares,
            highest_price=price,
        )

        record = {
            "type": "buy",
            "symbol": symbol,
            "date": date_str,
            "price": round(price, 4),
            "shares": shares,
            "trade_value": round(trade_value, 2),
            "commission": round(commission, 2),
        }
        self.trade_log.append(record)
        logger.info(
            f"[{symbol}] 매수: {shares}주 × ${price:.2f}"
            f" = ${trade_value:.2f} (수수료 ${commission:.2f})"
        )
        return record

    def _sell(
        self, symbol: str, price: float, date_str: str, reason: str = ""
    ) -> dict | None:
        """포지션 청산 + P&L 계산(수수료 포함) + 거래 기록."""
        pos = self.positions.pop(symbol, None)
        if pos is None:
            return None

        trade_value = price * pos.shares
        commission = self._calc_commission(trade_value)
        gross_pnl = (price - pos.entry_price) * pos.shares
        net_pnl = gross_pnl - commission
        # 매수 시 수수료도 P&L에 반영
        buy_commission = self._calc_commission(pos.entry_price * pos.shares)
        net_pnl -= buy_commission

        self.cash += trade_value - commission
        pnl_pct = net_pnl / (pos.entry_price * pos.shares) * 100

        record = {
            "type": "sell",
            "symbol": symbol,
            "date": date_str,
            "price": round(price, 4),
            "shares": pos.shares,
            "trade_value": round(trade_value, 2),
            "commission": round(commission, 2),
            "gross_pnl": round(gross_pnl, 2),
            "net_pnl": round(net_pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "reason": reason,
            "entry_price": round(pos.entry_price, 4),
            "entry_date": pos.entry_date,
        }
        self.trade_log.append(record)
        logger.info(
            f"[{symbol}] 매도({reason}): {pos.shares}주 × ${price:.2f}"
            f" → net_pnl=${net_pnl:.2f} ({pnl_pct:.2f}%)"
        )
        return record

    # ------------------------------------------------------------------
    # 수수료 / 상태 저장
    # ------------------------------------------------------------------

    def _calc_commission(self, trade_value: float) -> float:
        """max(trade_value × COMMISSION_RATE, COMMISSION_MIN_USD)"""
        return max(trade_value * config.COMMISSION_RATE, config.COMMISSION_MIN_USD)

    def save_state(self, date_str: str, ohlcv: dict = None) -> None:
        """data/reddit/{date_str}/portfolio_state.json 저장."""
        ohlcv = ohlcv or {}
        dir_path = os.path.join(config.REDDIT_DATA_DIR, date_str)
        os.makedirs(dir_path, exist_ok=True)

        total_value = self.cash
        positions_dict = {}
        for symbol, pos in self.positions.items():
            close = ohlcv.get(symbol, {}).get("close", pos.entry_price)
            total_value += pos.shares * close
            positions_dict[symbol] = {
                "entry_date": pos.entry_date,
                "entry_price": round(pos.entry_price, 4),
                "shares": pos.shares,
                "highest_price": round(pos.highest_price, 4),
            }

        state = {
            "date": date_str,
            "strategy_key": self.strategy_key,
            "cash": round(self.cash, 2),
            "total_value": round(total_value, 2),
            "positions": positions_dict,
            "daily_trades": [
                t for t in self.trade_log if t.get("date") == date_str
            ],
        }

        file_path = os.path.join(dir_path, f"portfolio_state_{self.strategy_key}.json")
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        logger.info(f"portfolio_state 저장: {file_path}")

    def load_state(self, date_str: str) -> bool:
        """지정 날짜 상태 로드. 파일 없으면 False 반환."""
        file_path = os.path.join(
            config.REDDIT_DATA_DIR, date_str,
            f"portfolio_state_{self.strategy_key}.json"
        )
        if not os.path.exists(file_path):
            return False
        with open(file_path, "r", encoding="utf-8") as f:
            state = json.load(f)

        self.cash = state["cash"]
        self.positions = {}
        for symbol, pos_data in state.get("positions", {}).items():
            self.positions[symbol] = Position(
                symbol=symbol,
                entry_date=pos_data["entry_date"],
                entry_price=pos_data["entry_price"],
                shares=pos_data["shares"],
                highest_price=pos_data["highest_price"],
            )
        logger.info(
            f"포트폴리오 로드: {file_path}"
            f" (포지션 {len(self.positions)}개, 현금 ${self.cash:.2f})"
        )
        return True

    def get_summary(self) -> dict:
        """전체 거래 기록 기반 수익률/거래수/승률/MDD 계산."""
        sells = [t for t in self.trade_log if t["type"] == "sell"]
        if not sells:
            return {
                "total_return_pct": 0.0,
                "total_trades": 0,
                "win_rate": 0.0,
                "mdd_pct": 0.0,
            }

        total_net_pnl = sum(t["net_pnl"] for t in sells)
        total_return_pct = total_net_pnl / config.INITIAL_CASH * 100
        wins = [t for t in sells if t["net_pnl"] > 0]
        win_rate = len(wins) / len(sells) * 100

        # MDD 계산: 누적 순자산 시계열 기반
        equity = config.INITIAL_CASH
        peak = equity
        mdd = 0.0
        for t in self.trade_log:
            if t["type"] == "sell":
                equity += t["net_pnl"]
            peak = max(peak, equity)
            dd = (equity - peak) / peak * 100
            mdd = min(mdd, dd)

        return {
            "total_return_pct": round(total_return_pct, 2),
            "total_trades": len(sells),
            "win_rate": round(win_rate, 1),
            "mdd_pct": round(mdd, 2),
        }

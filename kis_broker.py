"""KIS (한국투자증권) 모의투자 OpenAPI Adapter.

Design Ref: §1.1 Option C (Pragmatic Protocol) — Broker도 typing.Protocol로 정의해
SignalProvider와 일관된 추상화를 유지. 구상클래스 KISBroker가 Protocol을 암묵적으로 만족.

Plan SC-01/SC-06/SC-11: 토큰 발급 + 종목 마스터 캐시 + 24h 자동 갱신.
Plan FR-20: KIS_PAPER_TRADING=False 호출 시 RuntimeError로 실전 도메인 차단.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Protocol

import config

logger = logging.getLogger(__name__)


# =============================================================================
# Data Classes (Design §3.1, §5.1)
# =============================================================================

@dataclass(frozen=True)
class PositionSnapshot:
    shares: int
    avg_price: float
    current_price: float


@dataclass(frozen=True)
class AccountSnapshot:
    cash_usd: float
    positions: dict[str, PositionSnapshot]
    updated_at: str  # ISO8601 UTC


@dataclass(frozen=True)
class OrderResult:
    order_no: str
    status: Literal["FILLED", "REJECTED", "PENDING"]
    fill_price: float | None
    fill_shares: int | None
    timestamp: str  # ISO8601 UTC
    error_msg: str | None = None


# =============================================================================
# Broker Protocol (Design §3.1)
# =============================================================================

class Broker(Protocol):
    """Broker 인터페이스 — KISBroker / MockBroker / 향후 Alpaca·IBKR 모두 만족 가능."""

    def connect(self) -> None: ...
    def place_order(
        self,
        symbol: str,
        action: Literal["BUY", "SELL"],
        shares: int,
        price: float | None = None,
    ) -> OrderResult: ...
    def get_account(self) -> AccountSnapshot: ...
    def get_quote(self, symbol: str) -> float: ...
    def get_tradable_symbols(self) -> list[str]: ...


# =============================================================================
# KISBroker Adapter (Design §3.2)
# =============================================================================

# 토큰 만료 5분 전 선제 갱신 (Plan §5 Risk: 24h 만료 누락 방지)
_TOKEN_PREEMPT_REFRESH_SECONDS = 5 * 60

# place_order 네트워크 에러 시 1회 재시도 (Design §6 Error Handling)
_ORDER_RETRY_COUNT = 1


class KISBroker:
    """python-kis 래핑. Broker Protocol 암묵적 만족."""

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        account_no: str,
        paper: bool = True,
    ):
        # Plan SC: FR-20 — paper=False 호출 시 즉시 실전 도메인 차단
        if not paper:
            raise RuntimeError(
                "KIS 실전 도메인 사용은 별도 피처(kis-real-trading)로 분리됨. "
                "config.KIS_PAPER_TRADING=true 로 설정하세요."
            )
        if not app_key or not app_secret or not account_no:
            raise RuntimeError(
                "KIS 환경변수 누락 — .env에 KIS_APP_KEY/KIS_APP_SECRET/KIS_ACCOUNT_NO 설정 필요"
            )

        self._app_key = app_key
        self._app_secret = app_secret
        self._account_no = account_no
        self._paper = paper
        self._kis = None  # python-kis 인스턴스 (lazy)
        self._token_cache_path = Path(config.KIS_TOKEN_CACHE_FILE)
        self._symbols_cache_path = Path(config.KIS_SYMBOLS_FILE)

    # -------------------------------------------------------------------------
    # connect — Plan SC-01, SC-11
    # -------------------------------------------------------------------------

    def connect(self) -> None:
        """OAuth 토큰 발급/갱신. python-kis가 자동 갱신 처리, 본 메서드는 조기 갱신 + 캐시 영속화."""
        # Plan SC: 5분 전 선제 갱신 — 캐시 만료 임박 시 강제 재발급
        cached = self._load_token_cache()
        if cached and not self._token_expiring_soon(cached):
            logger.debug("[KIS] 토큰 캐시 유효 — 재사용")
            self._kis = self._build_kis_client(cached_token=cached.get("access_token"))
            return

        # 신규 발급 (또는 만료 임박 → 강제 재발급)
        self._kis = self._build_kis_client()
        token, expires_at = self._issue_token()
        self._save_token_cache(token, expires_at)
        logger.info(
            f"[KIS] 모의투자 토큰 발급 성공 (만료: {expires_at.isoformat()})"
        )

    def _build_kis_client(self, cached_token: str | None = None):
        """python-kis 클라이언트 생성. 라이브러리 변경 시 본 메서드만 수정 (Design §1.2 Adapter 격리)."""
        try:
            from pykis import PyKis  # type: ignore[import-not-found]
        except ImportError as e:
            raise RuntimeError(
                "python-kis 미설치 — `pip install python-kis` 또는 requirements.txt 설치 필요"
            ) from e

        # python-kis는 모의투자 전용 인자(virtual_id 등)와 실전 인자가 별도.
        # 본 어댑터는 paper=True에서만 동작하므로 모의투자 도메인만 구성.
        # TODO(M1 Open Q): 설치한 python-kis 버전의 PyKis(...) 시그니처 확인 후 인자 정합성 검증.
        return PyKis(
            id=self._account_no,
            account=self._account_no,
            appkey=self._app_key,
            secretkey=self._app_secret,
            virtual_id=self._account_no,
            virtual_appkey=self._app_key,
            virtual_secretkey=self._app_secret,
            keep_token=True,
        )

    def _issue_token(self) -> tuple[str, datetime]:
        """토큰 발급 → (access_token, expires_at). python-kis 내부 토큰을 가져온다."""
        # python-kis는 첫 API 호출 시 자동 토큰 발급 — get_account()로 트리거
        try:
            # 토큰 강제 발급 트리거 (가벼운 잔고조회 1회)
            _ = self._kis.account().balance()  # type: ignore[union-attr]
        except Exception as e:
            raise RuntimeError(f"[KIS] 토큰 발급 실패 — APP_KEY/SECRET/계좌번호 확인: {e}") from e

        # python-kis 내부에서 토큰 정보 추출 (라이브러리 버전별 attribute가 다를 수 있음)
        # TODO(M1 Open Q): python-kis 버전별 토큰 접근 경로 확인
        token = getattr(self._kis, "token", None) or getattr(self._kis, "access_token", "")
        # 24h 보수적 만료 (실제 토큰 만료 시각은 라이브러리가 관리)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
        return str(token), expires_at

    def _load_token_cache(self) -> dict | None:
        if not self._token_cache_path.exists():
            return None
        try:
            return json.loads(self._token_cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"[KIS] 토큰 캐시 읽기 실패 — 재발급: {e}")
            return None

    def _save_token_cache(self, token: str, expires_at: datetime) -> None:
        self._token_cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._token_cache_path.write_text(
            json.dumps(
                {"access_token": token, "expires_at": expires_at.isoformat()},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _token_expiring_soon(cached: dict) -> bool:
        try:
            expires_at = datetime.fromisoformat(cached["expires_at"])
        except (KeyError, ValueError):
            return True  # 파싱 실패 → 만료 간주
        remaining = (expires_at - datetime.now(timezone.utc)).total_seconds()
        return remaining < _TOKEN_PREEMPT_REFRESH_SECONDS

    # -------------------------------------------------------------------------
    # place_order — Plan FR-03, SC-03/SC-04
    # -------------------------------------------------------------------------

    def place_order(
        self,
        symbol: str,
        action: Literal["BUY", "SELL"],
        shares: int,
        price: float | None = None,  # None → 시장가
    ) -> OrderResult:
        """KIS 모의투자 주문 위임. 실패 시 OrderResult.status='REJECTED' 반환 (raise 아님)."""
        if self._kis is None:
            raise RuntimeError("[KIS] connect() 호출 전에 place_order() 실행됨")
        if shares <= 0:
            return OrderResult(
                order_no="", status="REJECTED",
                fill_price=None, fill_shares=None,
                timestamp=_now_iso(), error_msg=f"invalid shares: {shares}",
            )

        last_err: str | None = None
        for attempt in range(_ORDER_RETRY_COUNT + 1):
            try:
                return self._submit_order(symbol, action, shares, price)
            except Exception as e:  # noqa: BLE001 — 모든 실패를 REJECTED로 정규화
                last_err = str(e)
                logger.warning(
                    f"[KIS] {action} {symbol} 주문 실패 (attempt {attempt+1}): {last_err}"
                )
                if attempt < _ORDER_RETRY_COUNT:
                    time.sleep(0.5)

        return OrderResult(
            order_no="", status="REJECTED",
            fill_price=None, fill_shares=None,
            timestamp=_now_iso(), error_msg=last_err,
        )

    def _submit_order(
        self,
        symbol: str,
        action: Literal["BUY", "SELL"],
        shares: int,
        price: float | None,
    ) -> OrderResult:
        """python-kis 주문 호출 → OrderResult 정규화. 라이브러리 변경 시 본 메서드만 수정."""
        # TODO(M3 Open Q): python-kis 버전별 stock(...) / order(...) API 정합성 확인
        stock = self._kis.stock(symbol, market="NASDAQ")  # type: ignore[union-attr]
        if action == "BUY":
            resp = stock.buy(qty=shares, price=price) if price else stock.buy(qty=shares)
        else:  # SELL
            resp = stock.sell(qty=shares, price=price) if price else stock.sell(qty=shares)

        # 응답 정규화 (KIS 응답 구조 → OrderResult)
        order_no = str(getattr(resp, "order_no", "") or getattr(resp, "odno", ""))
        # python-kis 응답이 거부/체결 상태를 명시적으로 주지 않을 수 있음 → 주문번호 유무로 판정
        if not order_no:
            error_msg = str(getattr(resp, "msg", "") or getattr(resp, "msg1", "unknown error"))
            return OrderResult(
                order_no="", status="REJECTED",
                fill_price=None, fill_shares=None,
                timestamp=_now_iso(), error_msg=error_msg,
            )

        # 모의투자는 즉시 체결 가정 — 실제 체결가/수량은 잔고 조회로 확인 가능하나
        # 여기서는 주문 응답 시점의 가격을 기록 (시장가의 경우 quote 폴백)
        fill_price = price or self.get_quote(symbol)
        return OrderResult(
            order_no=order_no,
            status="FILLED",
            fill_price=fill_price,
            fill_shares=shares,
            timestamp=_now_iso(),
            error_msg=None,
        )

    # -------------------------------------------------------------------------
    # get_account — Plan FR-04
    # -------------------------------------------------------------------------

    def get_account(self) -> AccountSnapshot:
        """KIS 잔고조회. Source of Truth — Design §1.2."""
        if self._kis is None:
            raise RuntimeError("[KIS] connect() 호출 전에 get_account() 실행됨")
        balance = self._kis.account().balance()  # type: ignore[union-attr]

        # python-kis 응답 정규화 (라이브러리별 attribute가 다를 수 있음)
        # TODO(M2 Open Q): balance.foreign_cash / balance.deposits 등 외화현금 필드 확인
        cash_usd = float(
            getattr(balance, "foreign_cash_usd", None)
            or getattr(balance, "frcr_dncl_amt", None)
            or 0.0
        )
        positions: dict[str, PositionSnapshot] = {}
        for stock in getattr(balance, "stocks", []) or []:
            sym = str(getattr(stock, "symbol", "") or getattr(stock, "pdno", ""))
            if not sym:
                continue
            positions[sym] = PositionSnapshot(
                shares=int(getattr(stock, "qty", 0) or getattr(stock, "ovrs_cblc_qty", 0)),
                avg_price=float(getattr(stock, "avg_price", 0.0) or getattr(stock, "pchs_avg_pric", 0.0)),
                current_price=float(getattr(stock, "current_price", 0.0) or getattr(stock, "now_pric2", 0.0)),
            )

        return AccountSnapshot(
            cash_usd=cash_usd,
            positions=positions,
            updated_at=_now_iso(),
        )

    # -------------------------------------------------------------------------
    # get_quote — Plan FR-18 폴백 대상
    # -------------------------------------------------------------------------

    def get_quote(self, symbol: str) -> float:
        """KIS 해외주식 현재가."""
        if self._kis is None:
            raise RuntimeError("[KIS] connect() 호출 전에 get_quote() 실행됨")
        try:
            stock = self._kis.stock(symbol, market="NASDAQ")  # type: ignore[union-attr]
            quote = stock.quote()
            price = float(
                getattr(quote, "price", None)
                or getattr(quote, "current", None)
                or getattr(quote, "last", None)
                or 0.0
            )
            if price <= 0:
                raise RuntimeError(f"invalid quote response for {symbol}")
            return price
        except Exception as e:
            logger.warning(f"[KIS] {symbol} 현재가 조회 실패: {e}")
            raise

    # -------------------------------------------------------------------------
    # get_tradable_symbols — Plan FR-14, SC-06
    # -------------------------------------------------------------------------

    def get_tradable_symbols(self) -> list[str]:
        """매매 가능 미국 종목 캐시. KIS_SYMBOLS_REFRESH_DAYS 만료 시에만 마스터 재조회."""
        cached = self._load_symbols_cache()
        if cached is not None:
            return cached

        # 캐시 만료 또는 미존재 → 마스터 재조회
        try:
            symbols = self._fetch_tradable_symbols()
        except Exception as e:
            logger.warning(f"[KIS] 종목 마스터 조회 실패 — 마지막 캐시로 폴백: {e}")
            stale = self._load_symbols_cache(allow_stale=True)
            if stale is None:
                raise RuntimeError(
                    "[KIS] 종목 마스터 조회 실패 + 캐시 없음 — 매매 가능 종목 확인 불가"
                ) from e
            logger.warning("[KIS] 만료된 종목 캐시 강제 사용")
            return stale

        self._save_symbols_cache(symbols)
        return symbols

    def _fetch_tradable_symbols(self) -> list[str]:
        """KIS 종목 마스터 API 호출. python-kis가 mst 파일 다운로드를 래핑."""
        if self._kis is None:
            raise RuntimeError("[KIS] connect() 호출 전에 get_tradable_symbols() 실행됨")
        # TODO(M3 Open Q): python-kis가 종목 마스터를 직접 노출하는지 확인.
        # 노출 안 하면 자체 mst 파일 다운로드 또는 첫 주문 시도 후 거부 응답 학습 방식으로 대체.
        market = getattr(self._kis, "market", None)
        if market is None or not hasattr(market, "symbols"):
            logger.warning(
                "[KIS] python-kis가 market.symbols() 미노출 — "
                "config.SYMBOLS 전체를 매매 가능으로 가정 (REJECTED 응답으로 사후 학습)"
            )
            return list(config.SYMBOLS)

        raw = market.symbols(market="NASDAQ")
        return [str(s) for s in raw if s]

    def _load_symbols_cache(self, allow_stale: bool = False) -> list[str] | None:
        if not self._symbols_cache_path.exists():
            return None
        try:
            data = json.loads(self._symbols_cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"[KIS] 종목 캐시 읽기 실패: {e}")
            return None

        if not allow_stale:
            try:
                updated_at = datetime.fromisoformat(data["updated_at"])
            except (KeyError, ValueError):
                return None
            age = datetime.now(timezone.utc) - updated_at
            if age > timedelta(days=config.KIS_SYMBOLS_REFRESH_DAYS):
                return None

        return list(data.get("tradable", []))

    def _save_symbols_cache(self, symbols: list[str]) -> None:
        self._symbols_cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": _now_iso(),
            "refresh_days": config.KIS_SYMBOLS_REFRESH_DAYS,
            "tradable": symbols,
        }
        self._symbols_cache_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


# =============================================================================
# Factory (Design §3.2 get_broker)
# =============================================================================

def get_broker() -> Broker:
    """Factory — config 환경변수로 KISBroker 인스턴스 반환."""
    return KISBroker(
        app_key=config.KIS_APP_KEY,
        app_secret=config.KIS_APP_SECRET,
        account_no=config.KIS_ACCOUNT_NO,
        paper=config.KIS_PAPER_TRADING,
    )


# =============================================================================
# Helpers
# =============================================================================

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

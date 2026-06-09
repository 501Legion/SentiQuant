# Design Ref: community-opinion-agent §3.1 — UniverseFilter + UniverseDecision
# Plan FR-00.1~3: universe_mode별 거래 가능성 판정. 정적 JSON index 리스트 + OHLCV 유동성.
# 회귀 보호: COMMUNITY_ENABLE_UNIVERSE_FILTER=False → 무조건 allowed/CORE/size 1.0.
import json
import logging
import os
from dataclasses import dataclass, field

import config

logger = logging.getLogger(__name__)

TIER_CORE = "CORE"
TIER_EXPANDED = "EXPANDED"
TIER_COMMUNITY_LIQUID = "COMMUNITY_LIQUID"
TIER_BLOCKED = "BLOCKED"

# mode → 허용 tier 집합 (sp500_only/nasdaq100_only/custom_watchlist은 멤버십으로 별도 판정)
_MODE_ALLOWED_TIERS = {
    "sp500_nasdaq100": {TIER_CORE},
    "liquid_us": {TIER_CORE, TIER_EXPANDED},
    "community_liquid": {TIER_CORE, TIER_EXPANDED, TIER_COMMUNITY_LIQUID},
}


@dataclass
class UniverseDecision:
    symbol: str
    allowed: bool
    universe_tier: str                       # CORE | EXPANDED | COMMUNITY_LIQUID | BLOCKED
    reason_codes: list = field(default_factory=list)
    liquidity_score: float = 0.0             # 0~1 (avg_dollar_volume 정규화)
    tradeability_score: float = 0.0          # 0~1 (유동성 - ambiguity 페널티)
    size_multiplier: float = 1.0             # CORE 1.0 / EXPANDED·COMMUNITY_LIQUID 0.5


def load_universe_sets() -> tuple[set, set, dict]:
    """data/universe/{sp500,nasdaq100}.json → (sp500:set, nasdaq100:set, market_caps:dict).
    파일 없으면 빈 set/{} (안전 폴백)."""
    base = config.COMMUNITY_UNIVERSE_DATA_DIR
    sp500: set[str] = set()
    nasdaq100: set[str] = set()
    caps: dict[str, float] = {}
    for name, target in (("sp500.json", "sp500"), ("nasdaq100.json", "nasdaq100")):
        path = os.path.join(base, name)
        if not os.path.exists(path):
            logger.debug(f"universe 파일 없음: {path}")
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            syms = set(data.get("symbols", []))
            if target == "sp500":
                sp500 = syms
            else:
                nasdaq100 = syms
            for s, c in (data.get("market_caps") or {}).items():
                caps[s] = c
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"universe 파일 로드 실패 {path}: {e}")
    return sp500, nasdaq100, caps


def compute_avg_dollar_volume(ohlcv, lookback: int = 20) -> float | None:
    """OHLCV DataFrame([date,open,high,low,close,volume])에서 최근 lookback일
    평균 거래대금(close×volume). 데이터 부족/오류 시 None."""
    if ohlcv is None:
        return None
    try:
        if getattr(ohlcv, "empty", True):
            return None
        if "volume" not in ohlcv.columns or "close" not in ohlcv.columns:
            return None
        tail = ohlcv.tail(lookback)
        dv = (tail["close"] * tail["volume"]).mean()
        return float(dv) if dv == dv else None   # NaN guard
    except Exception:
        return None


class UniverseFilter:
    """symbol이 현재 universe_mode에서 거래 가능한지 판정 → UniverseDecision."""

    def __init__(self, mode: str = None, *, sp500: set = None,
                 nasdaq100: set = None, market_caps: dict = None):
        """sp500/nasdaq100/market_caps를 직접 주입하면 파일 로드 생략(테스트 용이)."""
        self.mode = mode or config.COMMUNITY_UNIVERSE_MODE
        if sp500 is None and nasdaq100 is None and market_caps is None:
            self.sp500, self.nasdaq100, self.market_caps = load_universe_sets()
        else:
            self.sp500 = set(sp500 or [])
            self.nasdaq100 = set(nasdaq100 or [])
            self.market_caps = dict(market_caps or {})

    def decide(self, symbol: str, *, ohlcv=None, price: float = None,
               avg_dollar_volume: float = None, market_cap: float = None,
               ambiguity_risk: bool = False) -> UniverseDecision:
        # 필터 OFF → 무조건 허용 (회귀 0)
        if not config.COMMUNITY_ENABLE_UNIVERSE_FILTER:
            return UniverseDecision(symbol, True, TIER_CORE, ["FILTER_DISABLED"],
                                    1.0, 1.0, 1.0)

        reasons: list[str] = []
        in_sp = symbol in self.sp500
        in_nq = symbol in self.nasdaq100
        in_index = in_sp or in_nq

        # --- 데이터 보강 ---
        if avg_dollar_volume is None:
            avg_dollar_volume = compute_avg_dollar_volume(ohlcv)
        if price is None and ohlcv is not None and not getattr(ohlcv, "empty", True):
            try:
                price = float(ohlcv.iloc[-1]["close"])
            except Exception:
                price = None
        if market_cap is None:
            market_cap = self.market_caps.get(symbol)

        # liquidity_score: avg_dollar_volume를 MIN×5 기준으로 정규화 clamp[0,1]
        if avg_dollar_volume is not None:
            liq = max(0.0, min(1.0, avg_dollar_volume
                               / (config.COMMUNITY_MIN_AVG_DOLLAR_VOLUME * 5)))
        else:
            liq = 0.0

        # --- 차단 조건 ---
        blocked = False
        if ambiguity_risk:
            reasons.append("TICKER_AMBIGUOUS")
            blocked = True
        if (price is not None and config.COMMUNITY_EXCLUDE_PENNY_STOCKS
                and price < config.COMMUNITY_MIN_PRICE_USD):
            reasons.append("PENNY_STOCK")
            blocked = True
        low_volume = (avg_dollar_volume is not None
                      and avg_dollar_volume < config.COMMUNITY_MIN_AVG_DOLLAR_VOLUME)
        if low_volume:
            reasons.append("LOW_DOLLAR_VOLUME")
            if not in_index:        # 인덱스 종목은 유동성 통과로 간주
                blocked = True
        if (market_cap is not None and market_cap < config.COMMUNITY_MIN_MARKET_CAP
                and not in_index):
            reasons.append("LOW_MARKET_CAP")
            blocked = True

        # --- tier 결정 ---
        if blocked:
            tier = TIER_BLOCKED
        elif in_index:
            tier = TIER_CORE
            reasons.append("INDEX_CORE")
        else:
            liquid_ok = (avg_dollar_volume is not None
                         and avg_dollar_volume >= config.COMMUNITY_MIN_AVG_DOLLAR_VOLUME)
            cap_ok = (market_cap is None
                      or market_cap >= config.COMMUNITY_MIN_MARKET_CAP)
            if liquid_ok and cap_ok and market_cap is not None:
                tier = TIER_EXPANDED
                reasons.append("NON_INDEX_LARGE_CAP")
            elif liquid_ok and config.COMMUNITY_ALLOW_NON_INDEX_IF_LIQUID:
                tier = TIER_COMMUNITY_LIQUID
                reasons.append("NON_INDEX_LIQUID")
            else:
                tier = TIER_BLOCKED
                reasons.append("INSUFFICIENT_LIQUIDITY")

        # --- mode → allowed ---
        allowed = self._allowed_by_mode(symbol, tier, in_sp, in_nq, reasons)
        if tier == TIER_BLOCKED:
            allowed = False

        size_mult = 1.0 if tier == TIER_CORE else config.COMMUNITY_NON_INDEX_SIZE_MULTIPLIER
        tradeability = 0.0 if ambiguity_risk else liq

        return UniverseDecision(
            symbol=symbol, allowed=allowed, universe_tier=tier,
            reason_codes=reasons, liquidity_score=round(liq, 4),
            tradeability_score=round(tradeability, 4), size_multiplier=size_mult,
        )

    def _allowed_by_mode(self, symbol, tier, in_sp, in_nq, reasons) -> bool:
        mode = self.mode
        if mode == "custom_watchlist":
            ok = symbol in set(config.COMMUNITY_CUSTOM_WATCHLIST)
            if not ok:
                reasons.append("NOT_IN_WATCHLIST")
            return ok
        if mode == "sp500_only":
            if not in_sp:
                reasons.append("NOT_SP500")
                return False
            return True
        if mode == "nasdaq100_only":
            if not in_nq:
                reasons.append("NOT_NASDAQ100")
                return False
            return True
        allowed_tiers = _MODE_ALLOWED_TIERS.get(
            mode, {TIER_CORE, TIER_EXPANDED, TIER_COMMUNITY_LIQUID}
        )
        ok = tier in allowed_tiers
        if not ok:
            reasons.append(f"TIER_{tier}_NOT_ALLOWED_IN_{mode}")
        return ok

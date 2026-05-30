# Design Ref: community-opinion-agent §3.2 — CostAwareTradeFilter + CostAwareTradeDecision
# Plan FR-00.4~5: 수수료·슬리피지·스프레드 대비 기대 움직임(edge)이 충분한지 판정.
# 단위 규약: 모든 pct 입력/출력은 "퍼센트"(예: 5.0 = 5%). 회귀 보호: 필터 OFF → allowed.
import logging
from dataclasses import dataclass, field

import config

logger = logging.getLogger(__name__)


@dataclass
class CostAwareTradeDecision:
    allowed: bool
    reason_codes: list = field(default_factory=list)
    round_trip_cost_pct: float = 0.0       # 왕복 비용(%)
    expected_edge_proxy: float = 0.0       # 기대 움직임 proxy(%)
    edge_to_cost_ratio: float = 0.0
    cost_risk_factor: float = 1.0          # 사이징 곱 factor (여유 작으면 <1.0)
    recommended_action: str = "ENTER"      # ENTER | DOWNSIZE | SKIP


class CostAwareTradeFilter:
    """expected_edge_proxy < round_trip_cost × MULTIPLIER → SKIP / 경계 → DOWNSIZE."""

    def evaluate(self, *, atr_pct: float = None, recent_volatility_pct: float = None,
                 opinion_conviction: float = None,
                 commission_rate: float = None) -> CostAwareTradeDecision:
        """
        Args (모두 선택, 우선순위대로 edge 결정):
            atr_pct: ATR / price × 100 (%)
            recent_volatility_pct: 최근 평균 변동폭 (%)
            opinion_conviction: 0~1 (의견 확신도 → expected_move_proxy 환산)
            commission_rate: None이면 config.COMMISSION_RATE
        """
        if not config.COMMUNITY_ENABLE_COST_AWARE_FILTER:
            return CostAwareTradeDecision(
                allowed=True, reason_codes=["FILTER_DISABLED"],
                round_trip_cost_pct=0.0, expected_edge_proxy=0.0,
                edge_to_cost_ratio=0.0, cost_risk_factor=1.0,
                recommended_action="ENTER",
            )

        comm = commission_rate if commission_rate is not None else config.COMMISSION_RATE
        round_trip_pct = (comm * 2
                          + config.COMMUNITY_ESTIMATED_SLIPPAGE_PCT
                          + config.COMMUNITY_ESTIMATED_SPREAD_PCT) * 100.0

        reasons: list[str] = []

        # --- expected_edge_proxy (우선순위: ATR → 변동폭 → conviction) ---
        if atr_pct is not None and atr_pct > 0:
            edge = atr_pct
            reasons.append("EDGE_FROM_ATR")
        elif recent_volatility_pct is not None and recent_volatility_pct > 0:
            edge = recent_volatility_pct
            reasons.append("EDGE_FROM_VOLATILITY")
        elif opinion_conviction is not None:
            edge = max(0.0, opinion_conviction) * round_trip_pct * 3.0
            reasons.append("EDGE_FROM_CONVICTION")
        else:
            edge = 0.0
            reasons.append("NO_EDGE_DATA")

        ratio = (edge / round_trip_pct) if round_trip_pct > 0 else 0.0
        min_ratio = config.COMMUNITY_MIN_EDGE_TO_COST_MULTIPLIER

        # --- ATR 최소 변동성 게이트 (atr 기반 edge일 때만) ---
        if atr_pct is not None and atr_pct < config.COMMUNITY_MIN_ATR_PCT_FOR_TRADE:
            reasons.append("ATR_BELOW_MIN")
            return CostAwareTradeDecision(
                False, reasons, round(round_trip_pct, 4), round(edge, 4),
                round(ratio, 3), 0.0, "SKIP",
            )

        # --- edge vs cost ---
        if ratio < min_ratio:
            reasons.append("EDGE_BELOW_COST_THRESHOLD")
            return CostAwareTradeDecision(
                False, reasons, round(round_trip_pct, 4), round(edge, 4),
                round(ratio, 3), 0.0, "SKIP",
            )
        if ratio < min_ratio * 1.5:
            reasons.append("EDGE_MARGINAL")
            return CostAwareTradeDecision(
                True, reasons, round(round_trip_pct, 4), round(edge, 4),
                round(ratio, 3), 0.7, "DOWNSIZE",
            )
        reasons.append("EDGE_SUFFICIENT")
        return CostAwareTradeDecision(
            True, reasons, round(round_trip_pct, 4), round(edge, 4),
            round(ratio, 3), 1.0, "ENTER",
        )

"""신호 엔진 추상화 — SignalProvider Protocol + Provider 디스패처.

Design Ref: §3.3 — SignalProvider Protocol. FinBERT/GPT-5 신호 엔진을 단일
config 변수(SIGNAL_ENGINE)로 교체 가능하게 추상화한다.

Plan FR-08: SignalProvider Protocol 정의 (단일 메서드 generate_signals).
Plan FR-09: config.SIGNAL_ENGINE으로 Provider 선택 (기본 "finbert").
Plan SC-08: SIGNAL_ENGINE="finbert" 기본값에서 기존 동작 100% 동일 (회귀 없음).
Plan SC-09: SIGNAL_ENGINE="gpt5"는 NotImplementedError (Design 결정 전 의도된 동작).

Note: Design §3.3 의사코드는 FinbertProvider가 signals.generate_signals_for_all()을
호출하나, 본 구현에서 generate_signals_for_all()은 디스패처이므로 그대로 호출하면
무한 재귀가 된다. 실제 신호 계산 로직은 signals._generate_signals_finbert()로 분리해
FinbertProvider가 그쪽을 래핑한다 (Design 의도 = 기존 동작 보존, 명칭만 차이).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SignalProvider(Protocol):
    """신호 엔진 인터페이스 — FinbertProvider / (향후) Gpt5Provider 모두 만족.

    Plan FR-08: 단일 메서드 generate_signals. KIS 연동과 독립적이나 trader.py /
    scheduler.py를 함께 건드리는 변경이라 동일 피처에서 다룬다.
    """

    name: str

    def generate_signals(self, symbols: list[str]) -> dict[str, dict]: ...


class FinbertProvider:
    """기존 RSI + FinBERT 신호 파이프라인 래퍼 — Broker Protocol을 암묵 만족.

    실제 계산은 signals._generate_signals_finbert()에 그대로 위임하므로
    SIGNAL_ENGINE="finbert"(기본값)에서 기존 동작과 100% 동일하다 (SC-08).
    """

    name = "finbert"

    def generate_signals(self, symbols: list[str]) -> dict[str, dict]:
        # 지연 import — signals ↔ signal_provider 순환 import 방지
        from signals import _generate_signals_finbert

        return _generate_signals_finbert(symbols)


def get_provider(name: str) -> SignalProvider:
    """SIGNAL_ENGINE 이름 → Provider 인스턴스 매핑 (Plan FR-09, Design §3.3).

    Args:
        name: "finbert" 또는 "gpt5"

    Returns:
        SignalProvider 구상 인스턴스

    Raises:
        NotImplementedError: name="gpt5" — 별도 피처 'signal-engine-decision'에서 결정 (SC-09)
        ValueError: 알 수 없는 엔진 이름
    """
    if name == "finbert":
        return FinbertProvider()
    if name == "gpt5":
        raise NotImplementedError(
            "GPT-5 신호 엔진은 별도 피처 'signal-engine-decision'에서 결정 후 구현됩니다. "
            "config.SIGNAL_ENGINE='finbert'를 사용하세요."
        )
    raise ValueError(f"알 수 없는 신호 엔진: '{name}' (가능: finbert | gpt5)")

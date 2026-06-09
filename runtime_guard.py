# Design Ref: live-scheduler-deploy §2/§6.1 — 무인 실주문 안전장치 (순수 정책 + 얇은 IO)
# 키스위치·일일/노출 한도·heartbeat·기동 자가점검. scheduler/community_live가 호출.
# 판단 로직(신호/사이징/라우터)은 건드리지 않고, "주문 실행 전 게이트/관측"으로만 동작.
import json
import logging
import os
from datetime import datetime, timezone

import config

logger = logging.getLogger(__name__)


# ── 키스위치 (D3) ──────────────────────────────────────────────────────────
def is_halted() -> bool:
    """주문 중단 여부. data/TRADING_HALT 파일 존재 OR env TRADING_HALT=1 → True.
    # Plan SC: SC-03 — 주문만 스킵, 스케줄러/수집/로그는 유지."""
    if os.getenv("TRADING_HALT", "").strip() in ("1", "true", "True"):
        return True
    return os.path.exists(config.TRADING_HALT_FILE)


# ── 일일/노출 한도 (D2, 순수) ────────────────────────────────────────────────
def filter_by_limits(
    buy_intents: list,
    *,
    equity: float,
    positions_value: float,
    position_value_by_symbol: dict,
    today_buy_count: int,
) -> tuple[list, list[str]]:
    """매수 주문 후보를 일일 건수·노출 한도로 필터 (순수 함수).
    # Plan SC: SC-04 — 일일 매수 건수 + 총/종목당 노출 상한. 매도/청산은 무관(리스크 축소).

    Args:
        buy_intents: list of (intent, price) — intent.symbol, intent.shares
        equity: 총자산(현금+평가액) 기준
        positions_value: 현재 보유 평가액 합계
        position_value_by_symbol: {symbol: 현재 평가액}
        today_buy_count: 오늘 이미 체결/시도한 신규 매수 건수
    Returns:
        (allowed: list[(intent,price)], blocked: list[str] 사유)
    """
    remaining = max(0, config.MAX_DAILY_BUYS - today_buy_count)
    allowed, blocked = [], []
    running = float(positions_value)
    for intent, price in buy_intents:
        sym = getattr(intent, "symbol", "?")
        shares = getattr(intent, "shares", 0) or 0
        if len(allowed) >= remaining:
            blocked.append(f"{sym}: 일일 매수 한도({config.MAX_DAILY_BUYS}) 도달")
            continue
        buy_value = float(price or 0) * shares
        if equity > 0:
            sym_after = position_value_by_symbol.get(sym, 0.0) + buy_value
            if sym_after / equity * 100 > config.MAX_SYMBOL_WEIGHT_PCT:
                blocked.append(
                    f"{sym}: 종목 비중 {sym_after/equity*100:.0f}% > {config.MAX_SYMBOL_WEIGHT_PCT:.0f}%")
                continue
            if (running + buy_value) / equity * 100 > config.MAX_TOTAL_EXPOSURE_PCT:
                blocked.append(
                    f"{sym}: 총 노출 {(running+buy_value)/equity*100:.0f}% > {config.MAX_TOTAL_EXPOSURE_PCT:.0f}%")
                continue
        allowed.append((intent, price))
        running += buy_value
    return allowed, blocked


# ── heartbeat (D5) ──────────────────────────────────────────────────────────
def read_heartbeat(path: str = None) -> dict:
    path = path or config.HEARTBEAT_FILE
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def write_heartbeat(job: str, path: str = None, now: datetime = None) -> None:
    """잡 성공 시각 기록. 실패는 무시(관측용, 매매 무영향)."""
    path = path or config.HEARTBEAT_FILE
    now = now or datetime.now(timezone.utc)
    try:
        hb = read_heartbeat(path)
        hb[job] = now.isoformat()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(hb, f, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"heartbeat 기록 실패(무시): {e}")


def heartbeat_stale(job: str, *, now: datetime = None, minutes: int = None,
                    hb: dict = None) -> bool:
    """해당 잡 heartbeat가 minutes보다 오래됐으면 True(hang 추정). 기록 없으면 stale 간주.
    # Plan SC: SC-09 (워치독이 사용)."""
    now = now or datetime.now(timezone.utc)
    minutes = minutes if minutes is not None else config.WATCHDOG_STALE_MINUTES
    hb = hb if hb is not None else read_heartbeat()
    ts = hb.get(job)
    if not ts:
        return True
    try:
        last = datetime.fromisoformat(ts)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return (now - last).total_seconds() > minutes * 60


# ── 기동 자가점검 (D4) ───────────────────────────────────────────────────────
def selfcheck() -> list[str]:
    """기동/잡 시작 시 점검. 빈 리스트=정상. 항목 있으면 주문 차단 권장.
    # Plan SC: SC-06 — 자격/TZ/필수파일/모델/paper 모드."""
    fails = []
    for name in ("KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT_NO",
                 "REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "POLYGON_API_KEY"):
        if not getattr(config, name, ""):
            fails.append(f"자격증명 누락: {name}")
    if not getattr(config, "TIMEZONE", ""):
        fails.append("TIMEZONE 미설정")
    # paper-only 설계 — 실계좌 모드면 차단 (안전)
    if not getattr(config, "KIS_PAPER_TRADING", True):
        fails.append("KIS_PAPER_TRADING=False (실계좌 모드 — paper-only 설계 위반)")
    # FinBERT 모델 (provisioning 갭: models/ 비-git, 누락 시 전부 neutral→매수 0)
    model = os.path.join("models", "finbert-onnx", "model.onnx")
    if not os.path.exists(model):
        fails.append(f"FinBERT 모델 없음: {model} (scp 전달 필요)")
    return fails

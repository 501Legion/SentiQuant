# Design Ref: live-scheduler-deploy §2/§6 — Slack webhook 알림 (D7: 미설정 no-op, 비밀 마스킹)
# 주문 체결 요약·오류·키스위치·헬스실패를 푸시. 실패해도 매매 무영향(graceful).
import logging
import re

import config

logger = logging.getLogger(__name__)

# 페이로드에서 마스킹할 비밀 패턴 (키/토큰/시크릿 값 노출 방지, SC-08)
_SECRET_RE = re.compile(
    r"(?i)(app[_-]?key|app[_-]?secret|api[_-]?key|client[_-]?secret|token|password)"
    r"\s*[=:]\s*\S+"
)


def _mask(text: str) -> str:
    return _SECRET_RE.sub(r"\1=***", str(text))


def notify(event: str, message: str = "", payload: dict = None) -> bool:
    """이벤트 알림 발송. SLACK_WEBHOOK_URL 미설정 시 no-op(False 반환, 예외 없음).
    # Plan SC: SC-05 (주문/오류/할트/헬스), SC-08 (마스킹).

    Args:
        event: "order" | "error" | "halt" | "healthcheck" 등 라벨
        message: 사람용 요약
        payload: 부가 정보(dict) — 마스킹 후 코드블록 첨부
    Returns:
        실제 발송 성공 True, no-op/실패 False.
    """
    url = getattr(config, "SLACK_WEBHOOK_URL", "") or ""
    text = f"[SentiQuant:{event}] {_mask(message)}"
    if payload:
        text += "\n```" + _mask(str(payload))[:1500] + "```"
    if not url:
        logger.info(f"[notifier no-op] {text}")  # 웹훅 미설정 — 로그만
        return False
    try:
        import requests
        resp = requests.post(url, json={"text": text}, timeout=10)
        if resp.status_code >= 300:
            logger.warning(f"[notifier] Slack 응답 {resp.status_code}")
            return False
        return True
    except Exception as e:  # noqa: BLE001 — 알림 실패가 매매를 막지 않음(NFR-03)
        logger.warning(f"[notifier] 발송 실패(무시): {e}")
        return False

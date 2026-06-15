#!/usr/bin/env python3
# Design Ref: live-scheduler-deploy §6.3 FR-09 — 외부 워치독 (Approach B)
# systemd timer/cron이 주기 실행. heartbeat가 stale(=hang 추정)이면 알림 + (옵션)서비스 재시작.
# crash는 systemd Restart=always가, hang은 이 워치독이 복구한다.
#
# 감시 대상:
#   alive — 스케줄러가 5분마다 갱신하는 생존 신호. WATCHDOG_STALE_MINUTES 초과 시 hang.
#   order/signal — 거래일 1회 갱신. 예정 시각+유예시간이 지난 뒤 "오늘 실행" 여부만 검사.
#   (일일 잡을 단순 stale minutes로 감시하면 하루 대부분이 stale → 무한 재시작되므로 금지)
#
# 사용:
#   python scripts/watchdog_check.py            # 검사만 (stale면 exit 1 + 알림)
#   python scripts/watchdog_check.py --restart  # stale면 systemctl restart도 수행
import json
import os
import subprocess
import sys
from datetime import datetime, time, timedelta, timezone

sys.path.insert(0, __file__.rsplit("scripts", 1)[0] or ".")

import config
import runtime_guard
import notifier
import pandas_market_calendars as mcal
import pytz

_SERVICE = "sentiquant.service"
_ET = pytz.timezone(config.TIMEZONE)
_NYSE = mcal.get_calendar("NYSE")
_STATE_FILE = getattr(config, "WATCHDOG_RESTART_STATE_FILE", "data/watchdog_restart_state.json")
_DAILY_JOBS = [
    {
        "job": "order",
        "hour": config.ORDER_JOB_HOUR,
        "minute": config.ORDER_JOB_MINUTE,
        "grace_minutes": getattr(config, "WATCHDOG_ORDER_GRACE_MINUTES", 90),
    },
    {
        "job": "signal",
        "hour": config.SIGNAL_JOB_HOUR,
        "minute": config.SIGNAL_JOB_MINUTE,
        "grace_minutes": getattr(config, "WATCHDOG_SIGNAL_GRACE_MINUTES", 180),
    },
]


def _now_et() -> datetime:
    return datetime.now(timezone.utc).astimezone(_ET)


def _parse_ts(value: str) -> datetime | None:
    try:
        ts = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _is_trading_day(day) -> bool:
    try:
        schedule = _NYSE.schedule(start_date=day.isoformat(), end_date=day.isoformat())
        return not schedule.empty
    except Exception as e:  # noqa: BLE001
        print(f"[watchdog] NYSE 캘린더 확인 실패({e}) — weekday 기준으로 대체")
        return day.weekday() < 5


def _due_at_today(now_et: datetime, *, hour: int, minute: int) -> datetime:
    due = datetime.combine(now_et.date(), time(hour=hour, minute=minute))
    return _ET.localize(due)


def _read_state() -> dict:
    if not os.path.exists(_STATE_FILE):
        return {}
    try:
        with open(_STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def _write_state(state: dict) -> None:
    os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
    with open(_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)


def _restart_key(job: str, due: datetime) -> str:
    return f"{job}:{due.date().isoformat()}"


def _restart() -> None:
    try:
        subprocess.run(["sudo", "-n", "/usr/bin/systemctl", "restart", _SERVICE], check=True, timeout=30)
        notifier.notify("watchdog", f"{_SERVICE} 재시작 수행")
        print(f"[watchdog] sudo systemctl restart {_SERVICE} 완료")
    except Exception as e:  # noqa: BLE001
        notifier.notify("error", f"워치독 재시작 실패: {e}")
        print(f"[watchdog] 재시작 실패: {e}")


def _check_alive(hb: dict) -> list[dict]:
    job = "alive"
    minutes = config.WATCHDOG_STALE_MINUTES
    last = hb.get(job, "(기록 없음)")
    if not runtime_guard.heartbeat_stale(job, minutes=minutes, hb=hb):
        print(f"[watchdog] OK — {job} heartbeat={last}")
        return []
    if last == "(기록 없음)":
        print(f"[watchdog] {job} heartbeat 기록 없음 — 초기 상태로 보고 생략")
        return []
    msg = f"heartbeat stale - {job} 마지막={last}, 한도 {minutes}분 초과 (hang 추정)"
    print(f"[watchdog] STALE - {msg}")
    notifier.notify("watchdog", msg, {"job": job, "last": last})
    return [{"job": job, "message": msg, "restart_key": None}]


def _check_daily_job(spec: dict, hb: dict, now_et: datetime) -> list[dict]:
    job = spec["job"]
    last = hb.get(job, "(기록 없음)")
    if not _is_trading_day(now_et.date()):
        print(f"[watchdog] SKIP — {job}: 오늘은 NYSE 휴장일")
        return []

    due = _due_at_today(now_et, hour=spec["hour"], minute=spec["minute"])
    deadline = due + timedelta(minutes=spec["grace_minutes"])
    if now_et < deadline:
        print(
            f"[watchdog] WAIT — {job}: 예정 {due.strftime('%Y-%m-%d %H:%M %Z')}, "
            f"유예 종료 {deadline.strftime('%H:%M %Z')}, heartbeat={last}"
        )
        return []

    last_ts = _parse_ts(hb.get(job))
    if last_ts and last_ts >= due.astimezone(timezone.utc):
        print(f"[watchdog] OK — {job} heartbeat={last}")
        return []

    msg = (
        f"daily job missed - {job} 오늘 예정={due.strftime('%Y-%m-%d %H:%M %Z')}, "
        f"유예 {spec['grace_minutes']}분, 마지막={last}"
    )
    print(f"[watchdog] STALE - {msg}")
    notifier.notify("watchdog", msg, {"job": job, "last": last})
    return [{"job": job, "message": msg, "restart_key": _restart_key(job, due)}]


def main(argv) -> int:
    do_restart = "--restart" in argv
    hb = runtime_guard.read_heartbeat()
    now_et = _now_et()
    stale_jobs = _check_alive(hb)
    for spec in _DAILY_JOBS:
        stale_jobs.extend(_check_daily_job(spec, hb, now_et))

    if not stale_jobs:
        return 0
    if not do_restart:
        return 1

    state = _read_state()
    restart_needed = False
    for item in stale_jobs:
        key = item["restart_key"]
        if key and state.get(key):
            print(f"[watchdog] {key} — 오늘 이미 재시작 시도함: {state[key]}")
            continue
        restart_needed = True
        if key:
            state[key] = _now_et().isoformat()

    if restart_needed:
        _restart()
        _write_state(state)
        return 1
    _write_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""
일회성 점검: timing-fix(08:45 ET) 이후 signal_calculation_job(Reddit 수집 잡)이
오늘 정상 발화했는지 확인하고 결과를 stdout + notifier로 보고한다.

배경: 2026-06-13 timing-fix로 수집 잡이 16:30 ET → 08:45 ET로 이동.
새 잡은 안정된 프로세스에서 첫 발화 미경험 상태였고, 워치독이 signal heartbeat를
감시하므로 미발화 시 매일 false-restart 위험이 있다(06-11 사고 동형).
이 스크립트는 22:00 KST 1회 실행으로 첫 발화를 검증하기 위한 것.

사용: venv/bin/python scripts/check_signal_fire.py
"""
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

HEARTBEAT = os.path.join(ROOT, "data", "heartbeat.json")
LOG = os.path.join(ROOT, "data", "trading.log")
WD_STATE = os.path.join(ROOT, "data", "watchdog_restart_state.json")


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001
        return {"__error__": str(e)}


def main() -> int:
    now_et = datetime.now(ET)
    today_et = now_et.date()
    # 오늘 예정 발화 시각(08:45 ET). heartbeat이 이 시각 이후면 오늘 발화한 것.
    due = datetime(today_et.year, today_et.month, today_et.day, 8, 45, tzinfo=ET)

    lines = [f"[signal-fire-check] now={now_et:%Y-%m-%d %H:%M %Z}, due={due:%H:%M %Z}"]

    # (1) heartbeat signal 키 신선도
    hb = _load_json(HEARTBEAT)
    sig_raw = hb.get("signal") if isinstance(hb, dict) else None
    fired = False
    if sig_raw:
        try:
            ts = datetime.fromisoformat(sig_raw)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            ts_et = ts.astimezone(ET)
            fired = ts >= due
            lines.append(f"(1) signal heartbeat = {ts_et:%Y-%m-%d %H:%M %Z} "
                         f"→ {'오늘 발화 ✅' if fired else '구버전(미발화) ❌'}")
        except Exception as e:  # noqa: BLE001
            lines.append(f"(1) signal heartbeat 파싱 실패: {sig_raw!r} ({e})")
    else:
        lines.append("(1) signal heartbeat 키 없음 ❌")

    # (2) 오늘(KST 로그 날짜) 잡 시작/완료/실패 로그
    kst_today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
    started = completed = failed = False
    try:
        with open(LOG, encoding="utf-8", errors="replace") as f:
            for ln in f:
                if not ln.startswith(kst_today):
                    continue
                if "신호 계산 잡 시작" in ln:
                    started = True
                if "신호 계산 잡 완료 (agent)" in ln:
                    completed = True
                if "Reddit 수집 실패" in ln:
                    failed = True
    except Exception as e:  # noqa: BLE001
        lines.append(f"(2) 로그 읽기 실패: {e}")
    lines.append(f"(2) 로그({kst_today}): 시작={started} 완료={completed} 실패={failed}")

    # (3) 워치독이 오늘 signal 부패로 재시작했는지
    wd = _load_json(WD_STATE)
    wd_key = f"signal:{today_et.isoformat()}"
    restarted = isinstance(wd, dict) and wd_key in wd
    lines.append(f"(3) watchdog {wd_key} = {'재시작 발생 ⚠️' if restarted else '없음'}")

    # 판정
    if fired and completed and not failed:
        verdict = "OK"
        head = "✅ signal 잡 첫 발화 성공 — 워치독 false-restart 위험 해소"
    elif failed:
        verdict = "FAIL"
        head = "❌ signal 잡 발화했으나 Reddit 수집 실패 — 로그 확인 필요"
    else:
        verdict = "FAIL"
        head = ("❌ signal 잡 미발화/미완료 — 워치독이 곧 서비스 재시작할 위험 "
                "(원인: 프로세스 부재 / misfire / 수집 행 추정)")

    report = head + "\n" + "\n".join(lines)
    print(report)

    # notifier (Slack webhook 미설정 시 no-op)
    try:
        sys.path.insert(0, ROOT)
        import notifier
        notifier.notify("info" if verdict == "OK" else "error",
                        f"[signal-fire-check] {head}", payload={"detail": lines})
    except Exception as e:  # noqa: BLE001
        print(f"[notify skipped] {e}")

    return 0 if verdict == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())

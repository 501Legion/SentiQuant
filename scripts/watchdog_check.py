#!/usr/bin/env python3
# Design Ref: live-scheduler-deploy §6.3 FR-09 — 외부 워치독 (Approach B)
# systemd timer/cron이 주기 실행. heartbeat가 stale(=hang 추정)이면 알림 + (옵션)서비스 재시작.
# crash는 systemd Restart=always가, hang은 이 워치독이 복구한다.
#
# 감시 대상 2개:
#   alive — 스케줄러가 5분마다 갱신하는 생존 신호. WATCHDOG_STALE_MINUTES(90분) 초과 시 hang.
#   order — 거래일 1회 갱신. WATCHDOG_ORDER_STALE_MINUTES(4일) 초과 시 잡 누락/행.
#   (order를 짧은 한도로 감시하면 하루 대부분이 stale → 15분마다 무한 재시작되므로 금지)
#
# 사용:
#   python scripts/watchdog_check.py            # 검사만 (stale면 exit 1 + 알림)
#   python scripts/watchdog_check.py --restart  # stale면 systemctl restart도 수행
import subprocess
import sys

sys.path.insert(0, __file__.rsplit("scripts", 1)[0] or ".")

import config
import runtime_guard
import notifier

_SERVICE = "auto-stock.service"
# (잡 이름, stale 한도 분) — alive=프로세스 hang, order=일일 잡 누락
_WATCH = [
    ("alive", config.WATCHDOG_STALE_MINUTES),
    ("order", getattr(config, "WATCHDOG_ORDER_STALE_MINUTES", 5760)),
]


def _restart() -> None:
    try:
        subprocess.run(["sudo", "-n", "/usr/bin/systemctl", "restart", _SERVICE], check=True, timeout=30)
        notifier.notify("watchdog", f"{_SERVICE} 재시작 수행")
        print(f"[watchdog] sudo systemctl restart {_SERVICE} 완료")
    except Exception as e:  # noqa: BLE001
        notifier.notify("error", f"워치독 재시작 실패: {e}")
        print(f"[watchdog] 재시작 실패: {e}")


def main(argv) -> int:
    do_restart = "--restart" in argv
    hb = runtime_guard.read_heartbeat()
    stale_jobs = []
    for job, minutes in _WATCH:
        last = hb.get(job, "(기록 없음)")
        if not runtime_guard.heartbeat_stale(job, minutes=minutes, hb=hb):
            print(f"[watchdog] OK — {job} heartbeat={last}")
            continue
        if last == "(기록 없음)":
            # 신규 배포 직후 등 초기 상태 — 재시작해도 의미 없으므로 기록만
            print(f"[watchdog] {job} heartbeat 기록 없음 — 초기 상태로 보고 생략")
            continue
        msg = f"heartbeat stale - {job} 마지막={last}, 한도 {minutes}분 초과 (hang 추정)"
        print(f"[watchdog] STALE - {msg}")
        notifier.notify("watchdog", msg, {"job": job, "last": last})
        stale_jobs.append(job)

    if not stale_jobs:
        return 0
    if do_restart:
        _restart()
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

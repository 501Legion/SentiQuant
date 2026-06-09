#!/usr/bin/env python3
# Design Ref: live-scheduler-deploy §6.3 FR-09 — 외부 워치독 (Approach B)
# systemd timer/cron이 주기 실행. heartbeat가 stale(=hang 추정)이면 알림 + (옵션)서비스 재시작.
# crash는 systemd Restart=always가, hang은 이 워치독이 복구한다.
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

# 워치독이 감시할 잡 — 주문잡이 핵심(거래일에 매일 갱신되어야 함)
_WATCH_JOB = "order"
_SERVICE = "auto-stock"


def main(argv) -> int:
    do_restart = "--restart" in argv
    hb = runtime_guard.read_heartbeat()
    stale = runtime_guard.heartbeat_stale(_WATCH_JOB)
    last = hb.get(_WATCH_JOB, "(기록 없음)")
    if not stale:
        print(f"[watchdog] OK — {_WATCH_JOB} heartbeat={last}")
        return 0

    msg = (f"heartbeat stale - {_WATCH_JOB} 마지막={last}, "
           f"한도 {config.WATCHDOG_STALE_MINUTES}분 초과 (hang 추정)")
    print(f"[watchdog] STALE - {msg}")
    notifier.notify("watchdog", msg, {"job": _WATCH_JOB, "last": last})

    if do_restart:
        try:
            subprocess.run(["systemctl", "restart", _SERVICE], check=True, timeout=30)
            notifier.notify("watchdog", f"{_SERVICE} 재시작 수행")
            print(f"[watchdog] systemctl restart {_SERVICE} 완료")
        except Exception as e:  # noqa: BLE001
            notifier.notify("error", f"워치독 재시작 실패: {e}")
            print(f"[watchdog] 재시작 실패: {e}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

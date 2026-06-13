#!/usr/bin/env python3
"""
멀티데이 손익 백테스트용 OHLCV 사전캐싱 (background 전용).

문제: reddit_backtester._prefetch_ohlcv는 캐시 키가 (ohlcv_start, to)
문자열이라 윈도가 바뀌면 기존 822개 캐시가 전부 미스 → 매 백테스트가
종목당 12s throttle × 200여 종목 = ~40분 → 명령 타임아웃.

해결: 목표 윈도(5/13~6/11) 키로 미스 종목만 미리 받아 CSV 캐시에 적재.
한 번 채우면 이후 rule vs llm·tight vs loose P&L 비교는 전부 오프라인.

진행상황은 data/prefetch_progress.txt에 즉시 기록(타임아웃/유실 방지).
사용: nohup venv/bin/python scripts/prefetch_ohlcv_window.py &
"""
import os
import sys
import time
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config
from reddit_collector import RedditCollector
from backtester import _snapshot_path, _get_ohlcv_snapshot

FROM, TO = "2026-05-13", "2026-06-11"
OHLCV_START = (datetime.strptime(FROM, "%Y-%m-%d") - timedelta(days=100)).strftime("%Y-%m-%d")
PROGRESS = os.path.join(ROOT, "data", "prefetch_progress.txt")


def _log(line=""):
    print(line, flush=True)
    with open(PROGRESS, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now().strftime('%H:%M:%S')} {line}\n")


def main():
    d0 = datetime.strptime(FROM, "%Y-%m-%d")
    d1 = datetime.strptime(TO, "%Y-%m-%d")
    dates = [(d0 + timedelta(days=i)).strftime("%Y-%m-%d") for i in range((d1 - d0).days + 1)]
    syms = set()
    for d in dates:
        try:
            syms |= set(RedditCollector.load_posts(d))
        except Exception:
            pass

    miss = [s for s in sorted(syms)
            if not os.path.exists(_snapshot_path("ohlcv", f"{s}_{OHLCV_START}_{TO}.csv"))]
    _log(f"=== prefetch 시작 window={OHLCV_START}~{TO} 전체{len(syms)} 미스{len(miss)} "
         f"예상 {round(len(miss)*config.REDDIT_BACKTEST_FETCH_THROTTLE/60,1)}분 ===")

    ok = fail = 0
    for i, s in enumerate(miss, 1):
        try:
            df = _get_ohlcv_snapshot(s, OHLCV_START, TO)
            if df is not None and not df.empty:
                ok += 1
                _log(f"[{i}/{len(miss)}] {s} OK rows={len(df)}")
            else:
                fail += 1
                _log(f"[{i}/{len(miss)}] {s} EMPTY")
        except Exception as e:  # noqa: BLE001
            fail += 1
            _log(f"[{i}/{len(miss)}] {s} FAIL {e}")
        time.sleep(config.REDDIT_BACKTEST_FETCH_THROTTLE)

    _log(f"=== 완료 OK={ok} FAIL={fail} ===")


if __name__ == "__main__":
    main()

#!/usr/bin/env python
"""community-opinion-agent 회귀 검출 스크립트 (Plan NFR-06 / Design §9).

목적: "모든 신규 필터 OFF + --sizing equal" 백테스트 결과가 baseline과 동일한지 검증.
신규 필터(universe/cost/source/snapshot/memory/reflection)를 전부 OFF로 강제한 뒤
equal sizing 백테스트를 돌려 baseline json과 trade(entry/exit/pnl)·final_equity·
total_trades를 비교한다. 차이가 있으면 stderr 출력 + exit code 1.

사용법:
  # 1) baseline 생성 (현재 코드가 정상일 때 1회)
  python scripts/regression_check_reddit.py --from 2026-05-17 --to 2026-05-25 --update

  # 2) 이후 회귀 검사 (CI/커밋 전)
  python scripts/regression_check_reddit.py --from 2026-05-17 --to 2026-05-25
  echo $?   # 0=통과, 1=회귀
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config

_DEFAULT_BASELINE = os.path.join(_ROOT, "data", "regression", "reddit_equal_baseline.json")

# 회귀 검사 시 OFF로 강제할 신규 필터 플래그
_FILTER_FLAGS = [
    "COMMUNITY_ENABLE_UNIVERSE_FILTER",
    "COMMUNITY_ENABLE_COST_AWARE_FILTER",
    "COMMUNITY_ENABLE_SOURCE_QUALITY_FILTER",
    "COMMUNITY_ENABLE_TICKER_AMBIGUITY_FILTER",
    "COMMUNITY_ENABLE_DAILY_OPINION_SNAPSHOT",
    "COMMUNITY_MEMORY_ENABLED",
    "COMMUNITY_REFLECTION_ENABLED",
    "COMMUNITY_LLM_ROUTER_ENABLED",
]


def _disable_new_filters() -> dict:
    """신규 필터를 전부 OFF로 강제하고 원래 값을 반환(복원용)."""
    orig = {}
    for flag in _FILTER_FLAGS:
        orig[flag] = getattr(config, flag, None)
        setattr(config, flag, False)
    return orig


def _restore(orig: dict) -> None:
    for flag, val in orig.items():
        if val is not None:
            setattr(config, flag, val)


def _run_equal(from_date: str, to_date: str, model: str) -> dict:
    """equal sizing 백테스트 → 비교용 직렬화 dict."""
    from reddit_backtester import RedditReplayBacktester

    r = RedditReplayBacktester(
        model=model, ranking="sentiment", sizing="equal",
        from_date=from_date, to_date=to_date, universe_mode="community_liquid",
    ).run()
    trades = [
        {
            "symbol": t.symbol, "entry_date": t.entry_date, "exit_date": t.exit_date,
            "entry_price": round(t.entry_price, 4), "exit_price": round(t.exit_price, 4),
            "shares": t.shares, "dollar_pnl": round(t.dollar_pnl, 2),
            "exit_reason": t.exit_reason,
        }
        for t in r.trades
    ]
    return {
        "from": from_date, "to": to_date, "model": model,
        "final_equity": round(r.final_equity, 2),
        "total_trades": r.total_trades,
        "trades": trades,
    }


def _compare(baseline: dict, current: dict) -> list[str]:
    """차이 목록 반환 (빈 리스트면 회귀 없음)."""
    diffs: list[str] = []
    if baseline.get("total_trades") != current.get("total_trades"):
        diffs.append(
            f"total_trades: baseline={baseline.get('total_trades')}"
            f" != current={current.get('total_trades')}")
    if abs(baseline.get("final_equity", 0) - current.get("final_equity", 0)) > 1e-6:
        diffs.append(
            f"final_equity: baseline={baseline.get('final_equity')}"
            f" != current={current.get('final_equity')}")
    bt, ct = baseline.get("trades", []), current.get("trades", [])
    if len(bt) != len(ct):
        diffs.append(f"trade count: baseline={len(bt)} != current={len(ct)}")
    for i, (b, c) in enumerate(zip(bt, ct)):
        if b != c:
            diffs.append(f"trade[{i}] 불일치:\n    baseline={b}\n    current ={c}")
    return diffs


def main() -> int:
    logging.disable(logging.CRITICAL)
    ap = argparse.ArgumentParser(description="Reddit equal sizing 회귀 검사")
    ap.add_argument("--from", dest="from_date", required=True, metavar="YYYY-MM-DD")
    ap.add_argument("--to", dest="to_date", required=True, metavar="YYYY-MM-DD")
    ap.add_argument("--model", default="finbert")
    ap.add_argument("--baseline", default=_DEFAULT_BASELINE)
    ap.add_argument("--update", action="store_true", help="현재 결과를 baseline으로 저장")
    args = ap.parse_args()

    orig = _disable_new_filters()
    try:
        current = _run_equal(args.from_date, args.to_date, args.model)
    finally:
        _restore(orig)

    if args.update or not os.path.exists(args.baseline):
        os.makedirs(os.path.dirname(args.baseline), exist_ok=True)
        with open(args.baseline, "w", encoding="utf-8") as f:
            json.dump(current, f, ensure_ascii=False, indent=2)
        action = "갱신" if args.update else "생성(최초)"
        print(f"[regression] baseline {action}: {args.baseline}")
        print(f"[regression] total_trades={current['total_trades']}"
              f" final_equity={current['final_equity']}")
        return 0

    with open(args.baseline, "r", encoding="utf-8") as f:
        baseline = json.load(f)

    diffs = _compare(baseline, current)
    if diffs:
        print("[regression] ❌ equal sizing 회귀 감지 (신규 필터 OFF 상태):", file=sys.stderr)
        for d in diffs:
            print(f"  - {d}", file=sys.stderr)
        return 1

    print(f"[regression] ✅ equal 회귀 없음 (trades={current['total_trades']},"
          f" final_equity={current['final_equity']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

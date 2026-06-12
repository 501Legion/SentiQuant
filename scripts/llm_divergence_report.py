#!/usr/bin/env python
"""llm-p1 ① — LLM 라우터 분기율/기여도 리포트.

라이브 결정 로그(decisions.jsonl)에서 rule_action vs llm_action을 비교해
LLM이 rule과 다르게 판단한 비율과, 분기 케이스의 이후 수익률(누가 옳았나)을 집계한다.
이 수치가 LLM 라우터에 대한 추가 투자(프롬프트 보강·권한 확장) 여부의 근거가 된다.

사용법:
  python scripts/llm_divergence_report.py                 # 라이브 로그 전체
  python scripts/llm_divergence_report.py --since 2026-06-13
  python scripts/llm_divergence_report.py --log path/to/decisions.jsonl
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config

_FWD_DAYS = (1, 3, 7)


def _load_records(path: str, since: str | None) -> list[dict]:
    records = []
    if not os.path.exists(path):
        return records
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if since and r.get("date", "") < since:
                continue
            records.append(r)
    return records


def _latest_ohlcv(symbol: str):
    """캐시된 OHLCV 스냅샷 중 end date가 가장 최신인 파일 → DataFrame | None."""
    import pandas as pd

    paths = glob.glob(os.path.join(
        config.BACKTEST_SNAPSHOT_DIR, "v2", "ohlcv", f"{symbol}_*.csv"))
    if not paths:
        return None
    latest = max(paths, key=lambda p: os.path.basename(p)[:-4].rsplit("_", 1)[-1])
    try:
        df = pd.read_csv(latest)
        return df if not df.empty and "date" in df.columns else None
    except Exception:  # noqa: BLE001
        return None


def _forward_returns(symbol: str, date: str) -> dict[int, float] | None:
    """date 종가 기준 1/3/7 거래일 수익률(%). 데이터 부족 시 가능한 것만, 전무하면 None."""
    df = _latest_ohlcv(symbol)
    if df is None:
        return None
    rows = df[df["date"] <= date].reset_index(drop=True)
    if rows.empty or rows.iloc[-1]["date"] != date:
        return None
    base_idx = len(rows) - 1
    full = df.reset_index(drop=True)
    base = float(full.iloc[base_idx]["close"])
    if base <= 0:
        return None
    out = {}
    for n in _FWD_DAYS:
        if base_idx + n < len(full):
            out[n] = round((float(full.iloc[base_idx + n]["close"]) - base) / base * 100, 2)
    return out or None


def main() -> int:
    ap = argparse.ArgumentParser(description="LLM 라우터 분기율 리포트")
    ap.add_argument("--log", default=config.COMMUNITY_LIVE_DECISIONS_FILE)
    ap.add_argument("--since", default=None, metavar="YYYY-MM-DD")
    args = ap.parse_args()

    records = _load_records(args.log, args.since)
    if not records:
        print(f"결정 로그 없음: {args.log}")
        return 0

    llm_on = [r for r in records if r.get("llm_enabled")]
    answered = [r for r in llm_on if r.get("llm_action")]
    diverged = [r for r in answered if r["llm_action"] != r.get("rule_action")]

    print(f"# LLM 분기율 리포트 — {args.log}")
    print(f"기간: {records[0].get('date')} ~ {records[-1].get('date')}")
    print(f"전체 결정: {len(records)} | LLM 활성: {len(llm_on)}"
          f" | LLM 유효응답: {len(answered)} | 분기: {len(diverged)}")
    if answered:
        print(f"분기율: {len(diverged)/len(answered)*100:.1f}%"
              f" (유효응답 {len(answered)}건 기준)")
    fallback = len(llm_on) - len(answered)
    if fallback:
        print(f"LLM 무응답/폴백: {fallback}건 (rule-based 유지)")

    final_dist = Counter(r.get("final_action") for r in records)
    print(f"\n최종 액션 분포: {dict(final_dist)}")

    if not diverged:
        print("\n분기 케이스 없음 — LLM이 rule 판단을 전부 따름.")
        return 0

    print(f"\n## 분기 케이스 {len(diverged)}건 (rule → llm, 최종, 이후 수익률)")
    print(f"{'date':<12} {'symbol':<7} {'rule':<7} {'llm':<7} {'final':<7}"
          + "".join(f" {'fwd'+str(n)+'d%':>8}" for n in _FWD_DAYS))
    for r in diverged:
        fwd = _forward_returns(r.get("symbol", ""), r.get("date", "")) or {}
        fwd_str = "".join(
            f" {fwd[n]:>+8.2f}" if n in fwd else f" {'n/a':>8}" for n in _FWD_DAYS)
        print(f"{r.get('date',''):<12} {r.get('symbol',''):<7}"
              f" {r.get('rule_action',''):<7} {r.get('llm_action',''):<7}"
              f" {r.get('final_action',''):<7}{fwd_str}")

    # 분기 케이스 판정 힌트: rule이 BUY인데 LLM이 보류/축소했고 이후 하락이면 LLM 승
    print("\n해석: rule=BUY & llm=HOLD/SKIP/REDUCE 에서 fwd가 음수면 LLM이 옳았던 케이스,"
          " 양수면 rule이 옳았던 케이스.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

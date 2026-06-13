#!/usr/bin/env python3
"""
완화된 깔때기에서 LLM 라우터가 실효를 내는지 결정-레벨로 측정 (단일일 06-13, 완전 캐시).

멀티데이 백테스트는 Polygon 무료플랜 OHLCV 수집 병목으로 비현실적 → 06-13 하루
(OHLCV 캐시 완비)에 run_live를 LLM ON/OFF로 돌려, 깔때기를 풀었을 때 룰이 내는
BUY 후보를 LLM이 몇 개나 veto/축소하는지 직접 센다.

- 깔때기 완화(score 52·consensus 1.3·neutral 0.95·min_mentions 2)는 양쪽 공통.
- dry_run + temp 상태 격리 → 라이브 무침습.

사용: venv/bin/python scripts/bt_loose_day_llm.py
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config
import community_live
from reddit_collector import RedditCollector

DATE = "2026-06-13"
FILE_KEYS = {
    "mention_history": "MENTION_HISTORY_FILE", "score_history": "SCORE_HISTORY_FILE",
    "position_scores": "POSITION_SCORES_FILE", "daily_snapshots": "COMMUNITY_DAILY_SNAPSHOT_FILE",
    "decisions": "COMMUNITY_LIVE_DECISIONS_FILE", "run_summaries": "COMMUNITY_LIVE_RUN_SUMMARIES_FILE",
}
DIR_KEYS = {"reports_dir": "COMMUNITY_LIVE_REPORTS_DIR", "memory_dir": "COMMUNITY_MEMORY_DIR",
            "reddit_data_dir": "REDDIT_DATA_DIR"}


def loosen():
    config.WSB_OPINION_SCORE_LOW = 52.0
    config.COMMUNITY_OPINION_SCORE_LOW = 52.0
    config.COMMUNITY_CONSENSUS_MIN_RATIO = 1.3
    config.WSB_NEUTRAL_RATIO_MAX = 0.96
    config.COMMUNITY_NEUTRAL_RATIO_MAX = 0.95
    config.WSB_MIN_DIRECTIONAL_MENTIONS = 2


def _sandbox(tag):
    base = tempfile.mkdtemp(prefix=f"bt_day_{tag}_")
    ov = {}
    for k, attr in FILE_KEYS.items():
        live = getattr(config, attr)
        dst = os.path.join(base, os.path.basename(live))
        if os.path.exists(live):
            shutil.copy2(live, dst)
        ov[k] = dst
    for k, attr in DIR_KEYS.items():
        live = getattr(config, attr)
        dst = os.path.join(base, k)
        if os.path.isdir(live):
            shutil.copytree(live, dst)
        else:
            os.makedirs(dst, exist_ok=True)
        ov[k] = dst
    return ov


def run_arm(llm, posts, ohlcv):
    config.COMMUNITY_LLM_ROUTER_ENABLED = llm
    ov = _sandbox("llm" if llm else "rule")
    community_live.run_live(date=DATE, dry_run=True, llm_router=llm,
                            posts_by_symbol=posts, ohlcv_full=ohlcv, state_overrides=ov)
    recs = {}
    with open(ov["decisions"], encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("date") == DATE:
                recs[r["symbol"]] = r   # 최신
    return recs


def main():
    loosen()
    posts = RedditCollector.load_posts(DATE)
    print(f"posts={len(posts)}; 깔때기 완화 score52/cons1.3/neut0.95/min2; OHLCV 캐시 빌드...")
    ohlcv = community_live._fetch_ohlcv_full(set(posts), DATE)

    out = {}
    for llm in (False, True):
        recs = run_arm(llm, posts, ohlcv)
        rule_buy = [s for s, r in recs.items() if r.get("rule_action") == "BUY"]
        final_buy = [s for s, r in recs.items() if r.get("final_action") == "BUY"]
        out[llm] = (recs, rule_buy, final_buy)
        tag = "LLM ON" if llm else "LLM OFF(rule-only)"
        print(f"\n===== {tag} =====")
        print(f"  라우터 도달 후보: {len(recs)}개")
        print(f"  rule_action=BUY : {len(rule_buy)}개  {sorted(rule_buy)}")
        print(f"  final_action=BUY: {len(final_buy)}개  {sorted(final_buy)}")

    rule_recs, rule_buy_off, final_off = out[False]
    llm_recs, rule_buy_on, final_on = out[True]
    vetoed = sorted(set(rule_buy_on) - set(final_on))   # 룰은 BUY인데 LLM이 최종 BUY 아님
    print("\n================ LLM 실효 (06-13, 완화 깔때기) ================")
    print(f"  룰이 낸 BUY 후보: {len(rule_buy_on)}개")
    print(f"  LLM OFF 최종 매수: {len(final_off)}개")
    print(f"  LLM ON  최종 매수: {len(final_on)}개")
    print(f"  LLM이 막은 종목(veto→HOLD/SKIP): {vetoed or '없음'}")
    for s in vetoed:
        r = llm_recs[s]
        rsn = (r.get("reasoning") or "")[:160]
        print(f"    - {s}: final={r.get('final_action')} | {rsn}")
    if len(final_on) != len(final_off):
        print(f"  → ✅ LLM 실효: 완화 시 룰 {len(final_off)}건 매수 → LLM이 {len(final_off)-len(final_on)}건 차단")
    else:
        print("  → ❌ 동일 (LLM 무실효)")


if __name__ == "__main__":
    main()

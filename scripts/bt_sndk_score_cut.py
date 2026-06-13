#!/usr/bin/env python3
"""
일회성 A/B 백테스트: 여론점수 진입컷(COMMUNITY_OPINION_SCORE_LOW)을 57 → 56으로
낮추면 2026-06-13 SNDK가 실제로 매수(final_action=BUY)됐을지 확인.

- 동일 posts/OHLCV/상태 사본을 양쪽 arm에 주입 → 임계값만 차이.
- dry_run=True, 모든 상태 파일 temp 격리 → 라이브 무침습.
- llm_router=True → 룰뿐 아니라 LLM 최종 판단까지 재현(SNDK는 llm_assisted였음).

사용: venv/bin/python scripts/bt_sndk_score_cut.py
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
SYMBOL = "SNDK"

# state_overrides 키 → config 속성 (community_live._RUN_STATE_PATH_KEYS와 동일)
FILE_KEYS = {
    "mention_history": "MENTION_HISTORY_FILE",
    "score_history": "SCORE_HISTORY_FILE",
    "position_scores": "POSITION_SCORES_FILE",
    "daily_snapshots": "COMMUNITY_DAILY_SNAPSHOT_FILE",
    "decisions": "COMMUNITY_LIVE_DECISIONS_FILE",
    "run_summaries": "COMMUNITY_LIVE_RUN_SUMMARIES_FILE",
}
DIR_KEYS = {
    "reports_dir": "COMMUNITY_LIVE_REPORTS_DIR",
    "memory_dir": "COMMUNITY_MEMORY_DIR",
    "reddit_data_dir": "REDDIT_DATA_DIR",
}


def _make_sandbox(tag: str) -> dict:
    """라이브 상태 파일/디렉터리를 temp로 복사하고 state_overrides dict 반환."""
    base = tempfile.mkdtemp(prefix=f"bt_sndk_{tag}_")
    overrides = {}
    for key, attr in FILE_KEYS.items():
        live = getattr(config, attr)
        dst = os.path.join(base, os.path.basename(live))
        if os.path.exists(live):
            shutil.copy2(live, dst)
        overrides[key] = dst
    for key, attr in DIR_KEYS.items():
        live = getattr(config, attr)
        dst = os.path.join(base, key)
        if os.path.isdir(live):
            shutil.copytree(live, dst)
        else:
            os.makedirs(dst, exist_ok=True)
        overrides[key] = dst
    return overrides


def _run_arm(threshold: float, posts, ohlcv, llm: bool) -> dict:
    config.COMMUNITY_OPINION_SCORE_LOW = threshold   # 라우터가 읽는 값(decision_router:265)
    config.WSB_OPINION_SCORE_LOW = threshold
    # 진짜 LLM 마스터 스위치 — DecisionRouter는 (인자 OR config플래그)라 config로 꺼야 OFF됨
    config.COMMUNITY_LLM_ROUTER_ENABLED = llm
    overrides = _make_sandbox(f"{int(threshold)}_{'llm' if llm else 'rule'}")
    community_live.run_live(
        date=DATE, dry_run=True, llm_router=llm,
        posts_by_symbol=posts, ohlcv_full=ohlcv,
        state_overrides=overrides,
    )
    # temp decisions 파일에서 SNDK 레코드 추출
    rec = None
    with open(overrides["decisions"], encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("symbol") == SYMBOL and r.get("date") == DATE:
                rec = r  # 마지막(최신) 레코드
    return rec


def main() -> int:
    posts = RedditCollector.load_posts(DATE)
    if not posts:
        print(f"[ERR] {DATE} posts 없음")
        return 2
    print(f"posts 종목수={len(posts)}; OHLCV 캐시 빌드 중...")
    ohlcv = community_live._fetch_ohlcv_full(set(posts), DATE)

    LLM = os.environ.get("BT_LLM", "0") == "1"
    label = "LLM 라우터 ON" if LLM else "LLM 라우터 OFF (rule-only)"
    print(f"\n######## {label} ########")
    results = {}
    for thr in (57.0, 56.0):
        print(f"\n===== arm: score_cut={thr} ({label}) =====")
        rec = _run_arm(thr, posts, ohlcv, llm=LLM)
        results[thr] = rec
        if rec is None:
            print(f"  SNDK 레코드 없음 (후보 탈락?)")
            continue
        print(f"  rule_action  = {rec.get('rule_action')}")
        print(f"  llm_action   = {rec.get('llm_action')}")
        print(f"  final_action = {rec.get('final_action')}")
        print(f"  size_factor  = {rec.get('size_factor')}  confidence={rec.get('confidence')}")
        print(f"  reason_codes = {rec.get('reason_codes')}")
        print(f"  reasoning    = {rec.get('reasoning')}")

    a, b = results.get(57.0), results.get(56.0)
    print("\n================ 결론 ================")
    fa = (a or {}).get("final_action")
    fb = (b or {}).get("final_action")
    print(f"[{label}] score_cut 57 → SNDK final = {fa}")
    print(f"[{label}] score_cut 56 → SNDK final = {fb}")
    if fb == "BUY":
        print("✅ 컷 56으로 낮추면 SNDK 매수됨")
    elif (a or {}).get("rule_action") != (b or {}).get("rule_action"):
        print(f"△ 룰은 {(a or {}).get('rule_action')}→{(b or {}).get('rule_action')}로 바뀌나, "
              f"최종 {fb}로 막힘 (매수 안 됨)")
    else:
        print(f"❌ 컷을 낮춰도 SNDK 매수 안 됨 (final={fb})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

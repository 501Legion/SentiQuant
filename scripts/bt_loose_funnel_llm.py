#!/usr/bin/env python3
"""
깔때기 완화 후 LLM 라우터가 실효(거래·수익 변화)를 내는지 A/B 백테스트.

가설: 현재 룰이 거의 다 SKIP이라 LLM이 브레이크 걸 대상이 없어 영향 ≈0.
깔때기를 풀어 룰이 BUY 후보를 많이 내면, LLM(보수 veto/축소)이 실제로 거래를 바꾸는가?

- 동일 깔때기 완화값으로 RULE-only vs LLM-assisted 2 arm 비교(LLM만 차이).
- 각 arm은 temp 메모리/스냅샷/이력으로 격리 → 라이브 무침습(백테스터가
  self._memory·daily_snapshot에 write하므로 필수).
- DecisionRouter는 (인자 OR config플래그)라 rule-only는 config 플래그도 꺼야 함.

사용: venv/bin/python scripts/bt_loose_funnel_llm.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config
from reddit_backtester import RedditReplayBacktester

FROM, TO = "2026-05-13", "2026-06-11"
MODEL, RANKING, SIZING, UNIV = "finbert-wsb", "sentiment", "opinion_trend", "community_liquid"
OUT = os.path.join(ROOT, "data", "bt_loose_result.txt")


def _w(line=""):
    """결과를 stdout+파일에 즉시 기록 (버퍼링/타임아웃 시 유실 방지)."""
    print(line, flush=True)
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def loosen_funnel():
    """룰이 BUY 후보를 많이 내도록 진입 게이트 완화 (양쪽 arm 공통).
    min_mentions=2로 유지(=1이면 obscure 티커까지 OHLCV fetch 폭증 → 타임아웃)."""
    config.WSB_OPINION_SCORE_LOW = 52.0
    config.COMMUNITY_OPINION_SCORE_LOW = 52.0
    config.COMMUNITY_CONSENSUS_MIN_RATIO = 1.3
    config.WSB_NEUTRAL_RATIO_MAX = 0.96
    config.COMMUNITY_NEUTRAL_RATIO_MAX = 0.95
    config.WSB_MIN_DIRECTIONAL_MENTIONS = 2
    config.POLYGON_REQUEST_DELAY = 0   # throttle 제거(캐시 위주 → rate-limit 부담 적음)


def _isolate_state():
    """라이브 메모리/스냅샷/이력을 temp로 격리 (arm별 독립·결정성)."""
    base = tempfile.mkdtemp(prefix="bt_loose_")
    config.COMMUNITY_MEMORY_DIR = os.path.join(base, "memory")
    os.makedirs(config.COMMUNITY_MEMORY_DIR, exist_ok=True)
    config.COMMUNITY_DAILY_SNAPSHOT_FILE = os.path.join(base, "daily_snapshots.jsonl")
    config.SCORE_HISTORY_FILE = os.path.join(base, "score_history.json")
    config.MENTION_HISTORY_FILE = os.path.join(base, "mention_history.json")
    config.POSITION_SCORES_FILE = os.path.join(base, "position_scores.json")


def run_arm(llm: bool):
    config.COMMUNITY_LLM_ROUTER_ENABLED = llm   # 진짜 마스터 스위치
    _isolate_state()
    r = RedditReplayBacktester(
        model=MODEL, ranking=RANKING, sizing=SIZING,
        from_date=FROM, to_date=TO, universe_mode=UNIV, llm_router=llm,
    ).run()
    return r


def _summ(tag, r):
    _w(f"\n===== {tag} =====")
    _w(f"  net_return   = {r.net_return_pct:+.2f}%   gross={r.gross_return_pct:+.2f}%")
    _w(f"  trades       = {r.total_trades}   win_rate={r.win_rate:.0%}"
       f"   PF={r.profit_factor:.2f}   MDD={r.max_drawdown:.1f}%")
    _w(f"  decisions    = buy {r.buy_decisions_logged} / hold {r.hold_decisions_logged}"
       f" / skip {r.skip_decisions_logged}   llm_assisted={r.llm_decisions_logged}")
    _w(f"  router_dist  = {r.router_action_dist}")
    tr = []
    for t in (r.trades or []):
        sym = getattr(t, "symbol", "?")
        ret = getattr(t, "net_return_pct", getattr(t, "return_pct", 0.0))
        ed = getattr(t, "entry_date", "")
        tr.append((sym, ed, ret))
    if tr:
        _w("  체결 거래:")
        for sym, ed, ret in tr:
            mark = " 👈SNDK" if sym == "SNDK" else ""
            _w(f"    {sym:<6} entry={ed}  ret={ret:+.1f}%{mark}")
    return {s for s, _, _ in tr}


def main() -> int:
    open(OUT, "w").close()   # 결과 파일 초기화
    loosen_funnel()
    _w("깔때기 완화: score_low=52, consensus_min=1.3, neutral_max=0.95/0.96, min_mentions=2")
    _w(f"기간 {FROM}~{TO}, {MODEL}/{RANKING}/{SIZING}/{UNIV}")

    _w("\n[1/2] RULE arm 실행 중...")
    rule = run_arm(False)
    rule_syms = _summ("RULE-only (LLM OFF)", rule)

    _w("\n[2/2] LLM arm 실행 중...")
    llm = run_arm(True)
    llm_syms = _summ("LLM-assisted (LLM ON)", llm)

    _w("\n================ 결론: LLM 실효 ================")
    _w(f"  net_return : rule {rule.net_return_pct:+.2f}%  vs  llm {llm.net_return_pct:+.2f}%"
       f"   (Δ {llm.net_return_pct - rule.net_return_pct:+.2f}%p)")
    _w(f"  trades     : rule {rule.total_trades}  vs  llm {llm.total_trades}")
    only_rule = rule_syms - llm_syms
    only_llm = llm_syms - rule_syms
    _w(f"  rule만 매수(=LLM이 막은 종목): {sorted(only_rule) or '없음'}")
    _w(f"  llm만 매수: {sorted(only_llm) or '없음'}")
    if rule.total_trades == llm.total_trades and abs(rule.net_return_pct - llm.net_return_pct) < 0.01:
        _w("  → ❌ 깔때기 완화해도 LLM 무실효 (거래·수익 동일)")
    else:
        _w("  → ✅ LLM 실효 발생 (거래/수익이 달라짐)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

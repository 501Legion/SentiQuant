#!/usr/bin/env python3
"""
Stage 1 — 모멘텀 가설 손익 매트릭스 백테스트.

질문: "모멘텀을 타는 게(깔때기 완화 + 브레이크 없음) 보수 필터보다 손익이 나은가?"
이건 Stage 2 코드변경(프롬프트 분리·downsize 가드레일) 없이도 검증 가능 —
현재 존재하는 두 축의 2×2면 충분하다:

  funnel ∈ {TIGHT(=production config 그대로), LOOSE(score52/cons1.3/neut0.95/min2)}
  llm    ∈ {OFF(=룰 그대로, 브레이크 없음), ON(=현재 veto 브레이크)}

해석:
  LOOSE×OFF = 모멘텀을 그대로 탄다(룰 BUY, veto 없음)  ← "모멘텀" arm
  LOOSE×ON  = 모멘텀을 LLM이 brake               ← "브레이크-온-모멘텀"
  TIGHT×ON  ≈ 현재 운영(production)
  TIGHT×OFF = 보수 룰 단독

⚠ 비대칭 함정: 모멘텀 알파는 오른쪽 꼬리(소수 초대박)다. 짧은 윈도엔 SNDK급
   사건이 없을 수 있어 평균수익만 보면 오해한다. 그래서 평균뿐 아니라
   **최대 단일승자·상하위 거래·MDD·SNDK 포착여부**(=분포)로 판단한다.

전제: scripts/prefetch_ohlcv_window.py로 5/13~6/11 OHLCV 캐시 선적재 완료.
      (미적재면 OHLCV throttle로 타임아웃)

사용: venv/bin/python scripts/bt_matrix_momentum.py
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
OUT = os.path.join(ROOT, "data", "bt_matrix_result.txt")

# production(=TIGHT) 진입 게이트 값을 import 시점에 스냅샷 (출력에 명시)
TIGHT = dict(
    score_low=config.WSB_OPINION_SCORE_LOW,
    comm_score_low=config.COMMUNITY_OPINION_SCORE_LOW,
    consensus=config.COMMUNITY_CONSENSUS_MIN_RATIO,
    neutral_wsb=config.WSB_NEUTRAL_RATIO_MAX,
    neutral_comm=config.COMMUNITY_NEUTRAL_RATIO_MAX,
    min_mentions=config.WSB_MIN_DIRECTIONAL_MENTIONS,
)


def _w(line=""):
    print(line, flush=True)
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def loosen_funnel():
    """룰이 BUY 후보를 많이 내도록 진입 게이트 완화 (bt_loose_funnel_llm과 동일값)."""
    config.WSB_OPINION_SCORE_LOW = 52.0
    config.COMMUNITY_OPINION_SCORE_LOW = 52.0
    config.COMMUNITY_CONSENSUS_MIN_RATIO = 1.3
    config.WSB_NEUTRAL_RATIO_MAX = 0.96
    config.COMMUNITY_NEUTRAL_RATIO_MAX = 0.95
    config.WSB_MIN_DIRECTIONAL_MENTIONS = 2


def restore_tight():
    config.WSB_OPINION_SCORE_LOW = TIGHT["score_low"]
    config.COMMUNITY_OPINION_SCORE_LOW = TIGHT["comm_score_low"]
    config.COMMUNITY_CONSENSUS_MIN_RATIO = TIGHT["consensus"]
    config.WSB_NEUTRAL_RATIO_MAX = TIGHT["neutral_wsb"]
    config.COMMUNITY_NEUTRAL_RATIO_MAX = TIGHT["neutral_comm"]
    config.WSB_MIN_DIRECTIONAL_MENTIONS = TIGHT["min_mentions"]


def _isolate_state():
    base = tempfile.mkdtemp(prefix="bt_matrix_")
    config.COMMUNITY_MEMORY_DIR = os.path.join(base, "memory")
    os.makedirs(config.COMMUNITY_MEMORY_DIR, exist_ok=True)
    config.COMMUNITY_DAILY_SNAPSHOT_FILE = os.path.join(base, "daily_snapshots.jsonl")
    config.SCORE_HISTORY_FILE = os.path.join(base, "score_history.json")
    config.MENTION_HISTORY_FILE = os.path.join(base, "mention_history.json")
    config.POSITION_SCORES_FILE = os.path.join(base, "position_scores.json")


def run_arm(loose: bool, llm: bool):
    if loose:
        loosen_funnel()
    else:
        restore_tight()
    config.POLYGON_REQUEST_DELAY = 0          # 캐시 위주 → throttle 불필요
    config.COMMUNITY_LLM_ROUTER_ENABLED = llm  # 진짜 마스터 스위치
    _isolate_state()
    return RedditReplayBacktester(
        model=MODEL, ranking=RANKING, sizing=SIZING,
        from_date=FROM, to_date=TO, universe_mode=UNIV, llm_router=llm,
    ).run()


def _trades(r):
    out = []
    for t in (r.trades or []):
        out.append((
            getattr(t, "symbol", "?"),
            getattr(t, "entry_date", ""),
            getattr(t, "net_return_pct", getattr(t, "return_pct", 0.0)),
        ))
    return out


def _summ(tag, r):
    tr = _trades(r)
    rets = sorted((ret for _, _, ret in tr), reverse=True)
    syms = {s for s, _, _ in tr}
    sndk = [(s, ed, ret) for s, ed, ret in tr if s == "SNDK"]
    _w(f"\n===== {tag} =====")
    _w(f"  net_return = {r.net_return_pct:+.2f}%  gross={r.gross_return_pct:+.2f}%")
    _w(f"  trades={r.total_trades}  win_rate={r.win_rate:.0%}  PF={r.profit_factor:.2f}"
       f"  MDD={r.max_drawdown:.1f}%")
    _w(f"  decisions: buy {r.buy_decisions_logged}/hold {r.hold_decisions_logged}"
       f"/skip {r.skip_decisions_logged}  llm_assisted={r.llm_decisions_logged}")
    _w(f"  router_dist={r.router_action_dist}")
    if rets:
        _w(f"  최대 단일승자={rets[0]:+.1f}%  최대 단일패자={rets[-1]:+.1f}%")
        _w(f"  상위3={['%+.1f%%' % x for x in rets[:3]]}  하위3={['%+.1f%%' % x for x in rets[-3:]]}")
    _w(f"  SNDK 포착: {'예 → ' + ', '.join('%s ret=%+.1f%%' % (s, ret) for s, ed, ret in sndk) if sndk else '아니오'}")
    return syms, (rets[0] if rets else 0.0)


def main() -> int:
    open(OUT, "w").close()
    _w(f"Stage1 모멘텀 매트릭스 — 기간 {FROM}~{TO}  {MODEL}/{RANKING}/{SIZING}/{UNIV}")
    _w(f"TIGHT(production) = score_low={TIGHT['score_low']} comm={TIGHT['comm_score_low']}"
       f" cons={TIGHT['consensus']} neut={TIGHT['neutral_wsb']}/{TIGHT['neutral_comm']}"
       f" min_mentions={TIGHT['min_mentions']}")
    _w("LOOSE = score52/cons1.3/neut0.96/0.95/min2")

    arms = [
        ("TIGHT × LLM-OFF", False, False),
        ("TIGHT × LLM-ON (≈production)", False, True),
        ("LOOSE × LLM-OFF (모멘텀)", True, False),
        ("LOOSE × LLM-ON (브레이크-온-모멘텀)", True, True),
    ]
    results = {}
    for i, (tag, loose, llm) in enumerate(arms, 1):
        _w(f"\n[{i}/{len(arms)}] {tag} 실행 중...")
        r = run_arm(loose, llm)
        syms, top = _summ(tag, r)
        results[tag] = (r, syms, top)

    _w("\n================ 매트릭스 요약 ================")
    _w(f"  {'arm':<34}{'net%':>8}{'trades':>8}{'win':>6}{'MDD%':>8}{'top%':>8}  SNDK")
    for tag, (r, syms, top) in results.items():
        _w(f"  {tag:<34}{r.net_return_pct:>+7.2f}{r.total_trades:>8}"
           f"{r.win_rate:>5.0%}{r.max_drawdown:>+7.1f}{top:>+7.1f}  "
           f"{'✓' if 'SNDK' in syms else '·'}")

    _w("\n  해석 가이드:")
    _w("  - LOOSE×OFF가 LOOSE×ON·TIGHT보다 net↑면 → 모멘텀 가설 지지(veto가 EV 깎음).")
    _w("  - 단 LOOSE×OFF의 MDD가 과도하면 → veto 대신 사이징+타이트손절(Stage2) 필요 신호.")
    _w("  - SNDK ✓인 arm만이 오른쪽 꼬리를 실제로 포착. net이 낮아도 top%·SNDK를 함께 보라.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

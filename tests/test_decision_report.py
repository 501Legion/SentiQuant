"""daily-decision-report 단위 테스트 (M3 / Plan SC-02~06).

decision_report 순수 코어(funnel 도출·MD 포맷·콘솔 요약) + 비침습 격리 검증.
파일 I/O는 tmp 경로로 격리. (pytest 미설치 환경 — 단독 러너)

실행:
  pytest tests/test_decision_report.py
  python tests/test_decision_report.py
"""
from __future__ import annotations

import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import config
import decision_report as dr
from decision_report import ReportContext, build_daily_report, _derive_funnel, _console_summary

_DATE = "2026-06-06"


def _ctx() -> ReportContext:
    """대표 funnel 시나리오: 중립탈락·컨센탈락·게이트탈락·매수·매도(보유-only) 각 1."""
    sigs = [
        {"symbol": "AAA", "neutral_filtered": True, "passed_consensus": False,
         "neutral_ratio": 0.80, "bullish": 1, "bearish": 1},
        {"symbol": "BBB", "neutral_filtered": False, "passed_consensus": False,
         "neutral_ratio": 0.30, "bullish": 1, "bearish": 1},
        {"symbol": "CCC", "neutral_filtered": False, "passed_consensus": True,
         "neutral_ratio": 0.10, "bullish": 5, "bearish": 1},   # 게이트 탈락(주문 없음)
        {"symbol": "DDD", "neutral_filtered": False, "passed_consensus": True,
         "neutral_ratio": 0.10, "bullish": 6, "bearish": 0},   # 매수
    ]
    decisions = [
        {"symbol": "CCC", "action": "SKIP", "size_factor": 0.0, "decision_id": "id-ccc"},
        {"symbol": "DDD", "action": "BUY", "size_factor": 0.8, "decision_id": "id-ddd"},
        {"symbol": "EEE", "action": "EXIT", "size_factor": 0.0, "decision_id": "id-eee",
         "reason": "stop_loss"},
    ]
    orders = [
        {"symbol": "DDD", "side": "BUY", "shares": 10, "executed": True},
        {"symbol": "EEE", "side": "SELL", "shares": 5, "executed": True},   # 보유-only 매도(D5)
    ]
    snapshots = {("DDD", _DATE): {"opinion_score": 80.0, "consensus_ratio": 6.0}}
    records = [{"symbol": "CCC", "reason_codes": ["universe_blocked"],
                "universe_reason_codes": [], "cost_reason_codes": []}]
    return ReportContext(date=_DATE, signal_details=sigs, decisions=decisions,
                         orders=orders, snapshots=snapshots, summary={"date": _DATE},
                         decision_records=records)


# --- TC-01: funnel 단계 분류 ---
def test_tc01_funnel_classification():
    f = _derive_funnel(_ctx())
    assert f["input_n"] == 4
    assert [x["symbol"] for x in f["neutral_dropped"]] == ["AAA"]
    assert [x["symbol"] for x in f["consensus_dropped"]] == ["BBB"]
    assert [x["symbol"] for x in f["gate_dropped"]] == ["CCC"]
    assert [x["symbol"] for x in f["buys"]] == ["DDD"]
    assert [x["symbol"] for x in f["sells"]] == ["EEE"]


# --- TC-02: 매수 사유(score·합의비율·size·shares) ---
def test_tc02_buy_reasons():
    f = _derive_funnel(_ctx())
    b = f["buys"][0]
    assert b["symbol"] == "DDD"
    assert b["score"] == 80.0
    assert b["consensus_ratio"] == 6.0
    assert b["size_factor"] == 0.8
    assert b["shares"] == 10


# --- TC-03: 매도 사유(action·reason) — 보유-only 종목 포함 ---
def test_tc03_sell_reasons():
    f = _derive_funnel(_ctx())
    s = f["sells"][0]
    assert s["symbol"] == "EEE"            # signal_details에 없는 보유-only 매도 (D5)
    assert s["action"] == "EXIT"
    assert s["reason"] == "stop_loss"


# --- TC-04: 게이트 탈락 reason_codes 보강 ---
def test_tc04_gate_dropped_reasons():
    f = _derive_funnel(_ctx())
    g = f["gate_dropped"][0]
    assert g["symbol"] == "CCC"
    assert g["final_action"] == "SKIP"
    assert "universe_blocked" in g["reason_codes"]
    md = dr._format_markdown(_ctx(), f)
    assert "## 관찰 후보" in md
    assert "| CCC | 최종 판단: 보류 | 투자 대상 조건 미충족 |" in md
    assert "| BBB | 매수 의견 합의 부족 | 상승 1 / 하락 1 |" in md
    assert "| 매수 의견 합의 부족 | 1개 |" in md
    assert "bull 1/bear 1" not in md


# --- TC-05: 비침습 — 보고서 실패가 흐름을 막지 않음(community_live 격리 패턴) ---
def test_tc05_non_invasive_isolation():
    orig = dr._format_markdown
    def _boom(*a, **k):
        raise RuntimeError("boom")
    dr._format_markdown = _boom
    try:
        # community_live.py:483 직후 패턴 복제 — try/except로 격리
        report_path = None
        try:
            report_path = build_daily_report(_ctx())
        except Exception:  # noqa: BLE001 — run_live는 정상 진행
            report_path = None
        assert report_path is None     # 실패해도 흐름 유지(NFR-01/SC-06)
    finally:
        dr._format_markdown = orig


# --- TC-06: 콘솔 한 줄 요약 형식 ---
def test_tc06_console_summary():
    f = _derive_funnel(_ctx())
    line = _console_summary(f, "reports/2026-06-06.md")
    assert "입력 4" in line
    assert "중립탈락 1" in line and "컨센탈락 1" in line and "게이트탈락 1" in line
    assert "매수 1" in line and "매도 1" in line
    assert "reports/2026-06-06.md" in line


# --- TC-07: 빈 입력도 의미 있는 보고서(현 운영 케이스) ---
def test_tc07_empty_meaningful_report():
    ctx = ReportContext(date=_DATE, signal_details=[], decisions=[], orders=[],
                        snapshots={}, summary={"date": _DATE})
    f = _derive_funnel(ctx)
    md = dr._format_markdown(ctx, f)
    assert f["input_n"] == 0
    assert "오늘의 매매 판단" in md
    assert "검토할 종목이 없어" in md
    assert "매수 주문 없음" in md and "매도 주문 없음" in md
    assert "run_live" not in md
    assert "summary:" not in md


# --- TC-08: 파일 저장 + 재구동 덮어쓰기 ---
def test_tc08_file_save_overwrite():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, f"{_DATE}.md")
        p1 = build_daily_report(_ctx(), path=path)
        assert p1 == path and os.path.exists(path)
        size1 = os.path.getsize(path)
        p2 = build_daily_report(_ctx(), path=path)   # 덮어쓰기
        assert p2 == path
        assert os.path.getsize(path) == size1        # 동일 입력 → 동일 크기(append 아님)


# --- TC-09: flag OFF → no-op(None) ---
def test_tc09_flag_off_noop():
    orig = config.COMMUNITY_DECISION_REPORT_ENABLED
    try:
        config.COMMUNITY_DECISION_REPORT_ENABLED = False
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, f"{_DATE}.md")
            assert build_daily_report(_ctx(), path=path) is None
            assert not os.path.exists(path)
    finally:
        config.COMMUNITY_DECISION_REPORT_ENABLED = orig


def _run_standalone() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    print(f"\ndaily-decision-report 단위 테스트 - {len(tests)}건\n" + "-" * 50)
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
            failed += 1
    print("-" * 50)
    print(f"{passed} passed, {failed} failed (of {len(tests)})")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(_run_standalone())

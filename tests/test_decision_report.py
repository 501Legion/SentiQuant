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
    en_orig = config.COMMUNITY_REPORT_LLM_COMMENTARY_ENABLED
    config.COMMUNITY_REPORT_LLM_COMMENTARY_ENABLED = False  # 실호출/로그 부수효과 격리
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
        config.COMMUNITY_REPORT_LLM_COMMENTARY_ENABLED = en_orig


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


# --- TC-08: 파일 저장 + 재구동 덮어쓰기 (결정론 템플릿 경로 — 총평 OFF로 격리) ---
def test_tc08_file_save_overwrite():
    en_orig = config.COMMUNITY_REPORT_LLM_COMMENTARY_ENABLED
    config.COMMUNITY_REPORT_LLM_COMMENTARY_ENABLED = False  # LLM 총평은 비결정론 → 제외
    try:
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, f"{_DATE}.md")
            p1 = build_daily_report(_ctx(), path=path)
            assert p1 == path and os.path.exists(path)
            size1 = os.path.getsize(path)
            p2 = build_daily_report(_ctx(), path=path)   # 덮어쓰기
            assert p2 == path
            assert os.path.getsize(path) == size1        # 동일 입력 → 동일 크기(append 아님)
    finally:
        config.COMMUNITY_REPORT_LLM_COMMENTARY_ENABLED = en_orig


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


# --- TC-10: 총평 flag OFF → 호출 안 함 / None / 섹션 없음 ---
def test_tc10_commentary_flag_off():
    ctx = _ctx()
    f = _derive_funnel(ctx)
    # flag OFF면 complete_fn 자체를 부르지 않고 None
    called = []
    config_orig = config.COMMUNITY_REPORT_LLM_COMMENTARY_ENABLED
    config.COMMUNITY_REPORT_LLM_COMMENTARY_ENABLED = False
    try:
        assert dr._llm_commentary(ctx, f, complete_fn=lambda p: called.append(p) or "x") is None
        assert called == []
    finally:
        config.COMMUNITY_REPORT_LLM_COMMENTARY_ENABLED = config_orig
    # commentary 미주입 시 섹션 없음(순수 포매터)
    md = dr._format_markdown(ctx, f)
    assert "## 오늘의 총평" not in md


# --- TC-11: 총평 ON + 주입 → 섹션 생성 + 프롬프트 grounding + 로깅 ---
def test_tc11_commentary_enabled_injected():
    ctx = _ctx()
    f = _derive_funnel(ctx)
    seen = {}
    def _stub(prompt: str) -> str:
        seen["prompt"] = prompt
        return "오늘은 DDD 1건 매수가 있었고 나머지는 보류되었습니다."
    en_orig = config.COMMUNITY_REPORT_LLM_COMMENTARY_ENABLED
    log_orig = config.COMMUNITY_REPORT_LLM_LOG_FILE
    with tempfile.TemporaryDirectory() as d:
        config.COMMUNITY_REPORT_LLM_COMMENTARY_ENABLED = True
        config.COMMUNITY_REPORT_LLM_LOG_FILE = os.path.join(d, "log.jsonl")
        try:
            text = dr._llm_commentary(ctx, f, complete_fn=_stub)
            assert text and "DDD" in text
            # 프롬프트엔 facts 숫자가 들어가고 "지어내지 마세요" 제약이 있어야 함
            assert "지어내지" in seen["prompt"]
            assert '"input_n": 4' in seen["prompt"]
            # 로깅(재현성): 프롬프트+응답 기록
            assert os.path.exists(config.COMMUNITY_REPORT_LLM_LOG_FILE)
            with open(config.COMMUNITY_REPORT_LLM_LOG_FILE, encoding="utf-8") as fh:
                import json as _json
                rec = _json.loads(fh.readline())
            assert rec["ok"] is True and rec["response"] == text and rec["prompt"]
            # 마크다운 섹션 삽입
            md = dr._format_markdown(ctx, f, commentary=text)
            assert "## 오늘의 총평" in md and text in md
        finally:
            config.COMMUNITY_REPORT_LLM_COMMENTARY_ENABLED = en_orig
            config.COMMUNITY_REPORT_LLM_LOG_FILE = log_orig


# --- TC-12: 총평 LLM 실패 → None + 보고서는 정상(폴백) ---
def test_tc12_commentary_failure_fallback():
    ctx = _ctx()
    f = _derive_funnel(ctx)
    def _boom(prompt: str) -> str:
        raise RuntimeError("openai down")
    en_orig = config.COMMUNITY_REPORT_LLM_COMMENTARY_ENABLED
    log_orig = config.COMMUNITY_REPORT_LLM_LOG_FILE
    with tempfile.TemporaryDirectory() as d:
        config.COMMUNITY_REPORT_LLM_COMMENTARY_ENABLED = True
        config.COMMUNITY_REPORT_LLM_LOG_FILE = os.path.join(d, "log.jsonl")
        try:
            assert dr._llm_commentary(ctx, f, complete_fn=_boom) is None
            # 실패도 로깅(ok=False)
            with open(config.COMMUNITY_REPORT_LLM_LOG_FILE, encoding="utf-8") as fh:
                import json as _json
                rec = _json.loads(fh.readline())
            assert rec["ok"] is False and "openai down" in rec["error"]
            # 보고서 본문은 commentary 없이도 정상
            md = dr._format_markdown(ctx, f, commentary=None)
            assert "오늘의 매매 판단" in md and "## 오늘의 총평" not in md
        finally:
            config.COMMUNITY_REPORT_LLM_COMMENTARY_ENABLED = en_orig
            config.COMMUNITY_REPORT_LLM_LOG_FILE = log_orig


# --- TC-13: facts는 숫자만 — 자유서술/원문 발췌 미포함(환각 표면 축소) ---
def test_tc13_commentary_facts_numbers_only():
    ctx = _ctx()
    f = _derive_funnel(ctx)
    facts = dr._commentary_facts(ctx, f)
    assert set(facts) == {
        "date", "input_n", "buys", "sells", "neutral_dropped_n",
        "consensus_dropped_n", "gate_dropped", "llm_router_calls",
    }
    assert facts["input_n"] == 4
    assert facts["buys"][0]["symbol"] == "DDD"
    # 원문 발췌/자유서술 키가 새어들어가지 않음
    assert "excerpts" not in facts and "reasoning" not in facts


# --- TC-14: 접수된 KIS 주문은 체결 실패처럼 표시하지 않음 ---
def test_tc14_pending_order_status_is_acceptance_not_failed():
    ctx = ReportContext(
        date=_DATE,
        signal_details=[
            {"symbol": "PLTR", "neutral_filtered": False, "passed_consensus": True,
             "neutral_ratio": 0.12, "bullish": 8, "bearish": 3},
            {"symbol": "AVGO", "neutral_filtered": False, "passed_consensus": True,
             "neutral_ratio": 0.20, "bullish": 2, "bearish": 6},
        ],
        decisions=[
            {"symbol": "PLTR", "action": "BUY", "size_factor": 1.0, "decision_id": "id-pltr"},
            {"symbol": "AVGO", "action": "SELL", "reason": "consensus_break"},
        ],
        orders=[
            {"symbol": "PLTR", "side": "BUY", "shares": 84, "accepted": True,
             "status": "PENDING", "order_no": "0000056615", "executed": False},
            {"symbol": "AVGO", "side": "SELL", "shares": 15, "accepted": True,
             "status": "PENDING", "order_no": "0000056608", "executed": False},
        ],
        snapshots={("PLTR", _DATE): {"opinion_score": 54.8, "consensus_ratio": 1.6}},
        summary={"date": _DATE},
    )
    f = _derive_funnel(ctx)
    md = dr._format_markdown(ctx, f)

    assert "| 종목 | 여론 점수 | 합의 비율 | 비중 | 수량 | 주문 상태 |" in md
    assert "| 종목 | 판단 | 사유 | 수량 | 주문 상태 |" in md
    assert "| PLTR | 54.8 | 1.60 | 1.00 | 84 | 접수 (0000056615) |" in md
    assert "| AVGO | 매도 | 매수 의견 약화 | 15 | 접수 (0000056608) |" in md
    assert "❌" not in md


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

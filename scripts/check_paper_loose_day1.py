#!/usr/bin/env python3
"""
일회성 점검: PAPER LOOSE×OFF 설정(2026-06-14 적용)의 첫 실거래일(2026-06-15, 월)
신호/주문 결과를 검증해 data/paper_loose_day1_report.txt + stdout + notifier로 보고한다.

배경: 2026-06-14 config를 LOOSE×OFF로 전환(score52/cons1.3/neut0.96·0.95/min2, LLM veto OFF),
sentiquant.service 재시작. KIS 모의투자(실돈 아님). 06-12(금)엔 깔때기 TIGHT로 매수후보 0이었음.
이 스크립트는 월 06-15 08:45 ET 신호 + 09:35 ET 주문 후(22:50 KST) 1회 실행해 결과를 본다.

점검: (1) signal heartbeat 2026-06-15 갱신 (2) 신호·주문잡 정상실행(휴장 스킵 아님)
      (3) LOOSE 깔때기로 매수후보 surface (4) veto OFF에서 매수 체결 (5) 워치독 false-restart 무.

cron 자기제거: 실행 후 crontab에서 CRON_MARKER 라인을 지운다(내년 재발화 방지).

사용: venv/bin/python scripts/check_paper_loose_day1.py
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
KST = ZoneInfo("Asia/Seoul")

TARGET = "2026-06-15"  # 첫 LOOSE×OFF 실거래일
HEARTBEAT = os.path.join(ROOT, "data", "heartbeat.json")
LOG = os.path.join(ROOT, "data", "trading.log")
WD_STATE = os.path.join(ROOT, "data", "watchdog_restart_state.json")
REPORT_MD = os.path.join(ROOT, "data", "community", "live", "reports", f"{TARGET}.md")
OUT = os.path.join(ROOT, "data", "paper_loose_day1_report.txt")
CRON_MARKER = "check_paper_loose_day1"


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001
        return {"__error__": str(e)}


def _self_remove_cron():
    """실행 후 crontab에서 자기 라인 제거 (내년 06-15 재발화 방지)."""
    try:
        cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        if cur.returncode != 0:
            return
        kept = [ln for ln in cur.stdout.splitlines() if CRON_MARKER not in ln]
        subprocess.run(["crontab", "-"], input="\n".join(kept) + "\n", text=True)
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    now_kst = datetime.now(KST)
    lines = [f"[paper-loose-day1] now={now_kst:%Y-%m-%d %H:%M %Z}, 대상거래일={TARGET}(08:45 ET 신호 / 09:35 ET 주문)"]
    flags = {}

    # (1) signal heartbeat 이 대상일로 갱신됐는지
    hb = _load_json(HEARTBEAT)
    sig_raw = hb.get("signal") if isinstance(hb, dict) else None
    sig_et_date = None
    if sig_raw:
        try:
            ts = datetime.fromisoformat(sig_raw)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            sig_et_date = ts.astimezone(ET).date().isoformat()
        except Exception:  # noqa: BLE001
            pass
    flags["heartbeat_fresh"] = (sig_et_date == TARGET)
    lines.append(f"(1) signal heartbeat = {sig_raw} → ET날짜 {sig_et_date} "
                 f"{'✅ 갱신됨' if flags['heartbeat_fresh'] else '❌ 미갱신(대상일 아님)'}")

    # (2) 신호/주문 잡이 휴장 스킵 아닌 정상실행됐는지 (trading.log, KST 날짜로 필터)
    #     06-15 08:45 ET = 06-15 21:45 KST, 09:35 ET = 06-15 22:35 KST → 둘 다 KST 06-15
    sig_started = sig_skipped = sig_done = ord_started = ord_skipped = False
    decision_line = ""
    try:
        with open(LOG, encoding="utf-8", errors="replace") as f:
            for ln in f:
                if not ln.startswith(TARGET):
                    continue
                if "신호 계산 잡 시작" in ln:
                    sig_started = True
                if "신호 계산 잡" in ln and "휴장" in ln:
                    sig_skipped = True
                if "신호 계산 잡 완료" in ln:
                    sig_done = True
                if "주문 처리 잡 시작" in ln:
                    ord_started = True
                if "주문 처리 잡" in ln and "휴장" in ln:
                    ord_skipped = True
                if "decision_report" in ln and "입력" in ln:
                    decision_line = ln.strip()
    except Exception as e:  # noqa: BLE001
        lines.append(f"(2) 로그 읽기 실패: {e}")
    flags["jobs_ran"] = sig_started and not sig_skipped and ord_started and not ord_skipped
    lines.append(f"(2) 신호잡: 시작={sig_started} 완료={sig_done} 휴장스킵={sig_skipped} / "
                 f"주문잡: 시작={ord_started} 휴장스킵={ord_skipped} "
                 f"{'✅ 정상실행' if flags['jobs_ran'] else '❌ 미실행/휴장스킵'}")

    # (3)(4) 매수후보 surface + 매수 체결 — decision_report 콘솔라인 우선, 리포트 md 보조
    buys = candidates = None
    if decision_line:
        m_buy = re.search(r"매수\s*(\d+)", decision_line)
        m_cons = re.search(r"컨센탈락\s*(\d+)", decision_line)
        m_gate = re.search(r"게이트탈락\s*(\d+)", decision_line)
        if m_buy:
            buys = int(m_buy.group(1))
        # surface된 후보 ≈ 게이트 진입분(컨센통과). 정확 집계는 리포트 md로.
        lines.append(f"    decision_report: {decision_line.split('decision_report:')[-1].strip()}")
    if os.path.exists(REPORT_MD):
        try:
            md = open(REPORT_MD, encoding="utf-8").read()
            m_c = re.search(r"매매\s*후보\s*\|\s*(\d+)", md) or re.search(r"매매 후보[^\d]*(\d+)\s*개", md)
            m_b = re.search(r"매수\s*\|\s*(\d+)", md) or re.search(r"\|\s*매수\s*\|\s*(\d+)", md)
            if m_c:
                candidates = int(m_c.group(1))
            if m_b and buys is None:
                buys = int(m_b.group(1))
        except Exception:  # noqa: BLE001
            pass
    else:
        lines.append(f"    (리포트 {os.path.basename(REPORT_MD)} 없음)")
    flags["candidates_surfaced"] = bool(candidates and candidates > 0)
    flags["buys_executed"] = bool(buys and buys > 0)
    lines.append(f"(3) 매매후보 = {candidates}  {'✅ surface됨' if flags['candidates_surfaced'] else '❌ 0/미확인 (06-12 대비 개선 안 됨)'}")
    lines.append(f"(4) 매수 체결 = {buys}  {'✅ veto OFF에서 매수발생' if flags['buys_executed'] else '⚠️ 매수 0 (후보는 있었나 확인 필요)'}")

    # (5) 워치독 false-restart (signal:TARGET 키 없어야 정상)
    wd = _load_json(WD_STATE)
    wd_key = f"signal:{TARGET}"
    restarted = isinstance(wd, dict) and wd_key in wd
    flags["no_false_restart"] = not restarted
    lines.append(f"(5) 워치독 {wd_key} = {'⚠️ 재시작 발생' if restarted else '✅ 없음(정상)'}")

    # 종합 판정
    core_ok = flags["heartbeat_fresh"] and flags["jobs_ran"] and flags["no_false_restart"]
    if core_ok and flags["buys_executed"]:
        head = "✅ PAPER LOOSE×OFF 첫 거래일 — 발화·후보·매수 모두 정상, 페이퍼테스트 가동 확인"
        verdict = "OK"
    elif core_ok and flags["candidates_surfaced"]:
        head = "🟡 첫 거래일 — 발화/후보 정상이나 매수 0. 후보는 올라왔으니 깔때기는 작동, 매수 미발생 사유 확인 필요"
        verdict = "PARTIAL"
    elif core_ok:
        head = "🟡 첫 거래일 — 잡은 정상 실행됐으나 매수후보 0. LOOSE 완화 효과 미관측(그날 여론이 빈약했을 수 있음)"
        verdict = "PARTIAL"
    else:
        head = "❌ 첫 거래일 — 발화/실행/워치독 중 문제. config 반영·서비스 상태 점검 필요"
        verdict = "FAIL"

    report = head + "\n" + "\n".join(lines)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(report + f"\n[verdict={verdict}] {datetime.now(UTC):%Y-%m-%dT%H:%M:%SZ}\n")
    print(report)

    try:
        sys.path.insert(0, ROOT)
        import notifier
        notifier.notify("info" if verdict in ("OK", "PARTIAL") else "error",
                        f"[paper-loose-day1] {head}", payload={"detail": lines})
    except Exception as e:  # noqa: BLE001
        print(f"[notify skipped] {e}")

    _self_remove_cron()
    return 0 if verdict in ("OK", "PARTIAL") else 1


if __name__ == "__main__":
    raise SystemExit(main())

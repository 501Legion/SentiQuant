#!/usr/bin/env python3
# Design Ref: streamlit-dashboard-deploy §6.2 — 대시보드 데이터 동기화
# allowlist 서브셋 + 슬림앱 코드를 orphan 'dashboard-data' 브랜치에 단일커밋 force-push.
# 우분투 실매매 박스가 systemd timer로 주기 실행. Streamlit Cloud가 그 브랜치를 배포.
#
# 안전(NFR-01): allowlist(명시 파일만) + DENY 재검사로 비밀 절대 제외.
# 사용: python scripts/sync_dashboard_data.py            # curate + push
#       python scripts/sync_dashboard_data.py --no-push  # curate만(로컬 검증)
import json
import os
import shutil
import subprocess
import sys
import hashlib
import csv
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRANCH = "dashboard-data"

# 복사 대상 (명시) — 디렉터리/파일 혼용
SYNC_ALLOWLIST = [
    "data/portfolio.json",
    "data/trades.csv",
    "data/community/live/reports",                  # 디렉터리
    "data/community/live/decisions.jsonl",
    "data/community/live/run_summaries.jsonl",
    "data/community/daily_opinion_snapshots.jsonl",
]
SYNC_CODE = [
    "dashboard_app.py",
    "dashboard_utils.py",
    "requirements-dashboard.txt",
    ".streamlit/config.toml",
    "assets/sentiquant-logo.jpeg",
]
# 방어적 차단 — 경로에 이 문자열이 있으면 절대 복사 금지(비밀/모델/캐시)
DENY_SUBSTR = [".env", "kis_token", "models/", "models\\", "cache", "secret", ".key", "token"]
OHLCV_SOURCE_DIR = "data/backtest_snapshots/v2/ohlcv"
OHLCV_MAX_FILES_PER_SYMBOL = 5
OHLCV_RECENT_DAYS = 90
OHLCV_RECENT_DECISION_LINES = 1000
OHLCV_RECENT_TRADE_ROWS = 300
OHLCV_SNAPSHOT_LOOKBACK_DAYS = 30
OHLCV_TOP_SNAPSHOT_SYMBOLS = 30


def _denied(rel: str) -> bool:
    low = rel.replace("\\", "/").lower()
    return any(s.replace("\\", "/") in low for s in DENY_SUBSTR)


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _symbol(value) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    text = text.split(".")[0]
    return text if re.fullmatch(r"[A-Z0-9][A-Z0-9.-]{0,9}", text) else ""


def _parse_date(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        return None


def _recent_records(records: list[dict], date_keys: tuple[str, ...], days: int) -> list[dict]:
    dated = []
    undated = []
    for rec in records:
        d = None
        for key in date_keys:
            d = _parse_date(rec.get(key))
            if d:
                break
        if d:
            dated.append((d, rec))
        else:
            undated.append(rec)
    if not dated:
        return records
    latest = max(d for d, _ in dated)
    cutoff = latest - timedelta(days=days)
    return [rec for d, rec in dated if d >= cutoff] + undated[-50:]


def _read_jsonl_tail(path: Path, max_lines: int) -> list[dict]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()[-max_lines:]
    rows = []
    for line in lines:
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def _iter_symbols(value):
    if isinstance(value, str):
        sym = _symbol(value)
        if sym:
            yield sym
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _iter_symbols(item)
    elif isinstance(value, dict):
        for key in ("symbol", "ticker"):
            if key in value:
                yield from _iter_symbols(value.get(key))


def _dashboard_ohlcv_symbols(src: Path) -> set[str]:
    """대시보드가 실제로 볼 가능성이 높은 종목만 추린다.

    원본 OHLCV 캐시는 서버에 그대로 두고, Cloud payload에는 보유/최근 거래/최근 판단/
    최근 여론 상위 종목만 복사해 파일 수 증가를 제한한다.
    """
    symbols: set[str] = set()

    portfolio = _read_json(src / "data/portfolio.json")
    positions = portfolio.get("positions") or {}
    if isinstance(positions, dict):
        for sym in positions:
            if s := _symbol(sym):
                symbols.add(s)

    trades_path = src / "data/trades.csv"
    if trades_path.exists():
        try:
            with trades_path.open(encoding="utf-8", newline="") as f:
                rows = list(csv.DictReader(f))[-OHLCV_RECENT_TRADE_ROWS:]
            for row in _recent_records(rows, ("date", "timestamp", "created_at"), OHLCV_RECENT_DAYS):
                if s := _symbol(row.get("symbol")):
                    symbols.add(s)
        except Exception:
            pass

    decisions = _read_jsonl_tail(
        src / "data/community/live/decisions.jsonl",
        OHLCV_RECENT_DECISION_LINES,
    )
    for rec in _recent_records(decisions, ("date", "created_at", "ts"), OHLCV_RECENT_DAYS):
        for key in ("symbol", "candidate_symbols", "symbols"):
            symbols.update(_iter_symbols(rec.get(key)))

    summaries = _read_jsonl_tail(
        src / "data/community/live/run_summaries.jsonl",
        OHLCV_RECENT_DECISION_LINES,
    )
    for rec in _recent_records(summaries, ("date", "created_at", "ts"), OHLCV_RECENT_DAYS):
        for key in ("symbols", "candidate_symbols", "top_symbols", "observed_symbols"):
            symbols.update(_iter_symbols(rec.get(key)))

    snapshots = _read_jsonl_tail(
        src / "data/community/daily_opinion_snapshots.jsonl",
        5000,
    )
    snapshot_rows = _recent_records(
        snapshots,
        ("date", "snapshot_date", "created_at", "ts"),
        OHLCV_SNAPSHOT_LOOKBACK_DAYS,
    )
    scored: dict[str, float] = {}
    for rec in snapshot_rows:
        sym = _symbol(rec.get("symbol"))
        if not sym:
            continue
        try:
            mentions = float(rec.get("total_mentions") or rec.get("mentions") or 0)
        except (TypeError, ValueError):
            mentions = 0.0
        scored[sym] = max(scored.get(sym, 0.0), mentions)
    for sym, _ in sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))[:OHLCV_TOP_SNAPSHOT_SYMBOLS]:
        symbols.add(sym)

    return symbols


def _copy_curated_ohlcv(src: Path, staging: Path, included: list[str]) -> dict:
    source = src / OHLCV_SOURCE_DIR
    if not source.exists():
        return {"symbols": 0, "files": 0}
    wanted = _dashboard_ohlcv_symbols(src)
    copied_symbols: set[str] = set()
    copied_files = 0
    for sym in sorted(wanted):
        files = sorted(source.glob(f"{sym}_*.csv"), key=lambda p: p.name)
        for f in files[-OHLCV_MAX_FILES_PER_SYMBOL:]:
            rel = f.relative_to(src).as_posix()
            if _denied(rel):
                continue
            (staging / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, staging / rel)
            included.append(rel)
            copied_symbols.add(sym)
            copied_files += 1
    return {"symbols": len(copied_symbols), "files": copied_files}


def _payload_files(staging: Path) -> list[Path]:
    return sorted(
        p for p in staging.rglob("*")
        if p.is_file() and p.relative_to(staging).as_posix() != "last_sync.json"
    )


def payload_hash(staging: Path) -> str:
    """last_sync.json 자체를 제외한 대시보드 페이로드 해시.

    last_sync.synced_at은 매 실행마다 달라지므로, 실제 앱/데이터 변경 여부는
    이 해시로 판단한다.
    """
    digest = hashlib.sha256()
    for path in _payload_files(staging):
        rel = path.relative_to(staging).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def curate(src: Path, staging: Path) -> list[str]:
    """allowlist + code를 staging에 복사. DENY 경로는 제외. 포함된 상대경로 목록 반환.
    # Plan SC: SC-03 — allowlist만, 비밀 제외. (순수 파일 연산 — 테스트 대상)"""
    included: list[str] = []

    def _copy(rel: str):
        if _denied(rel):
            return  # 비밀/모델/캐시 — 절대 복사 금지
        s = src / rel
        if not s.exists():
            return
        d = staging / rel
        if s.is_dir():
            for f in sorted(s.rglob("*")):
                if f.is_file():
                    frel = f.relative_to(src).as_posix()
                    if _denied(frel):
                        continue
                    (staging / frel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, staging / frel)
                    included.append(frel)
        else:
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, d)
            included.append(rel)

    for rel in SYNC_CODE + SYNC_ALLOWLIST:
        _copy(rel)
    ohlcv_meta = _copy_curated_ohlcv(src, staging, included)

    # Streamlit Cloud는 배포 브랜치 루트의 requirements.txt를 자동 감지 → 슬림 deps를 그 이름으로 제공.
    # (main의 무거운 requirements.txt는 이 orphan 브랜치에 없음)
    slim = src / "requirements-dashboard.txt"
    if slim.exists():
        shutil.copy2(slim, staging / "requirements.txt")
        included.append("requirements.txt")

    # last_sync.json (D6)
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=src,
                             capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        sha = ""
    current_payload_hash = payload_hash(staging)
    synced_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (staging / "last_sync.json").write_text(json.dumps({
        "synced_at": synced_at,
        "source_commit": sha,
        "payload_hash": current_payload_hash,
        "payload_file_count": len(_payload_files(staging)),
        "ohlcv_policy": {
            "mode": "curated",
            "recent_days": OHLCV_RECENT_DAYS,
            "snapshot_lookback_days": OHLCV_SNAPSHOT_LOOKBACK_DAYS,
            "top_snapshot_symbols": OHLCV_TOP_SNAPSHOT_SYMBOLS,
            "max_files_per_symbol": OHLCV_MAX_FILES_PER_SYMBOL,
        },
        "ohlcv_symbol_count": ohlcv_meta["symbols"],
        "ohlcv_file_count": ohlcv_meta["files"],
        "payload_changed": True,
        "payload_changed_at": synced_at,
    }), encoding="utf-8")
    included.append("last_sync.json")
    return included


def push_branch(staging: Path) -> str:
    """staging 내용을 orphan dashboard-data 브랜치 단일커밋으로 force-push (D3).
    git worktree로 메인 작업트리 오염 없이 수행. 변경 상태("changed"|"heartbeat")를 반환한다.
    (서버 전용 — 원격 인증 필요)"""
    wt = ROOT / ".dashboard-worktree"
    subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=ROOT,
                   capture_output=True)
    shutil.rmtree(wt, ignore_errors=True)
    subprocess.run(["git", "worktree", "prune"], cwd=ROOT, capture_output=True)
    subprocess.run(["git", "fetch", "origin", BRANCH], cwd=ROOT,
                   capture_output=True)
    has_remote = subprocess.run(
        ["git", "rev-parse", "--verify", f"origin/{BRANCH}"],
        cwd=ROOT, capture_output=True
    ).returncode == 0
    # 멱등성: 기존 로컬 브랜치가 있으면 삭제(다음 -b/-B 충돌 방지)
    subprocess.run(["git", "branch", "-D", BRANCH], cwd=ROOT, capture_output=True)
    if has_remote:
        subprocess.run(["git", "worktree", "add", "--force", "-B",
                        BRANCH, str(wt), f"origin/{BRANCH}"], cwd=ROOT, check=True)
    else:
        # orphan 브랜치를 빈 상태로 새로 만든 워크트리
        subprocess.run(["git", "worktree", "add", "--force", "--orphan",
                        "-b", BRANCH, str(wt)], cwd=ROOT, check=True)
    try:
        previous_meta = _read_json(wt / "last_sync.json")
        current_meta = _read_json(staging / "last_sync.json")
        previous_payload_hash = previous_meta.get("payload_hash")
        current_payload_hash = current_meta.get("payload_hash")
        if previous_payload_hash and previous_payload_hash == current_payload_hash:
            current_meta["payload_changed"] = False
            current_meta["payload_changed_at"] = (
                previous_meta.get("payload_changed_at")
                or previous_meta.get("synced_at")
                or current_meta.get("synced_at")
            )
            sync_status = "heartbeat"
        else:
            current_meta["payload_changed"] = True
            current_meta["payload_changed_at"] = current_meta.get("synced_at")
            sync_status = "changed"
        (staging / "last_sync.json").write_text(
            json.dumps(current_meta, ensure_ascii=False),
            encoding="utf-8",
        )
        # 워크트리 내용 비우고 staging 복사
        for p in wt.iterdir():
            if p.name == ".git":
                continue
            shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink()
        for f in staging.rglob("*"):
            if f.is_file():
                rel = f.relative_to(staging)
                (wt / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, wt / rel)
        subprocess.run(["git", "add", "-A"], cwd=wt, check=True)
        has_head = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=wt,
                                  capture_output=True).returncode == 0
        commit_message = f"dashboard sync {datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ}"
        if has_head:
            subprocess.run(["git", "commit", "--amend", "-q", "-m", commit_message],
                           cwd=wt, check=True)
        else:
            subprocess.run(["git", "commit", "-q", "-m", commit_message],
                           cwd=wt, check=True)
        subprocess.run(["git", "push", "--force", "origin", BRANCH], cwd=wt, check=True)
        return sync_status
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=ROOT,
                       capture_output=True)


def main(argv) -> int:
    staging = ROOT / ".dashboard-staging"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        included = curate(ROOT, staging)
        print(f"[sync] curate 완료 - {len(included)}개 파일")
        # 안전 재검사: DENY가 staging에 하나라도 있으면 중단(이중 방어)
        for f in staging.rglob("*"):
            if f.is_file() and _denied(f.relative_to(staging).as_posix()):
                print(f"[sync] 위험 파일 감지 - 중단: {f}")
                return 2
        if "--no-push" in argv:
            print("[sync] --no-push: push 생략")
            return 0
        sync_status = push_branch(staging)
        if sync_status == "changed":
            print(f"[sync] dashboard-data 브랜치 force-push 완료")
        else:
            print("[sync] dashboard-data heartbeat force-push 완료")
        return 0
    finally:
        shutil.rmtree(staging, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

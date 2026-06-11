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
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRANCH = "dashboard-data"

# 복사 대상 (명시) — 디렉터리/파일 혼용
SYNC_ALLOWLIST = [
    "data/portfolio.json",
    "data/trades.csv",
    "data/community/live/reports",                  # 디렉터리
    "data/community/live/decisions.jsonl",
    "data/community/daily_opinion_snapshots.jsonl",
    "data/backtest_snapshots/v2/ohlcv",             # 가격 차트용(공개 시세, 비밀 아님)
]
SYNC_CODE = [
    "dashboard_app.py",
    "requirements-dashboard.txt",
    ".streamlit/config.toml",
    "assets/sentiquant-logo.jpeg",
]
# 방어적 차단 — 경로에 이 문자열이 있으면 절대 복사 금지(비밀/모델/캐시)
DENY_SUBSTR = [".env", "kis_token", "models/", "models\\", "cache", "secret", ".key", "token"]


def _denied(rel: str) -> bool:
    low = rel.replace("\\", "/").lower()
    return any(s.replace("\\", "/") in low for s in DENY_SUBSTR)


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
    (staging / "last_sync.json").write_text(json.dumps({
        "synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_commit": sha,
    }), encoding="utf-8")
    included.append("last_sync.json")
    return included


def push_branch(staging: Path) -> None:
    """staging 내용을 orphan dashboard-data 브랜치 단일커밋으로 force-push (D3).
    git worktree로 메인 작업트리 오염 없이 수행. (서버 전용 — 원격 인증 필요)"""
    wt = ROOT / ".dashboard-worktree"
    subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=ROOT,
                   capture_output=True)
    shutil.rmtree(wt, ignore_errors=True)
    subprocess.run(["git", "worktree", "prune"], cwd=ROOT, capture_output=True)
    # 멱등성: 기존 로컬 브랜치가 있으면 삭제(다음 -b 충돌 방지)
    subprocess.run(["git", "branch", "-D", BRANCH], cwd=ROOT, capture_output=True)
    # orphan 브랜치를 빈 상태로 새로 만든 워크트리
    subprocess.run(["git", "worktree", "add", "--force", "--orphan",
                    "-b", BRANCH, str(wt)], cwd=ROOT, check=True)
    try:
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
        subprocess.run(["git", "commit", "-q", "-m",
                        f"dashboard sync {datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ}"],
                       cwd=wt, check=True)
        subprocess.run(["git", "push", "--force", "origin", BRANCH], cwd=wt, check=True)
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
        push_branch(staging)
        print(f"[sync] dashboard-data 브랜치 force-push 완료")
        return 0
    finally:
        shutil.rmtree(staging, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

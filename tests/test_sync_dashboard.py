"""streamlit-dashboard-deploy 동기화 단위 테스트 (M3 / Plan SC-03,01,05).

curate() allowlist·비밀 차단 + dashboard_app heavy-import 0 검증.
push_branch()는 임시 git repo + bare origin으로 멱등성만 검증.

실행:
  pytest tests/test_sync_dashboard.py
  python tests/test_sync_dashboard.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import importlib.util
_spec = importlib.util.spec_from_file_location(
    "sync_dashboard_data", os.path.join(_ROOT, "scripts", "sync_dashboard_data.py"))
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)


def _build_fake_src(root: Path):
    """비밀 + 데이터 혼재 src 트리 생성."""
    (root / "data/community/live/reports").mkdir(parents=True)
    (root / "data/community/live/reports/2026-06-08.md").write_text("# report", encoding="utf-8")
    (root / "data/community/live/decisions.jsonl").write_text('{"symbol":"X"}\n', encoding="utf-8")
    (root / "data/community/live/run_summaries.jsonl").write_text('{"date":"2026-06-08"}\n', encoding="utf-8")
    (root / "data/community/daily_opinion_snapshots.jsonl").write_text('{"date":"2026-06-08"}\n', encoding="utf-8")
    (root / "data/portfolio.json").write_text('{"cash":100000}', encoding="utf-8")
    (root / "data/trades.csv").write_text("date,symbol\n", encoding="utf-8")
    # --- 비밀/모델/캐시 (절대 포함되면 안 됨) ---
    (root / ".env").write_text("KIS_APP_KEY=secret", encoding="utf-8")
    (root / "data/kis_token.json").write_text('{"token":"x"}', encoding="utf-8")
    (root / "models/finbert-onnx").mkdir(parents=True)
    (root / "models/finbert-onnx/model.onnx").write_text("BIN", encoding="utf-8")
    (root / "data/gpt_cache.json").write_text("{}", encoding="utf-8")
    # 코드(있으면 포함 대상)
    (root / "dashboard_app.py").write_text("# app", encoding="utf-8")
    (root / "requirements-dashboard.txt").write_text("streamlit\n", encoding="utf-8")
    (root / ".streamlit").mkdir()
    (root / ".streamlit/config.toml").write_text("[server]\n", encoding="utf-8")
    (root / "assets").mkdir()
    (root / "assets/sentiquant-logo.jpeg").write_bytes(b"JPEG")


# --- TC-01: allowlist 데이터 포함 ---
def test_tc01_allowlist_included():
    with tempfile.TemporaryDirectory() as d:
        src, stg = Path(d) / "src", Path(d) / "stg"
        src.mkdir(); stg.mkdir()
        _build_fake_src(src)
        inc = sync.curate(src, stg)
        incset = set(inc)
        assert "data/portfolio.json" in incset
        assert "data/trades.csv" in incset
        assert "data/community/live/decisions.jsonl" in incset
        assert "data/community/live/run_summaries.jsonl" in incset
        assert "data/community/daily_opinion_snapshots.jsonl" in incset
        assert "data/community/live/reports/2026-06-08.md" in incset
        # 코드도 포함
        assert "dashboard_app.py" in incset and "requirements-dashboard.txt" in incset
        assert "assets/sentiquant-logo.jpeg" in incset


# --- TC-02: 비밀/모델/캐시 절대 제외 ---
def test_tc02_secrets_excluded():
    with tempfile.TemporaryDirectory() as d:
        src, stg = Path(d) / "src", Path(d) / "stg"
        src.mkdir(); stg.mkdir()
        _build_fake_src(src)
        sync.curate(src, stg)
        # staging에 비밀/모델/캐시가 하나도 없어야 함
        present = [p.relative_to(stg).as_posix() for p in stg.rglob("*") if p.is_file()]
        joined = " ".join(present).lower()
        assert ".env" not in present
        assert "kis_token" not in joined
        assert "model.onnx" not in joined and "models/" not in joined
        assert "gpt_cache" not in joined


# --- TC-03: last_sync.json 생성 ---
def test_tc03_last_sync_written():
    with tempfile.TemporaryDirectory() as d:
        src, stg = Path(d) / "src", Path(d) / "stg"
        src.mkdir(); stg.mkdir()
        _build_fake_src(src)
        inc = sync.curate(src, stg)
        assert "last_sync.json" in inc
        import json
        meta = json.loads((stg / "last_sync.json").read_text(encoding="utf-8"))
        assert "synced_at" in meta
        assert len(meta.get("payload_hash", "")) == 64
        assert meta.get("payload_file_count", 0) > 0


# --- TC-04: _denied 차단 로직 ---
def test_tc04_denied_logic():
    assert sync._denied(".env") is True
    assert sync._denied("data/kis_token.json") is True
    assert sync._denied("models/finbert-onnx/model.onnx") is True
    assert sync._denied("data/gpt_cache.json") is True
    assert sync._denied("data/portfolio.json") is False
    assert sync._denied("data/trades.csv") is False


# --- TC-05: dashboard_app heavy import 0 (소스 스캔) ---
def test_tc05_dashboard_no_heavy_import():
    # 주석 제외, 실제 import/from 문만 스캔 (주석에 모듈명이 들어가도 오탐 방지)
    lines = Path(_ROOT, "dashboard_app.py").read_text(encoding="utf-8").splitlines()
    import_lines = [ln for ln in lines
                    if ln.strip().startswith(("import ", "from ")) and not ln.strip().startswith("#")]
    forbidden = ["torch", "transformers", "optimum", "onnxruntime", "community_live",
                 "backtester", "kis_broker", "indicators", "sentiment_provider",
                 "praw", "reddit_collector"]
    hits = [f for f in forbidden if any(f in ln for ln in import_lines)]
    assert hits == [], f"대시보드가 무거운 모듈 import: {hits}"


# --- TC-06: push_branch 멱등성 (기존 dashboard-data 로컬 브랜치 있어도 2회차 성공) ---
def test_tc06_push_branch_reuses_existing_dashboard_branch():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "repo"
        origin = Path(d) / "origin.git"
        staging = Path(d) / "staging"
        root.mkdir()
        staging.mkdir()

        def run(args, cwd=root):
            return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)

        run(["git", "init", "-q"])
        run(["git", "config", "user.email", "test@example.com"])
        run(["git", "config", "user.name", "Test User"])
        (root / "README.md").write_text("main\n", encoding="utf-8")
        run(["git", "add", "README.md"])
        run(["git", "commit", "-q", "-m", "init"])
        run(["git", "init", "-q", "--bare", str(origin)], cwd=Path(d))
        run(["git", "remote", "add", "origin", str(origin)])

        old_root = sync.ROOT
        try:
            sync.ROOT = root
            (staging / "dashboard_app.py").write_text("first\n", encoding="utf-8")
            sync.push_branch(staging)

            first = run(["git", "rev-parse", "dashboard-data"]).stdout.strip()
            assert run(["git", "branch", "--list", "dashboard-data"]).stdout.strip()
            assert run(["git", "ls-tree", "-r", "--name-only", "dashboard-data"]).stdout.strip() == "dashboard_app.py"

            (staging / "dashboard_app.py").write_text("second\n", encoding="utf-8")
            sync.push_branch(staging)

            second = run(["git", "rev-parse", "dashboard-data"]).stdout.strip()
            remote = run(["git", "rev-parse", "origin/dashboard-data"]).stdout.strip()
            assert second != first
            assert second == remote
            assert "second" in run(["git", "show", "dashboard-data:dashboard_app.py"]).stdout
        finally:
            sync.ROOT = old_root


# --- TC-07: payload 변화가 없으면 last_sync만 달라도 push 생략 ---
def test_tc07_push_branch_skips_unchanged_payload():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "repo"
        origin = Path(d) / "origin.git"
        staging = Path(d) / "staging"
        root.mkdir()
        staging.mkdir()

        def run(args, cwd=root):
            return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)

        run(["git", "init", "-q"])
        run(["git", "config", "user.email", "test@example.com"])
        run(["git", "config", "user.name", "Test User"])
        (root / "README.md").write_text("main\n", encoding="utf-8")
        run(["git", "add", "README.md"])
        run(["git", "commit", "-q", "-m", "init"])
        run(["git", "init", "-q", "--bare", str(origin)], cwd=Path(d))
        run(["git", "remote", "add", "origin", str(origin)])

        old_root = sync.ROOT
        try:
            sync.ROOT = root
            (staging / "dashboard_app.py").write_text("same\n", encoding="utf-8")
            first_hash = sync.payload_hash(staging)
            (staging / "last_sync.json").write_text(
                f'{{"synced_at":"2026-06-12T00:00:00+00:00","payload_hash":"{first_hash}"}}',
                encoding="utf-8",
            )
            sync.push_branch(staging)
            first = run(["git", "rev-parse", "origin/dashboard-data"]).stdout.strip()

            (staging / "last_sync.json").write_text(
                f'{{"synced_at":"2026-06-12T00:30:00+00:00","payload_hash":"{first_hash}"}}',
                encoding="utf-8",
            )
            sync.push_branch(staging)
            second = run(["git", "rev-parse", "origin/dashboard-data"]).stdout.strip()

            assert second == first
            assert "2026-06-12T00:00:00+00:00" in run(
                ["git", "show", "origin/dashboard-data:last_sync.json"]
            ).stdout
        finally:
            sync.ROOT = old_root


def _run_standalone() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    print(f"\nsync_dashboard 단위 테스트 - {len(tests)}건\n" + "-" * 50)
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

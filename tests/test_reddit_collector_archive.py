"""RedditCollector 저장 파일 재현성 테스트."""
from __future__ import annotations

import json
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import config
from reddit_collector import RedditCollector


def test_save_posts_preserves_run_archive():
    saved_dir = config.REDDIT_DATA_DIR
    try:
        with tempfile.TemporaryDirectory() as d:
            config.REDDIT_DATA_DIR = os.path.join(d, "reddit")
            collector = RedditCollector.__new__(RedditCollector)

            collector._save_posts("2026-06-12", {
                "NVDA": [{"title": "first", "body_excerpt": ""}],
            })
            collector._save_posts("2026-06-12", {
                "AMD": [{"title": "second", "body_excerpt": ""}],
            })

            day_dir = os.path.join(config.REDDIT_DATA_DIR, "2026-06-12")
            latest_path = os.path.join(day_dir, "wsb_posts.json")
            with open(latest_path, encoding="utf-8") as f:
                latest = json.load(f)
            assert "AMD" in latest and "NVDA" not in latest

            runs_dir = os.path.join(day_dir, "runs")
            run_dirs = sorted(os.listdir(runs_dir))
            assert len(run_dirs) == 2

            archived_symbols = []
            for run_id in run_dirs:
                with open(os.path.join(runs_dir, run_id, "wsb_posts.json"), encoding="utf-8") as f:
                    archived_symbols.extend(k for k in json.load(f) if k != "date")
                with open(os.path.join(runs_dir, run_id, "metadata.json"), encoding="utf-8") as f:
                    meta = json.load(f)
                assert meta["date"] == "2026-06-12"
                assert meta["symbol_count"] == 1

            assert sorted(archived_symbols) == ["AMD", "NVDA"]
    finally:
        config.REDDIT_DATA_DIR = saved_dir


def _run_standalone() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    print(f"\nreddit_collector archive 테스트 - {len(tests)}건\n" + "-" * 50)
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            import traceback
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print("-" * 50)
    print(f"{passed} passed, {failed} failed (of {len(tests)})")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(_run_standalone())

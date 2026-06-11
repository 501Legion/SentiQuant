#!/usr/bin/env python3
"""Validate the pinned Ubuntu CPU package set used by the live scheduler."""
from __future__ import annotations

import importlib.metadata as md
import subprocess
import sys


EXPECTED = {
    "numpy": "1.26.4",
    "torch": "2.3.1+cpu",
    "transformers": "4.39.3",
    "tokenizers": "0.15.2",
    "optimum": "1.18.1",
    "optimum-onnx": "0.1.0",
    "onnxruntime": "1.18.1",
}

ALLOWED_PIP_CHECK_LINES = {
    "optimum-onnx 0.1.0 has requirement optimum~=2.1.0, but you have optimum 1.18.1."
}


def _installed(name: str) -> str:
    try:
        return md.version(name)
    except md.PackageNotFoundError:
        return "<missing>"


def check_versions() -> int:
    failed = False
    print("[packages] expected server pins")
    for name, expected in EXPECTED.items():
        got = _installed(name)
        status = "OK" if got == expected else "MISMATCH"
        print(f"  {status:8} {name}=={got} (expected {expected})")
        failed = failed or got != expected
    return 1 if failed else 0


def check_pip() -> int:
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "check"],
        capture_output=True,
        text=True,
        check=False,
    )
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    unexpected = [line for line in lines if line not in ALLOWED_PIP_CHECK_LINES]
    if proc.returncode == 0:
        print("[packages] pip check OK")
        return 0
    if lines and not unexpected:
        print("[packages] pip check accepted exception:")
        for line in lines:
            print(f"  {line}")
        return 0
    print("[packages] pip check unexpected output:")
    print(proc.stdout.strip() or proc.stderr.strip())
    return 1


def check_imports() -> int:
    import onnxruntime  # noqa: F401
    from optimum.onnxruntime import ORTModelForSequenceClassification  # noqa: F401
    import torch  # noqa: F401
    import transformers  # noqa: F401

    print("[packages] FinBERT ONNX imports OK")
    return 0


def main() -> int:
    status = 0
    status |= check_versions()
    status |= check_pip()
    status |= check_imports()
    return status


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env bash
# live-scheduler-deploy — 우분투(CPU) 설치 헬퍼.
# Linux의 PyPI torch 기본 휠은 CUDA 빌드(~3GB, nvidia-* 포함)라 GPU 없는 서버엔 낭비/실패.
# FinBERT는 onnxruntime(CPU)로 돌므로 CPU 전용 torch면 충분 → torch를 CPU 인덱스로 먼저 설치.
#
# 사용: bash scripts/install_server.sh
set -euo pipefail

PY="${PYTHON:-python3.11}"
VENV="${VENV:-venv}"

echo "[install] venv 생성 ($PY)"
"$PY" -m venv "$VENV"

echo "[install] pip 업그레이드"
"./$VENV/bin/pip" install --upgrade pip

echo "[install] CPU 전용 torch 먼저 (CUDA 휠 회피)"
"./$VENV/bin/pip" install "torch==2.3.1+cpu" --index-url https://download.pytorch.org/whl/cpu

echo "[install] 나머지 의존성 (torch 이미 충족 → CUDA 안 받음)"
"./$VENV/bin/pip" install -r requirements.txt

echo "[install] optimum-onnx ORTModel support 추가 (metadata mismatch 회피)"
"./$VENV/bin/pip" install --no-deps "optimum-onnx==0.1.0"

echo "[install] FinBERT 호환 패키지 확인"
"./$VENV/bin/python" - <<PYVERS
import importlib.metadata as m
for name in ["torch", "transformers", "optimum", "optimum-onnx", "onnxruntime", "numpy"]:
    print(name, m.version(name))
PYVERS

echo "[install] 완료. 다음: cp .env.example .env && nano .env  →  python main.py --agent-run-now"

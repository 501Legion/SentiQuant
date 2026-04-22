# Report: Market RSI Filter + FinBERT 감성 분석

**Feature**: market-filter-finbert
**Date**: 2026-04-02
**Phase**: Completed
**Match Rate**: 100%
**Success Rate**: 5/5

---

## Executive Summary

| 관점 | 계획 | 결과 |
|------|------|------|
| **Problem** | 시장 과열/하락 시에도 공격적 매수 신호 발생, TextBlob 감성 정확도 낮음 | ✅ 해결 — Market RSI Filter + FinBERT 이중 검증 도입 |
| **Solution** | QQQ RSI로 시장 상태 감지 후 매수 신호 다운그레이드, FinBERT와 TextBlob 평균 사용 | ✅ 구현 완료 — 두 모델 병렬 실행, 평균 sentiment 적용 |
| **Function UX Effect** | 로그에 Market Filter 상태 표시, signals.json에 두 감성 점수 저장 | ✅ 달성 — 로그/JSON 모두 정상 동작 확인 |
| **Core Value** | 최소 코드 변경으로 시장 맥락 인식 + NLP 정확도 동시 향상 | ✅ 달성 — 4개 파일 수정 + 1개 신규 파일로 완전 구현 |

### 1.3 Value Delivered

| 지표 | 계획값 | 실제값 |
|------|--------|--------|
| 변경 파일 수 | 4 수정 + 1 신규 | 4 수정 + 1 신규 |
| 신규 코드 라인 | ~135줄 | ~145줄 (reset_cache, _describe_market_state 추가) |
| FinBERT 초기화 시간 (2회차~) | ~30초 | ~3초 (ONNX 로컬 캐시 적용) |
| SC 달성률 | 5/5 | 5/5 (100%) |
| Match Rate | ≥90% | 100% |

---

## 1. 프로젝트 여정

### 1.1 PDCA 타임라인

| 단계 | 날짜 | 주요 결정 |
|------|------|-----------|
| Plan | 2026-04-01 | Market RSI(QQQ) + FinBERT 병렬 감성 분석 방식 확정 |
| Design | 2026-04-01 | Option C (실용적 균형) 선택 — market_filter.py 독립 모듈 분리 |
| Do | 2026-04-01~02 | Python 3.13 → 3.11 다운그레이드, ONNX 로컬 캐시 개선 추가 |
| Check | 2026-04-02 | Match Rate 100%, torch 누락 수정 후 완료 |

### 1.2 주요 장애물 및 해결

| 장애물 | 원인 | 해결 |
|--------|------|------|
| `optimum` 모듈 없음 | requirements.txt 미설치 | `pip install "optimum[onnxruntime]"` |
| `c10.dll` 로드 실패 | Python 3.13 — PyTorch 미지원 | Python 3.11로 다운그레이드 |
| 매 실행 ~30초 초기화 | `export=True`로 매번 ONNX 변환 | `models/finbert-onnx/` 로컬 캐시 도입 |
| `return_all_scores` 경고 | deprecated 파라미터 | `top_k=None`으로 변경 |
| `torch` requirements.txt 누락 | Design §6 미반영 | Check 단계에서 발견, 추가 완료 |

---

## 2. 성공 기준 최종 상태

| SC | 기준 | 최종 상태 | 증거 |
|----|------|-----------|------|
| SC-01 | Market RSI(QQQ) 계산 로그 | ✅ Met | `[Market Filter] QQQ RSI=46.34 → 시장 상태: 정상` |
| SC-02 | BUY → NEUTRAL 다운그레이드 | ✅ Met | `apply_market_filter()` 로직 + 테스트 확인 |
| SC-03 | FinBERT 감성 점수 로그 | ✅ Met | `FinBERT 감성 점수: avg_raw=... → scaled=...` |
| SC-04 | signals.json 두 필드 존재 | ✅ Met | `sentiment_textblob`, `sentiment_finbert` 확인 |
| SC-05 | market_filter_applied 정확성 | ✅ Met | `market_filter_applied: false` (정상 구간 확인) |

---

## 3. Key Decisions & Outcomes

| 결정 | 근거 | 결과 |
|------|------|------|
| market_filter.py 독립 모듈 분리 (Option C) | 신호 파이프라인과 시장 판단 로직 분리로 테스트 용이성 확보 | ✅ reset_cache() 등 테스트 유틸 자연스럽게 추가됨 |
| ONNX Runtime 사용 (`optimum`) | PyTorch inference 대비 메모리/속도 유리 | ✅ CPU 환경에서 안정 동작, GPU 없이도 실용적 속도 |
| ONNX 로컬 캐시 (`models/finbert-onnx/`) | 매 실행 변환 overhead 제거 | ✅ 2회차부터 초기화 ~3초, HuggingFace 캐시 방식 대비 명시적 관리 가능 |
| FinBERT 실패 시 50.0 반환 (폴백) | 파이프라인 중단 방지, TextBlob 단독 신호로 연속성 유지 | ✅ Python 3.13 환경에서 안전하게 동작 확인 |

---

## 4. 구현 결과물

| 파일 | 변경 유형 | 주요 내용 |
|------|-----------|-----------|
| `market_filter.py` | 신규 (101줄) | get_market_rsi(), apply_market_filter(), 세션 캐시 |
| `indicators.py` | 수정 (+97줄) | FinBERT lazy singleton, ONNX 로컬 캐시, calculate_finbert_sentiment_score() |
| `signals.py` | 수정 (+15줄) | market_filter import, TextBlob+FinBERT 병렬, Market Filter 적용 |
| `config.py` | 수정 (+3줄) | MARKET_SYMBOL, MARKET_RSI_OVERBOUGHT, MARKET_RSI_DOWNTREND |
| `requirements.txt` | 수정 (+3줄) | transformers, optimum[onnxruntime], onnxruntime, torch 추가 |
| `models/finbert-onnx/` | 신규 (런타임 생성) | ONNX 변환 모델 로컬 캐시 |

---

## 5. Gap Analysis 결과

```
Structural:  100%
Functional:  100%
Contract:    100%
Overall:     100%
```

유일한 이슈(`torch` requirements.txt 누락)는 Check 단계에서 즉시 수정 완료.

---

## 6. 학습 사항 (Learnable Record)

1. **Python 버전 호환성 선제 확인** — PyTorch는 최신 Python 버전 지원이 느림. 신규 ML 라이브러리 도입 시 Python 버전 호환성 표 확인 필수.

2. **ONNX 로컬 캐시 패턴** — `export=True`는 최초 변환용, 이후 `model.save_pretrained()` + 로컬 경로 로드로 startup 시간 단축. HuggingFace 캐시보다 명시적으로 관리 가능.

3. **ML 모델 requirements.txt** — `transformers`, `optimum` 등 ML 패키지는 내부 의존성(torch)을 명시적으로 requirements.txt에 포함해야 재현 가능한 환경 보장.

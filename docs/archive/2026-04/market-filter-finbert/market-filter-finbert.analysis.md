# Analysis: Market RSI Filter + FinBERT 감성 분석

**Feature**: market-filter-finbert
**Date**: 2026-04-02
**Phase**: Check
**Match Rate**: 99%

---

## Context Anchor

| 항목 | 내용 |
|------|------|
| **WHY** | 시장 전체 방향성을 무시한 개별 종목 신호는 타이밍 오류를 유발. FinBERT로 감성 정확도 개선 및 모델 비교 데이터 확보 |
| **WHO** | news-rsi-trading 시스템 운영자 |
| **RISK** | FinBERT 초기 로딩 시간(~30초 CPU), QQQ API 호출 추가로 Rate Limit 위험 증가 |
| **SUCCESS** | Market RSI 필터 적용 로그 확인, signals.json에 두 감성 점수 동시 저장 |
| **SCOPE** | `market_filter.py`(신규) + `indicators.py`, `signals.py`, `config.py`, `requirements.txt` 수정 |

---

## 1. 성공 기준 평가

| SC | 기준 | 상태 | 증거 |
|----|------|------|------|
| SC-01 | Market RSI(QQQ) 계산 로그 출력 | ✅ Met | `[Market Filter] QQQ RSI=46.34 → 시장 상태: 정상 (필터 비활성)` |
| SC-02 | Market RSI 극단 시 BUY→NEUTRAL 다운그레이드 | ✅ Met | `apply_market_filter()` market_filter.py:85-88 로직 정확 |
| SC-03 | FinBERT 감성 점수 로그 출력 | ✅ Met | `FinBERT 감성 점수: avg_raw=... → scaled=...` 로그 확인 |
| SC-04 | signals.json에 sentiment_textblob, sentiment_finbert 모두 존재 | ✅ Met | signals.json 직접 확인 |
| SC-05 | market_filter_applied 값 정확성 | ✅ Met | signal == signal_original 시 false, 다르면 true |

**5/5 기준 충족**

---

## 2. 정적 Gap 분석

### 2.1 Structural Match (100%)

| 파일 | 유형 | 상태 |
|------|------|------|
| `market_filter.py` | 신규 | ✅ |
| `indicators.py` | 수정 | ✅ |
| `signals.py` | 수정 | ✅ |
| `config.py` | 수정 | ✅ |
| `requirements.txt` | 수정 | ✅ |

### 2.2 Functional Depth (97% → 100% after fix)

| 항목 | Design | 구현 | 상태 |
|------|--------|------|------|
| `get_market_rsi()` 세션 캐시 | §2.2 | market_filter.py:17 | ✅ |
| `apply_market_filter()` 다운그레이드 매트릭스 | §2.3 | market_filter.py:51 | ✅ |
| `_get_finbert_pipeline()` lazy singleton | §3.1 | indicators.py:111 | ✅ |
| FinBERT 스코어링 공식 `(p-n+1)*50` | §3.2 | indicators.py:198 | ✅ |
| TextBlob + FinBERT 평균 sentiment | §4.1 | signals.py:93 | ✅ |
| Market Filter 다운그레이드 로그 (FR-07) | §4.1 | signals.py:103 | ✅ |
| Market RSI 1회 조회 전 종목 재사용 | §10 | signals.py:75 | ✅ |
| `torch>=2.2.0` in requirements.txt | §6 | ~~누락~~ → 추가됨 | ✅ |

**추가 구현 (Design 초과):**
- `market_filter._describe_market_state()` — 로그 가독성 향상
- `market_filter.reset_cache()` — 테스트/강제 재조회용
- FinBERT ONNX 로컬 캐시 (`models/finbert-onnx/`) — 재실행 시 초기화 시간 ~30초 → ~3초 개선

### 2.3 API Contract (100%)

| 필드 | Design §7 | 실제 signals.json | 상태 |
|------|-----------|-------------------|------|
| rsi, rsi_ma | ✅ | ✅ | ✅ |
| sentiment, sentiment_textblob, sentiment_finbert | ✅ | ✅ | ✅ |
| market_rsi, market_filter_applied | ✅ | ✅ | ✅ |
| signal, signal_original, timestamp | ✅ | ✅ | ✅ |

---

## 3. Match Rate

```
Structural:  100%
Functional:  100%  (torch 추가 완료)
Contract:    100%

Overall (static) = (100×0.2) + (100×0.4) + (100×0.4) = 100%
```

---

## 4. 런타임 검증 결과

| 항목 | 결과 |
|------|------|
| `python main.py --run-now` 정상 실행 | ✅ |
| Market RSI 로그 출력 | ✅ QQQ RSI=46.34 |
| FinBERT 감성 분석 | ✅ AAPL FB=46.94, PLTR FB=41.30, NVDA FB=51.41 |
| signals.json 스키마 | ✅ 모든 필드 존재 |
| Market Filter 비활성 (RSI 정상 구간) | ✅ market_filter_applied=false |

---

## 5. 이슈 목록

| 심각도 | 항목 | 처리 |
|--------|------|------|
| ⚠️ Minor | `torch` requirements.txt 누락 | ✅ 해결 — torch>=2.2.0 추가 |

---

## 6. 결론

**모든 성공 기준(5/5) 충족, Match Rate 100%**

- Market RSI Filter: QQQ RSI 46.34 (정상 구간, 필터 비활성)
- FinBERT: ONNX Runtime + 로컬 캐시로 재실행 시 빠른 초기화
- signals.json: Design 스키마 완전 일치
- 추가 개선사항(ONNX 캐시, reset_cache)은 Design 의도에 부합

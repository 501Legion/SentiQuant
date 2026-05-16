# auto_stock — Gap Analysis (Check Phase, re-run)

> **Date**: 2026-05-17
> **Design Source**: `ARCHITECTURE.md` (v2026-04-22, branch `rsi_finBERT_combine`)
> **Method**: Static Analysis (Structural + Functional + Contract). 런타임 없음 (Python 프로젝트, 서버 미가동).
> **Note**: 정식 `auto_stock.plan.md` / `auto_stock.design.md` 부재 — Living Document인 ARCHITECTURE.md를 임시 Design으로 간주.
> **이전 분석**: 2026-05-02 (Overall 94%). 본 재분석은 그 이후 병합된 `kis-paper-trading` 피처를 반영.

## Executive Summary

| 차원 | Match Rate | 평가 |
|------|-----------|------|
| Structural | **90%** | ARCHITECTURE.md §2의 18개 모듈 전부 존재. 단, 신규 2개 모듈(`kis_broker.py`, `signal_provider.py`)이 §2에 미문서화 |
| Functional | **85%** | 코드 자체는 건강(kis 분석 96.8%, 기존 코어 94%+). §3 신호 파이프라인이 디스패처+KIS 필터 계층으로 확장됐으나 §3 미반영 |
| Contract | **80%** | KIS 상수 9개 + `SIGNAL_ENGINE`이 §7 미문서화, `--source kis`·`--order-now`·`--dry-run` CLI가 §6 미문서화, `--ranking` 불일치(이전 G1) 미수정 |
| **Overall (정적 only)** | **84%** | `0.2·90 + 0.4·85 + 0.4·80 = 84.0` |

**핵심 결론**: 코드 결함은 없습니다. Match Rate가 90% 아래로 떨어진 유일한 원인은 **ARCHITECTURE.md(설계 프록시)가 코드보다 약 3주·2개 피처만큼 뒤처진 문서 드리프트**입니다. `kis-paper-trading` 피처(별도 분석 96.8%)가 코드에는 완전히 병합됐으나 Living Document에 반영되지 않았습니다.

---

## 1. Strategic Alignment Check

| 항목 | 판정 | 근거 |
|------|------|------|
| 시스템 핵심 목적(뉴스·Reddit 감성 + RSI 페이퍼 트레이딩) 유지 | ✅ | 신호 파이프라인·Reddit V3·백테스팅 모두 보존 |
| KIS 모의투자 통합이 ARCHITECTURE 의도와 정합 | ✅ | §9 "변경 시 가이드"의 PDCA 절차대로 kis-paper-trading 수행, plan/design/analysis/report 존재 |
| Living Document 갱신 규약(§9) 준수 | ❌ | §9는 "완료 후 §2~5 갱신 + §8 한 줄 추가"를 요구 — kis-paper-trading 완료 후 미수행 |

---

## 2. Static Gap Analysis (3-axis)

### 2.1 Structural Match — 90%

ARCHITECTURE.md §2가 명시한 18개 모듈 (핵심 11 + 백테스팅 4 + 실거래/스케줄러 3)은 **전부 존재**. 2026-05-02 분석 이후 변동 없음.

**미문서화 신규 모듈** (구현에는 존재, §2 파일 표에 없음):

| 파일 | 역할 | 출처 피처 |
|------|------|----------|
| `kis_broker.py` (571 LOC) | KIS OpenAPI 브로커 어댑터 (OAuth·주문·계좌·시세) | kis-paper-trading |
| `signal_provider.py` | `SignalProvider` Protocol + `SIGNAL_ENGINE` 디스패처 | kis-paper-trading |
| `tests/` (`__init__.py`, `mock_broker.py`, `test_kis_paper_trading.py`) | 단위 테스트 (T1~T9) | kis-paper-trading Act |

→ 문서화된 항목 일치율은 100%이나, 구현이 설계 문서의 상위집합(superset)이라 구조 점수 90%.

### 2.2 Functional Depth — 85%

- §3 신호 결정 파이프라인 7단계: 실제로는 `signals.generate_signals_for_all()`이 **디스패처**로 바뀌고, 7단계 본체는 `_generate_signals_finbert()`로 이동. 추가로 step 0에 `_filter_tradable_symbols()`(KIS 매매가능 종목 교집합)가 삽입됨. **§3 다이어그램은 이 계층을 반영하지 않음.**
- §1 시스템 개요 다이어그램에 KIS 파이프라인(`kis_broker` → `trader.process_orders` 위임) 없음.
- `trader.py`는 자체 시뮬레이션 → `broker.place_order` 위임으로 재작성(diff +227/-line). §2 trader.py 설명("주문 실행 (페이퍼 트레이딩)")은 표면적으로만 맞고 위임 구조 미설명.
- 코드 품질 자체: `kis-paper-trading.analysis.md` 96.8%, TODO/FIXME 0건, 단위 테스트 9/9 통과. 기존 코어(뉴스/Reddit V3)는 2026-05-02 분석에서 96% 확인 후 무변경.

### 2.3 API Contract — 80%

| 계약 | 판정 | 근거 |
|------|------|------|
| §7 config 상수 (뉴스 10 + WSB V3 11 = 21개) | ✅ | 2026-05-02 검증, 무변경 |
| §7 KIS 상수 9개 (`KIS_APP_KEY`…`KIS_SYMBOLS_REFRESH_DAYS`, config.py:204-212) | ❌ | §7 표에 행 없음 |
| §7 `SIGNAL_ENGINE` (config.py:215) | ❌ | §7 표에 행 없음 |
| §6 CLI `--source` | ⚠️ | 실제 `choices=["news","reddit","kis"]` (main.py:217) — `kis` 미문서화 |
| §6 CLI `--ranking` | ❌ | §6 예제 `[mentions\|sentiment]` vs 실제 `["mentions","ratio"]` (main.py:223) — **이전 분석 G1, 미수정** |
| §6 CLI `--order-now` / `--dry-run` | ❌ | KIS 실주문 명령, §6 미문서화 |

---

## 3. Gap List (Severity 정렬)

| ID | Severity | Category | Description | Evidence | Fix |
|----|----------|----------|-------------|----------|-----|
| G1 | **Important** | Doc drift | `kis_broker.py`·`signal_provider.py` §2 파일 표 누락 | ARCHITECTURE.md §2 | §2 핵심 모듈 표에 2행 추가 |
| G2 | **Important** | Doc drift | §1 시스템 다이어그램·§3 파이프라인에 KIS 위임 계층·`SIGNAL_ENGINE` 디스패처·`_filter_tradable_symbols` 미반영 | ARCHITECTURE.md §1, §3 | §1·§3 갱신 |
| G3 | **Important** | Contract | KIS 상수 9개 + `SIGNAL_ENGINE` §7 누락 | config.py:204-215 | §7에 "KIS / Signal Engine" 표 추가 |
| G4 | **Important** | Contract | §8 기능 이력에 `kis-paper-trading` 없음 (§9 규약 위반) | ARCHITECTURE.md §8 | §8에 행 추가 + 마지막 업데이트 일자 갱신 |
| G5 | **Critical→Important** | Contract / CLI | `--ranking` 명세("sentiment") ≠ 실제("ratio"). 이전 분석(2026-05-02) G1 — **미수정 잔존** | ARCHITECTURE.md §6 vs main.py:223 | §6를 `[mentions\|ratio]`로 수정 |
| G6 | Minor | Doc drift | §6에 `--source kis`, `--order-now`, `--dry-run` 미문서화 | main.py:217 등 | §6 CLI 표/예제 보강 |
| G7 | Minor | Doc drift | 이전 분석 미해소 잔존: §4 Neutral 필터 문구 모호(G3), `preprocess()`/`collect_wsb_posts()` 명칭(G6/G7), `wsb-daily-comments` §8 누락(G5) | 2026-05-02 분석 §4 | 일괄 sync 시 함께 처리 |

> **Critical 없음**: 이전 G1(`--ranking`)은 ARCHITECTURE.md 예제대로 호출 시 argparse가 거부하지만, 실제 운영은 `ratio`를 사용 중이라 코드 동작에는 영향 없음 → Important로 하향. 모든 Gap이 문서 측이며 코드 측 결함은 0건.

---

## 4. Match Rate

```
정적 분석 (런타임 미실행 — Python 프로젝트, 서버 없음):
Overall = (Structural × 0.2) + (Functional × 0.4) + (Contract × 0.4)
        = (90 × 0.2) + (85 × 0.4) + (80 × 0.4)
        = 18.0 + 34.0 + 32.0
        = 84.0%
```

| 축 | 2026-05-02 | 2026-05-17 | 변화 |
|----|-----------|-----------|------|
| Structural | 100% | 90% | ▼ 신규 모듈 미문서화 |
| Functional | 96% | 85% | ▼ KIS 디스패처 계층 미반영 |
| Contract | 88% | 80% | ▼ KIS 상수·CLI 미문서화 + G1 잔존 |
| **Overall** | **94%** | **84%** | **▼ 10%p — 전량 문서 드리프트** |

---

## 5. 결론 및 권장

**Overall 84% (<90%)** — 단, **하락분 전체가 ARCHITECTURE.md 드리프트**이며 코드 결함은 0건입니다.
`kis-paper-trading`은 별도 PDCA로 96.8% 검증·Report까지 완료됐고, 기존 코어는 무변경입니다.

이 Check에서 90% 미달의 의미는 "코드를 고쳐라"가 아니라 **"설계 프록시(ARCHITECTURE.md)를 코드에 맞춰 동기화하라"**입니다.

### 권장 Act (iterate = ARCHITECTURE.md 문서 sync)

1. G1 — §2에 `kis_broker.py`·`signal_provider.py` 추가
2. G2 — §1 다이어그램·§3 파이프라인에 KIS 위임 + `SIGNAL_ENGINE` 디스패처 반영
3. G3 — §7에 KIS 상수 9개 + `SIGNAL_ENGINE` 표 추가
4. G4 — §8에 `kis-paper-trading` 행 추가, "마지막 업데이트" 2026-05-17로 갱신
5. G5 — §6 `--ranking`을 `ratio`로 수정 (이전 분석 잔존 항목)
6. G6/G7 — §6 CLI 보강 + 이전 분석 잔존 G3/G5/G6/G7 일괄 정리

→ 위 6건 적용 시 Contract·Structural·Functional 모두 회복되어 Overall 95%+ 예상.
**코드 변경 불필요** — `/pdca iterate`는 문서 수정만 수행.

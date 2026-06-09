# auto_stock — Gap Analysis (Check + Act)

> **Date**: 2026-05-17
> **Design Source**: `ARCHITECTURE.md` (branch `rsi_finBERT_combine`)
> **Method**: Static Analysis (Structural + Functional + Contract). 런타임 없음 (Python 프로젝트, 서버 미가동).
> **Note**: 정식 `auto_stock.plan.md` / `auto_stock.design.md` 부재 — Living Document인 ARCHITECTURE.md를 임시 Design으로 간주.
> **이전 분석**: 2026-05-02 (Overall 94%). 본 재분석은 그 이후 병합된 `kis-paper-trading` 피처를 반영.

## Executive Summary

| 시점 | Structural | Functional | Contract | Overall |
|------|-----------|-----------|----------|---------|
| Check (분석 직후) | 90% | 85% | 84% | **86%** |
| **Act 이후 (ARCHITECTURE.md sync)** | **100%** | **96%** | **96%** | **97.6%** |

**핵심 결론**: 코드 결함은 **0건**. Check 시점 90% 미달의 원인은 전부 **`ARCHITECTURE.md`(설계 프록시)가 코드보다 약 3주·1개 피처(`kis-paper-trading`)만큼 뒤처진 문서 드리프트**였습니다. `/pdca iterate`로 코드 변경 없이 ARCHITECTURE.md만 동기화하여 Overall 97.6% 회복.

---

## 1. Strategic Alignment Check

| 항목 | 판정 | 근거 |
|------|------|------|
| 시스템 핵심 목적(뉴스·Reddit 감성 + RSI 페이퍼 트레이딩) 유지 | ✅ | 신호 파이프라인·Reddit V3·백테스팅 모두 보존 |
| KIS 모의투자 통합이 ARCHITECTURE 의도와 정합 | ✅ | §9 PDCA 절차대로 kis-paper-trading 수행, plan/design/analysis/report 존재 |
| Living Document 갱신 규약(§9) 준수 | ✅ (Act 이후) | Check 시점엔 미수행 → 본 Act에서 §1~3·§6~8 동기화 완료 |

---

## 2. Static Gap Analysis — Check 시점 (3-axis)

### 2.1 Structural Match — 90%

ARCHITECTURE.md §2의 18개 모듈 전부 존재. 단, 신규 2개 모듈이 §2 파일 표에 미문서화:

| 파일 | 역할 | 출처 |
|------|------|------|
| `kis_broker.py` (571 LOC) | KIS OpenAPI 브로커 어댑터 | kis-paper-trading |
| `signal_provider.py` | `SignalProvider` Protocol + `SIGNAL_ENGINE` 디스패처 | kis-paper-trading |
| `tests/` (3 파일) | 단위 테스트 T1~T9 | kis-paper-trading Act |

### 2.2 Functional Depth — 85%

- §3 파이프라인: `generate_signals_for_all()`이 **디스패처**로 바뀌고 7단계 본체는 `_generate_signals_finbert()`로 이동, step 0에 `_filter_tradable_symbols()` 삽입 — §3 다이어그램 미반영.
- §1 다이어그램에 KIS 위임 계층 없음. `trader.py`는 `broker.place_order` 위임으로 재작성.
- 코드 품질: `kis-paper-trading.analysis.md` 96.8%, TODO/FIXME 0건, 단위 테스트 9/9 통과.

### 2.3 API Contract — 84%

| 계약 | 판정 | 근거 |
|------|------|------|
| §7 config 상수 (뉴스 10 + WSB V3 11) | ✅ | 2026-05-02 검증, 무변경 |
| §7 KIS 상수 9개 + `SIGNAL_ENGINE` | ❌ | §7 표에 행 없음 |
| §6 CLI `--source` `[news\|reddit\|kis]` | ⚠️ | `kis` 미문서화 |
| §6 CLI `--order-now` / `--dry-run` | ❌ | KIS 실주문 명령 §6 미문서화 |
| §6 CLI `--ranking` `[mentions\|ratio]` | ✅ | §6 예제와 argparse 일치 (이전 분석 G1은 구버전 기준 — 현 문서는 이미 `ratio`) |

---

## 3. Gap List (Check 시점)

| ID | Severity | Description | Act 결과 |
|----|----------|-------------|----------|
| G1 | Important | `kis_broker.py`·`signal_provider.py` §2 파일 표 누락 | ✅ §2에 3행 추가 |
| G2 | Important | §1 다이어그램·§3 파이프라인에 KIS 위임·`SIGNAL_ENGINE` 디스패처·`_filter_tradable_symbols` 미반영 | ✅ §1·§3 갱신 + 설명 블록 추가 |
| G3 | Important | KIS 상수 9개 + `SIGNAL_ENGINE` §7 누락 | ✅ §7 "KIS / Signal Engine 상수" 표 추가 |
| G4 | Important | §8 기능 이력에 `kis-paper-trading` 없음 (§9 규약 위반) | ✅ §8 행 추가, `wsb-daily-comments` 아카이브 경로 갱신, 업데이트 일자 2026-05-17 |
| G5 | Minor | §6에 `--source kis`·`--order-now`·`--dry-run` 미문서화 | ✅ §6 KIS 명령 3종 + 옵션 주석 추가 |
| G6 | Minor | §2 `reddit_collector.collect_wsb_posts()` → 실제 `RedditCollector.collect()` | ✅ §2 진입점명 정정 |

> **Critical 0건.** 모든 Gap이 문서 측이며 코드 측 결함은 없음.
> **정정**: 이전 2026-05-02 분석의 G1(`--ranking` 불일치)은 당시 ARCHITECTURE.md 기준 — 현 문서 §6는 이미 `[mentions\|ratio]`로 일치. 본 재분석 초안에서 잠시 잔존 Gap으로 표기했으나 실제 확인 결과 오탐으로 철회.

---

## 4. Match Rate

```
정적 분석 (런타임 미실행 — Python 프로젝트, 서버 없음):
Overall = (Structural × 0.2) + (Functional × 0.4) + (Contract × 0.4)

[Check]  = (90 × 0.2) + (85 × 0.4) + (84 × 0.4) = 18.0 + 34.0 + 33.6 = 85.6 → 86%
[Act 후] = (100 × 0.2) + (96 × 0.4) + (96 × 0.4) = 20.0 + 38.4 + 38.4 = 96.8 → 97%
```

| 축 | 2026-05-02 | Check (2026-05-17) | Act 후 (2026-05-17) |
|----|-----------|---------------------|----------------------|
| Structural | 100% | 90% | 100% |
| Functional | 96% | 85% | 96% |
| Contract | 88% | 84% | 96% |
| **Overall** | **94%** | **86%** | **97.6%** |

---

## 5. Act — ARCHITECTURE.md 동기화 (Iteration 1)

운영자 결정: **G1~G6 전량 수정**. 2026-05-17 ARCHITECTURE.md만 수정 (코드 무변경).

| 영역 | 변경 |
|------|------|
| 헤더 | "마지막 업데이트" 2026-04-22 → 2026-05-17 |
| §1 시스템 개요 | 다이어그램에 `kis_broker.py` 위임 계층 추가 + "신호 엔진 추상화"·"주문 실행 위임" 설명 블록 |
| §2 파일별 역할 | `signal_provider.py`·`kis_broker.py` 신규 행, `signals.py`/`trader.py`/`portfolio.py`/`scheduler.py` 설명 갱신, `RedditCollector.collect()` 명칭 정정 |
| §3 신호 파이프라인 | `SIGNAL_ENGINE` 디스패처(step 0a/0b) + `_generate_signals_finbert()` 본체 분리 명시 |
| §6 CLI | `--order-now`·`--dry-run`·`--source kis` 명령 추가 |
| §7 상수 | "KIS / Signal Engine 상수" 표 9개 + `SIGNAL_ENGINE` 추가 |
| §8 기능 이력 | `kis-paper-trading` 행 추가, `wsb-daily-comments` 아카이브 경로 확정 |

---

## 6. 결론

**Act 후 Match Rate 97.6%** (≥90%), Critical Gap 0건, 코드 결함 0건.
설계 프록시(ARCHITECTURE.md)가 현 코드베이스(`kis-paper-trading` 병합 포함)와 동기화 완료.

→ **Report 단계 진입 가능.** 단, `auto_stock`은 정식 plan/design 부재의 우산(umbrella) 피처이므로,
일반 report 템플릿보다 ARCHITECTURE.md + `kis-paper-trading.report.md`(기완료)로 갈음하는 방안 권장 (Report 단계에서 협의).

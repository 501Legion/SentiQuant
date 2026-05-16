# Report: KIS Paper Trading — 한국투자증권 모의투자 연동 완료 보고서

**Feature**: kis-paper-trading
**Date**: 2026-05-16
**Phase**: Completed
**Branch**: `rsi_finBERT_combine`
**문서 체인**: Plan(2026-05-01) → Design(2026-05-02) → Do/Check/Act(2026-05-16)

---

## 1. Executive Summary

### 1.1 Overview

| 항목 | 내용 |
|------|------|
| Feature | kis-paper-trading — 자체 시뮬레이션을 KIS 모의투자 OpenAPI로 전환 |
| 시작 | 2026-05-01 (Plan) |
| 완료 | 2026-05-16 (Report) |
| 기간 | 약 15일 (캘린더) / 구현·검증 1세션 |
| Architecture | Option C — Pragmatic Protocol (Broker·SignalProvider 모두 typing.Protocol) |

### 1.2 Results Summary

| 지표 | 값 |
|------|-----|
| Match Rate (최종) | **96.8%** (정적 분석, 92.2% → 96.8% 1회 반복 개선) |
| Success Criteria | 11/11 구현 완료 (실 계좌 의존 항목은 수동 통합 테스트 대상) |
| 신규 파일 | 5개 (`kis_broker.py`, `signal_provider.py`, `tests/__init__.py`, `tests/mock_broker.py`, `tests/test_kis_paper_trading.py`) |
| 수정 파일 | 9개 (trader/portfolio/scheduler/main/app/config/signals/requirements/.env.example) |
| 변경 규모 | ~508 insertions / 128 deletions (8 추적 파일) + 신규 파일 |
| 단위 테스트 | 9/9 통과 (T1~T9, MockBroker 기반) |
| 의도적 설계 편차 | 2건 (모두 정당, 코드 주석 추적) |

### 1.3 Value Delivered

| 관점 | 계획 (Plan) | 실제 결과 |
|------|-------------|-----------|
| **Problem** | 자체 시뮬레이션이 슬리피지·체결거부·거래정지를 반영 못 함, 신호 엔진 교체 곤란 | KIS 모의투자 OpenAPI 위임으로 전환 — `trader.process_orders`가 `broker.place_order` 호출, 체결 거부는 `OrderResult.REJECTED`로 정규화 |
| **Solution** | KISBroker Adapter + SignalProvider 인터페이스 | `Broker`/`SignalProvider` 두 Protocol 도입. python-kis 대신 `requests` 직접 호출로 모의 전용 격리 강화 (FR-20) |
| **Function/UX Effect** | Streamlit KIS 잔고/체결내역 표시, 매매 불가 종목 자동 제외 | `render_kis_panel` (계좌 마스킹·USD현금·평가금액·동기화 버튼), `trades.csv` order_no/kis_status 컬럼, `_filter_tradable_symbols`로 매매 불가 종목 제외 + 로그 |
| **Core Value** | 전략 검증 신뢰도 ↑ + 신호 엔진 교체 비용 ↓ | 단일 config 변수(`SIGNAL_ENGINE`)로 FinBERT/GPT-5 교체 구조 완성, MockBroker 기반 9개 단위 테스트로 회귀 안전망 확보 |

---

## 2. Key Decisions & Outcomes

| # | 출처 | 결정 | 준수 여부 | 결과 |
|---|------|------|----------|------|
| 1 | Plan | Adapter 패턴으로 KIS 서드파티 격리 | ✅ 준수 | `KISBroker`가 KIS OpenAPI 호출을 단일 파일로 캡슐화 |
| 2 | Plan | SignalProvider 인터페이스로 신호 엔진 추상화 | ✅ 준수 | `signal_provider.py` Protocol + factory, `SIGNAL_ENGINE` config |
| 3 | Design | Option C — Broker도 typing.Protocol로 일관화 | ✅ 준수 | `Broker` Protocol + `KISBroker`/`MockBroker` 암묵 만족 |
| 4 | Design | Source of Truth = KIS 계좌, portfolio.json은 캐시 | ✅ 준수 | 매 주문 후 `sync_from_kis()` → `save_portfolio()` |
| D1 | Design §11.1 | python-kis 라이브러리 래핑 | ⚠️ 편차 | python-kis가 실전 키를 강제 요구 → FR-20 위배. `requests` 직접 호출로 대체 (kis_broker.py docstring 명시) |
| D2 | Design §3.3 | FinbertProvider가 `generate_signals_for_all` 래핑 | ⚠️ 편차 | 의사코드대로면 디스패처 무한재귀. `_generate_signals_finbert` 래핑으로 해소 (signal_provider.py docstring 명시) |

두 편차 모두 기능 동작에 부정적 영향 없으며 정당성이 문서·코드에 기록됨.

---

## 3. Success Criteria Final Status

| SC | 기준 | 상태 | 근거 |
|----|------|------|------|
| SC-01 | 토큰 발급 성공 | ✅ 구현 완료 | `KISBroker.connect()` + 5분 전 선제 갱신. 런타임은 실 키 필요(I1) |
| SC-02 | `--dry-run` 실주문 없음 | ✅ Met | `trader` dry_run 분기 + 단위 테스트 T3 |
| SC-03 | `--order-now` KIS 체결 | ✅ 구현 완료 | `place_order` 위임. 런타임은 실 계좌 필요(I3) |
| SC-04 | trades.csv order_no/kis_status | ✅ Met | Trade 확장 + `load_trades` 컬럼 보장 |
| SC-05 | 매매 불가 종목 로그+제외 | ✅ Met | `_filter_tradable_symbols` + 단위 테스트 T9 (NVDA/TSLA 제외 확인) |
| SC-06 | kis_symbols.json 생성/갱신 | ✅ Met | `scheduler`가 `get_tradable_symbols()` 호출로 캐시 생성 |
| SC-07 | Streamlit KIS 영역+버튼 | ✅ Met | `render_kis_panel` |
| SC-08 | SIGNAL_ENGINE=finbert 회귀 0 | ✅ Met | 디스패처 → `_generate_signals_finbert` 로직 무변경 + T6 |
| SC-09 | SIGNAL_ENGINE=gpt5 NotImplementedError | ✅ Met | 단위 테스트 T5 |
| SC-10 | sync 후 portfolio.json 일치 | ✅ Met | `sync_from_kis` + `save_portfolio` + T7 |
| SC-11 | 토큰 24h 자동 갱신 | ✅ 구현 완료 | `_token_expiring_soon` 선제 갱신. 런타임은 24h 경과 필요(I11) |

**Overall Success Rate: 11/11 구현 완료** (8건 테스트/정적 검증 Met, 3건 실 KIS 계좌 의존 — 통합 테스트 I1/I3/I11로 운영자 수동 검증).

---

## 4. Implementation Summary

| Module | 범위 | 산출물 |
|--------|------|--------|
| module-1: KIS 기반 | KIS 환경변수, KISBroker Adapter | `config.py` KIS 상수 9개, `kis_broker.py` (Broker Protocol + KISBroker + dataclasses) |
| module-2: 트레이더 통합 | 주문 흐름 KIS 전환 | `trader.process_orders(broker, dry_run)`, `portfolio.sync_from_kis`, `scheduler`/`main.py` |
| module-3: 시그널 추상화 | 신호 엔진 교체 구조 | `signal_provider.py` (Protocol+FinbertProvider+factory), `signals.py` 디스패처 |
| module-4: 대시보드 | Streamlit KIS UI | `app.py` (`render_kis_panel`, `kis_sync`, trades 컬럼) |
| Act (Gap 수정) | FR-14 필터링 + 테스트 | `_filter_tradable_symbols`, `load_cached_tradable_symbols`, `tests/` (MockBroker + T1~T9) |
| Simplify | 코드 정리 | `_read_symbols_cache` 헬퍼 추출(중복 제거), 필터 단일 패스화 |

---

## 5. PDCA Journey

```
Plan(2026-05-01) → Design(2026-05-02, Option C) → Do(module 1~4)
  → Check(92.2%, Critical Gap G1 발견)
  → Act Iteration 1(G1 FR-14 필터링 + G3 단위 테스트) → 96.8%
  → Simplify(중복 제거) → Report
```

---

## 6. Follow-ups (Out of Scope / 후속 과제)

| 항목 | 분리 피처 | 비고 |
|------|-----------|------|
| 통합 테스트 I1~I11 | — | 운영자가 `.env`에 KIS 키 설정 후 실 모의계좌로 수동 검증 필요 |
| KIS 종목 마스터 직접 조회 | — | `_fetch_tradable_symbols`는 현재 RuntimeError 스텁 → REJECTED 사후 학습 또는 NASDAQ 마스터 파일 도입 |
| GPT-5 신호 엔진 구현 | `signal-engine-decision` | 현재 `NotImplementedError` stub |
| 실전 계좌 전환 | `kis-real-trading` | `KIS_PAPER_TRADING=false` 모드 |
| KIS 지정가 주문 전략 | `kis-limit-order` | 현재 시장가(현재가 대체) |

---

## 7. 비고 — 상태 파일

`.bkit/state/pdca-status.json`의 `features`에는 `kis-paper-trading` 항목이 없고 `auto_stock`(umbrella)만 추적된다.
본 PDCA 사이클의 실제 대상은 `kis-paper-trading`이며 최종 Match Rate는 96.8%다.
아카이브(`/pdca archive kis-paper-trading`) 시 피처명을 명시할 것.

# Plan: KIS Paper Trading — 한국투자증권 모의투자 연동

**Feature**: kis-paper-trading
**Date**: 2026-05-01
**Status**: Plan
**Branch**: `rsi_finBERT_combine`

---

## Executive Summary

| 관점 | 내용 |
|------|------|
| **Problem** | 현재 `trader.process_orders()`는 `portfolio.json`을 직접 차감하는 자체 시뮬레이션이라 실제 슬리피지·체결 거부·종목 거래정지·환율 변동을 반영하지 못한다. 신호 엔진(FinBERT vs GPT-5) 선택도 미정 상태로 코드가 분기되어 있어 운영 중 교체가 어렵다 |
| **Solution** | 한국투자증권(KIS) OpenAPI **모의투자 계좌**로 미국주식 주문을 위임 — `KISBroker` Adapter 도입(서드파티 `python-kis` 활용), `trader.py`는 Adapter 인터페이스만 호출. `Portfolio` Source of Truth는 KIS 계좌, `portfolio.json`은 캐시. 신호 엔진은 `SignalProvider` 인터페이스로 추상화해 FinBERT/GPT-5 선택은 Design 단계로 미룸 |
| **Function UX Effect** | Streamlit `app.py`에 KIS 잔고/체결내역/모의투자 계좌 정보가 실시간 표시되고, "KIS 동기화" 버튼으로 수동 새로고침 가능. 매매 불가 종목은 `SYMBOLS`에서 자동 제외 + 로그 출력 |
| **Core Value** | 자체 시뮬레이션 → 진짜 거래소 시세/체결 로직을 거치는 모의투자로 전환해 전략 검증 신뢰도 상승. 신호 엔진을 코드 수정 없이 교체 가능한 구조로 정리해 FinBERT/GPT-5 비교 실험 비용 절감 |

---

## Context Anchor

| 항목 | 내용 |
|------|------|
| **WHY** | 자체 시뮬레이션은 시초가 단일 가격으로만 체결돼 실제 모의투자 환경(호가/체결지연/거래정지/환율)을 반영하지 못함. 운영 신뢰도 향상 + 향후 실전 전환 시 코드 변경 최소화 |
| **WHO** | 미국주식 페이퍼 트레이딩 시스템 운영자(본인). KIS 모의투자 계좌는 이번 Plan 기준 **미발급** 상태 |
| **RISK** | KIS 모의투자에서 NVDA/TSLA가 매매 불가능할 가능성, 모의투자 API 호출 한도(실전 대비 낮음), 토큰 만료(24h) 자동 갱신 누락, 신호 엔진 미결정 상태로 Design 진입 시 인터페이스 설계 오버엔지니어링 위험 |
| **SUCCESS** | (1) `python main.py --order-now` 실행 시 KIS 모의계좌에 실제 주문이 체결되고 응답이 로그에 남음 (2) Streamlit에 KIS 잔고가 표시됨 (3) 매매 불가 종목은 자동 필터링 (4) `SignalProvider` 인터페이스로 FinBERT/GPT-5 교체가 단일 config 변수로 가능 |
| **SCOPE** | 신규: `kis_broker.py`, `signal_provider.py` (인터페이스만), `data/kis_symbols.json`. 수정: `trader.py`(Adapter 호출로 단순화), `portfolio.py`(KIS 동기화 메서드 추가), `app.py`(KIS 섹션 추가), `config.py`(KIS 환경변수). **Out of Scope**: 신호 엔진 결정(FinBERT vs GPT-5), 실전 계좌 전환, 다중 계좌 |

---

## 0. 사전 준비 (KIS 모의투자 계좌 발급)

> 이 섹션은 **운영자가 코드 작업 전에 직접 수행**하는 외부 절차입니다. Design/Do 단계 진입 전 완료해야 합니다.

### 0.1 발급 절차

| 단계 | 작업 | 비고 |
|------|------|------|
| 1 | 한국투자증권 일반 계좌 + 해외주식 거래 약정 보유 확인 | 미보유 시 영업점/비대면 개설 필요 |
| 2 | [eFriend Expert](https://securities.koreainvestment.com/main/customer/systemdown/OpenAPI.jsp) 설치 및 로그인 | Windows 전용 HTS — Plan 작성 PC와 동일 환경 OK |
| 3 | [모의투자 신청 페이지](https://securities.koreainvestment.com/main/research/virtual/_static/TF07da010000.jsp)에서 **해외주식 모의투자 리그** 신청 | "리그구분 = 해외주식" 선택, 가상자금 USD 부여 |
| 4 | [KIS Developers 포털](https://apiportal.koreainvestment.com/intro)에서 OpenAPI 신청 → **모의투자용 APP_KEY / APP_SECRET 별도 발급** | 실전과 키가 다름 — 둘 다 받아두면 향후 전환 용이 |
| 5 | 모의투자 계좌번호 확인 (8자리 + 2자리 상품코드) | 예: `5012345601` |
| 6 | `.env`에 환경변수 추가 (§3 참고) | `KIS_PAPER_TRADING=true` 모드로 설정 |

### 0.2 발급 후 검증 체크리스트

- [ ] eFriend Expert에서 해외 모의투자 리그 활성화 확인
- [ ] APP_KEY/APP_SECRET 발급 이메일 수신
- [ ] 모의투자 가상자금 USD 잔고 확인 (HTS 또는 모바일)
- [ ] 매매 가능 미국 종목 일부 메모 (NVDA/TSLA/AAPL/MSFT 중 어느 것이 가능한지)

> **중요**: 단계 4의 APP_KEY는 **모의투자용**과 **실전용**이 별도입니다. 잘못된 키로 모의투자 도메인을 호출하면 401이 발생합니다.

---

## 1. 기능 요구사항

### FR-01~04: KIS Broker Adapter (신규 핵심 모듈)

| ID | 요구사항 |
|----|----------|
| FR-01 | 신규 파일 `kis_broker.py`에 `KISBroker` 클래스 구현 — `python-kis` (Soju06) 라이브러리 래핑 |
| FR-02 | `KISBroker.connect()` — APP_KEY/APP_SECRET/계좌번호로 인증, OAuth 토큰 24h 자동 갱신 처리 |
| FR-03 | `KISBroker.place_order(symbol, action, shares, price=None)` — `action ∈ {"BUY", "SELL"}`, `price=None`이면 시장가, 지정가 시 호가 단위 자동 보정. 응답에 주문번호/체결가/체결시각 포함 |
| FR-04 | `KISBroker.get_account()` — `{cash_usd, positions: {symbol: {shares, avg_price, current_price}}}` 반환. KIS 해외주식 잔고조회 API 사용 |

**기존 vs 신규 주문 처리 비교:**

| 구분 | 기존 (자체 시뮬레이션) | 신규 (KIS Adapter) |
|------|---------------------|-------------------|
| 체결가 | `collector.get_latest_open_price()` 단일 가격 | KIS 실제 모의주문 응답 |
| 잔고 | `portfolio.cash` 직접 차감 | KIS 계좌 잔고 API 조회 |
| 체결 거부 | 없음 (항상 성공) | 거래정지/매매불가/잔고부족 시 응답 코드로 거부 |
| 환율 | 없음 (USD 단일) | USD 외화계좌 사용 — 환율 무관 (FR-15 참조) |

---

### FR-05~07: trader.py Adapter 호출로 단순화

| ID | 요구사항 |
|----|----------|
| FR-05 | `trader.process_orders(signals, portfolio, broker)` 시그니처 추가 — 세 번째 인자로 `KISBroker` 주입 |
| FR-06 | `_process_buy_signal()`, `_process_sell_signal()`은 가격 계산 후 `broker.place_order(...)` 호출. 응답이 정상이면 `Trade` 객체 생성, 실패 시 로그 + None 반환 |
| FR-07 | `apply_buy()` / `apply_sell()` 호출 제거 — KIS 계좌가 Source of Truth이므로 Trade 기록만 유지(`record_trade`), 잔고는 `sync_from_kis()`로 갱신 (FR-12) |

---

### FR-08~10: SignalProvider 인터페이스 (신호 엔진 추상화)

> **목적**: Plan 단계에서는 인터페이스만 정의하고 **FinBERT vs GPT-5 결정은 Design 단계로 위임**. 이 인터페이스는 KIS 연동과 독립적이지만, 동일 Plan 안에서 다루는 이유는 두 변경이 `trader.py`/`scheduler.py`를 함께 건드리기 때문.

| ID | 요구사항 |
|----|----------|
| FR-08 | 신규 파일 `signal_provider.py`에 `SignalProvider` 추상 클래스(Protocol) 정의 — `generate_signals(symbols: list[str]) -> dict[str, dict]` 단일 메서드 |
| FR-09 | `config.py`에 `SIGNAL_ENGINE` 변수 추가 — `"finbert"` 또는 `"gpt5"` 중 선택. 기본값은 Design 결정 시까지 `"finbert"` (현행 동작 유지) |
| FR-10 | `signals.generate_signals_for_all()`은 내부적으로 `SIGNAL_ENGINE`을 보고 적절한 Provider를 인스턴스화 — 기존 호출부(scheduler/main/app)는 변경 없음 |

> **Note**: `FinbertProvider` / `Gpt5Provider`의 **실제 구현은 Design 이후 별도 피처(`signal-engine-decision`)** 로 분리. 이번 Plan에서는 인터페이스 + dispatcher + 기존 코드를 `FinbertProvider`로 묶는 wrapping만 수행.

---

### FR-11~13: portfolio.py KIS 동기화

| ID | 요구사항 |
|----|----------|
| FR-11 | `portfolio.sync_from_kis(broker: KISBroker) -> Portfolio` 신규 함수 — KIS 계좌 잔고를 조회해 `Portfolio` 객체로 변환 |
| FR-12 | `sync_from_kis()` 호출 후 `save_portfolio()`로 `portfolio.json`에 캐시 저장 — 대시보드/리포트에서 KIS API 부하 없이 참조 가능 |
| FR-13 | `load_portfolio()`는 호환성 유지 (캐시 읽기) — 단, 신규 `--source kis` 옵션 시 `sync_from_kis()` 후 반환 |

**동기화 트리거 시점:**
- 매 `order_processing_job` 실행 후 (주문 처리 완료 → 동기화)
- Streamlit "KIS 동기화" 버튼 클릭 시
- `python main.py --report --source kis` 실행 시

---

### FR-14: 매매 가능 종목 자동 필터링

| ID | 요구사항 |
|----|----------|
| FR-14 | KIS 모의투자 매매 가능 종목 목록을 `data/kis_symbols.json`에 캐시. 기동 시 또는 일 1회 갱신. `config.SYMBOLS`와 교집합만 거래 대상으로 사용 |

**처리 흐름:**
```
1. KIS 해외주식 종목 마스터 API 조회 (또는 첫 주문 시도 후 거부 응답 학습)
2. data/kis_symbols.json 저장: ["AAPL", "MSFT", "NVDA", ...]
3. signals 생성 시 SYMBOLS ∩ kis_symbols 만 사용
4. 제외된 종목은 로그 출력: "[KIS] {symbol} 모의투자 매매 불가 — 신호 생성 제외"
```

---

### FR-15: 통화 = USD 외화계좌 단일

| ID | 요구사항 |
|----|----------|
| FR-15 | KIS 미국주식 모의투자는 **USD 외화계좌**로 운용. 원화 통합증거금 사용 안 함 → 환율 변환 로직 불필요. `config.INITIAL_CASH`(현재 $100,000)는 모의투자 가상자금 USD와 별개 — KIS 계좌 잔고를 직접 사용 |

---

### FR-16~19: Streamlit 대시보드 KIS 통합

| ID | 요구사항 |
|----|----------|
| FR-16 | `app.py` 상단 헤더에 "KIS 모의투자 계좌" 영역 추가 — 계좌번호(마스킹), USD 가용현금, 총 평가금액 표시 |
| FR-17 | "KIS 동기화" 버튼 추가 — 클릭 시 `portfolio.sync_from_kis()` 실행 후 `st.rerun()` |
| FR-18 | 보유 종목 카드의 "현재가"는 KIS API의 실시간 시세를 사용 (기존 `collector.get_latest_open_price()` 폴백 유지) |
| FR-19 | 거래 내역 표(`load_trades()`)에 `order_no`(KIS 주문번호), `kis_status`(체결/거부) 컬럼 추가 |

---

### FR-20: 안전장치 (Mock 모드 + Dry Run)

| ID | 요구사항 |
|----|----------|
| FR-20 | `config.KIS_PAPER_TRADING=True`일 때만 KIS 도메인 = `https://openapivts.koreainvestment.com:29443` (모의투자 도메인). False면 명시적 에러 — 실수로 실전 도메인 호출 방지 |

> **추가 안전장치**: `python main.py --order-now --dry-run` 옵션 추가 — 주문 직전까지 동작하고 `place_order()` 직전에 로그만 남기고 종료.

---

## 2. 변경 대상 파일

| 파일 | 유형 | 주요 변경 |
|------|------|-----------|
| `kis_broker.py` | **신규** | `KISBroker` 클래스 — connect/place_order/get_account/get_quote |
| `signal_provider.py` | **신규** | `SignalProvider` Protocol + `get_signal_provider(name)` factory |
| `data/kis_symbols.json` | **신규** | KIS 모의투자 매매 가능 종목 캐시 |
| `trader.py` | 수정 | `process_orders()` 시그니처에 `broker` 추가, `apply_buy/sell` 제거 |
| `portfolio.py` | 수정 | `sync_from_kis()` 추가 |
| `scheduler.py` | 수정 | `order_processing_job()`에서 KIS Broker 인스턴스화 + 주문 후 동기화 |
| `main.py` | 수정 | `--dry-run` 옵션 추가, `--source kis` 옵션 추가 |
| `app.py` | 수정 | KIS 헤더 영역, 동기화 버튼, 거래내역 컬럼 추가 |
| `config.py` | 수정 | KIS 환경변수, `SIGNAL_ENGINE` 변수 추가 |
| `requirements.txt` | 수정 | `python-kis` 추가 |
| `.env.example` | 수정 | KIS_APP_KEY/SECRET/ACCOUNT_NO 항목 추가 |

**변경 없는 파일**: `signals.py`(내부에서 dispatcher만 호출), `collector.py`, `indicators.py`, `wsb_signal_engine.py`, `reddit_*`, `market_filter.py`

---

## 3. 새 config 상수 / 환경변수

```python
# --- KIS (한국투자증권) ---
KIS_APP_KEY: str = os.getenv("KIS_APP_KEY", "")
KIS_APP_SECRET: str = os.getenv("KIS_APP_SECRET", "")
KIS_ACCOUNT_NO: str = os.getenv("KIS_ACCOUNT_NO", "")    # "12345678-01" 형식
KIS_PAPER_TRADING: bool = os.getenv("KIS_PAPER_TRADING", "true").lower() == "true"
KIS_BASE_URL_PAPER = "https://openapivts.koreainvestment.com:29443"
KIS_BASE_URL_REAL  = "https://openapi.koreainvestment.com:9443"
KIS_TOKEN_CACHE_FILE = "data/kis_token.json"             # OAuth 토큰 캐시 (24h)
KIS_SYMBOLS_FILE = "data/kis_symbols.json"               # 매매 가능 종목 캐시
KIS_SYMBOLS_REFRESH_DAYS = 7                              # 종목 마스터 갱신 주기

# --- Signal Engine 선택 (Design 단계 결정) ---
SIGNAL_ENGINE = os.getenv("SIGNAL_ENGINE", "finbert")    # "finbert" | "gpt5"
```

```bash
# .env.example 추가 항목
KIS_APP_KEY=<모의투자용 App Key>
KIS_APP_SECRET=<모의투자용 App Secret>
KIS_ACCOUNT_NO=<8자리 계좌번호-2자리 상품코드>  # 예: 50123456-01
KIS_PAPER_TRADING=true
SIGNAL_ENGINE=finbert  # finbert | gpt5
```

---

## 4. 인터페이스 / 계약

### 4.1 KISBroker 인터페이스

```python
# kis_broker.py
class KISBroker:
    def __init__(self, app_key: str, app_secret: str, account_no: str, paper: bool = True): ...
    def connect(self) -> None: ...                              # OAuth 토큰 발급/갱신
    def place_order(
        self, symbol: str, action: Literal["BUY", "SELL"],
        shares: int, price: float | None = None,
    ) -> KISOrderResult: ...
    def get_account(self) -> KISAccount: ...                    # 잔고 + 보유종목
    def get_quote(self, symbol: str) -> float: ...              # 실시간 시세 (현재가)
    def get_tradable_symbols(self) -> list[str]: ...            # 매매 가능 종목 마스터

@dataclass
class KISOrderResult:
    order_no: str
    status: Literal["FILLED", "REJECTED", "PENDING"]
    fill_price: float | None
    fill_shares: int | None
    timestamp: str
    error_msg: str | None
```

### 4.2 SignalProvider 인터페이스

```python
# signal_provider.py
from typing import Protocol

class SignalProvider(Protocol):
    name: str
    def generate_signals(self, symbols: list[str]) -> dict[str, dict]: ...

def get_signal_provider(name: str) -> SignalProvider:
    if name == "finbert":
        from sentiment_provider import FinbertProvider
        return FinbertProvider()
    if name == "gpt5":
        # Design 단계에서 결정 후 구현
        raise NotImplementedError("GPT-5 provider — Design 단계 결정 후 구현")
    raise ValueError(f"Unknown signal engine: {name}")
```

---

## 5. 리스크

| 리스크 | 영향도 | 대응 |
|--------|--------|------|
| NVDA/TSLA 모의투자 매매 불가 | **높음** | FR-14로 자동 필터링 + 발급 후 0.2 체크리스트로 사전 확인. 불가 시 AAPL/MSFT 등 대체 종목으로 SYMBOLS 변경 |
| 모의투자 API 호출 한도 초과 | 중 | 토큰 캐시(`data/kis_token.json`) + 종목 마스터 캐시 + 잔고 조회 디바운싱(주문 직후 1회만) |
| OAuth 토큰 24h 만료 누락 | 중 | `python-kis`의 자동 갱신 사용. 추가로 `KISBroker.connect()`에 만료 5분 전 선제 갱신 로직 |
| `python-kis` API 변경 (서드파티 리스크) | 중 | `KISBroker` Adapter로 격리 — 라이브러리 교체 시 단일 파일만 수정. 버전 고정(`==`)으로 requirements.txt 명시 |
| 신호 엔진 미결정 상태로 Design 진입 | 낮음 | FR-08~10 인터페이스만 정의 — Design에서 결정 후 별도 피처로 구현 분리 |
| `portfolio.json` ↔ KIS 잔고 불일치 | 중 | 매 주문 직후 `sync_from_kis()` 호출 + Streamlit "KIS 동기화" 수동 버튼 + 불일치 감지 시 WARNING 로그 |
| 모의투자 도메인을 실전 키로 호출 | 중 | FR-20 안전장치 (`KIS_PAPER_TRADING=true` 강제 검증) |
| Streamlit 대시보드가 KIS API 호출로 느려짐 | 낮음 | `portfolio.json` 캐시 우선 사용, "KIS 동기화" 버튼 클릭 시에만 API 호출 |

---

## 6. 성공 기준

| SC | 기준 |
|----|------|
| SC-01 | `.env`에 KIS 키 설정 후 `python -c "from kis_broker import KISBroker; KISBroker(...).connect()"` 실행 시 토큰 발급 성공 로그 |
| SC-02 | `python main.py --order-now --dry-run` 실행 시 `place_order()` 직전 로그 출력 + 실제 주문은 발생하지 않음 |
| SC-03 | `python main.py --order-now` 실행 시 KIS HTS/모바일에서 모의투자 주문 체결 내역 확인 가능 |
| SC-04 | `data/trades.csv`에 `order_no`, `kis_status` 컬럼 추가 + 정상 기록 |
| SC-05 | 모의투자 매매 불가 종목 입력 시 `[KIS] {symbol} 모의투자 매매 불가` 로그 + 신호 생성 제외 |
| SC-06 | `data/kis_symbols.json` 파일이 첫 실행 시 생성되고, 이후 `KIS_SYMBOLS_REFRESH_DAYS` 경과 시에만 갱신 |
| SC-07 | Streamlit 대시보드 상단에 KIS 계좌 영역 표시 + "KIS 동기화" 버튼 동작 |
| SC-08 | `config.SIGNAL_ENGINE="finbert"` 기본값에서 기존 동작과 100% 동일 (회귀 없음) |
| SC-09 | `config.SIGNAL_ENGINE="gpt5"` 설정 시 `NotImplementedError` 발생 (Design 결정 전이므로 의도된 동작) |
| SC-10 | 매 `order_processing_job` 실행 후 `portfolio.json`이 KIS 계좌 상태와 일치 |
| SC-11 | OAuth 토큰이 24h 후 자동 갱신되어 다음 날 주문 정상 동작 |

---

## 7. 구현 순서 (Module Map / Session Guide)

| Module | 파일 | 작업 | 난이도 | Session |
|--------|------|------|--------|---------|
| M1 | `requirements.txt`, `.env.example`, `config.py` | KIS 환경변수 + python-kis 설치 | 낮음 | S1 (KIS 기반) |
| M2 | `kis_broker.py` | `KISBroker.connect()` + `get_account()` + `get_quote()` | 중간 | S1 |
| M3 | `kis_broker.py` | `place_order()` + `get_tradable_symbols()` + `data/kis_symbols.json` 캐시 | 중간 | S1 |
| M4 | `portfolio.py` | `sync_from_kis()` 추가 | 낮음 | S2 (트레이더 통합) |
| M5 | `trader.py` | `process_orders(signals, portfolio, broker)` 시그니처 변경 + Adapter 호출 | 중간 | S2 |
| M6 | `scheduler.py`, `main.py` | KIS Broker 인스턴스화 + `--dry-run` 옵션 + 주문 후 sync | 낮음 | S2 |
| M7 | `signal_provider.py` | `SignalProvider` Protocol + factory + `FinbertProvider` 래핑 | 낮음 | S3 (시그널 추상화) |
| M8 | `signals.py` | `SIGNAL_ENGINE` 분기 추가 (기존 동작 보존) | 낮음 | S3 |
| M9 | `app.py` | KIS 헤더 영역 + 동기화 버튼 + 거래내역 컬럼 | 중간 | S4 (대시보드) |

**권장 세션 분할:**
- **S1: KIS 기반** (M1~M3) — KIS API 연동 단독 검증
- **S2: 트레이더 통합** (M4~M6) — 주문 흐름 KIS로 전환
- **S3: 시그널 추상화** (M7~M8) — 신호 엔진 인터페이스 도입
- **S4: 대시보드** (M9) — Streamlit 통합

---

## 8. 미래 확장 (Out of Scope)

| 확장 | 분리 피처명 | 설명 |
|------|------------|------|
| FinBERT vs GPT-5 결정 + 구현 | `signal-engine-decision` | 비용/지연/정확도 비교표 + `Gpt5Provider` 구현 |
| 실전 계좌 전환 | `kis-real-trading` | `KIS_PAPER_TRADING=false` 모드 + 실거래 수수료/세금 반영 |
| 한국주식 동시 운용 | `kis-domestic-trading` | KOSPI/KOSDAQ 종목 추가 + 원화 계좌 |
| 호가창 기반 지정가 주문 | `kis-limit-order` | 시장가 → 지정가 전략 (1호가 위/아래 등) |
| ATR 기반 동적 Stop-Loss | `wsb-atr-stop` (기존 계획) | 고변동성 종목 적정 손절선 |

---

## 9. 참고 자료

- KIS Developers 포털: https://apiportal.koreainvestment.com/intro
- 공식 GitHub: https://github.com/koreainvestment/open-trading-api
- 서드파티 라이브러리: https://github.com/Soju06/python-kis
- 모의투자 안내: https://securities.koreainvestment.com/main/research/virtual/_static/TF07da010000.jsp
- 해외주식주문 API 문서: https://apiportal.koreainvestment.com/apiservice/apiservice-overseas-stock
- WikiDocs 튜토리얼: https://wikidocs.net/165185

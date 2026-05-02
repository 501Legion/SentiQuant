# Design: KIS Paper Trading — 한국투자증권 모의투자 연동

**Feature**: kis-paper-trading
**Date**: 2026-05-02
**Status**: Design (Option C — Pragmatic Protocol)
**Plan**: `docs/01-plan/features/kis-paper-trading.plan.md`
**PRD**: 없음 (운영자 단일 사용 기능)

---

## Context Anchor

| 항목 | 내용 |
|------|------|
| **WHY** | 자체 시뮬레이션은 시초가 단일 가격으로만 체결돼 실제 모의투자 환경(호가/체결지연/거래정지/환율)을 반영하지 못함. 운영 신뢰도 향상 + 향후 실전 전환 시 코드 변경 최소화 |
| **WHO** | 미국주식 페이퍼 트레이딩 시스템 운영자(본인). KIS 모의투자 계좌는 Plan 기준 미발급 — Design 진입 전 발급 완료 가정 |
| **RISK** | NVDA/TSLA 모의투자 매매 불가 가능성 / API 호출 한도 / 토큰 24h 만료 / python-kis 서드파티 변경 / portfolio.json↔KIS 잔고 불일치 |
| **SUCCESS** | (1) `--order-now` → KIS 모의계좌 실제 체결 (2) Streamlit에 KIS 잔고 표시 (3) 매매 불가 종목 자동 필터링 (4) `SignalProvider` 인터페이스로 FinBERT/GPT-5 교체가 단일 config 변수로 가능 |
| **SCOPE** | 신규: `kis_broker.py`, `signal_provider.py`(Protocol만), `data/kis_symbols.json`. 수정: trader.py / portfolio.py / scheduler.py / main.py / app.py / config.py / requirements.txt / .env.example. **Out**: 신호 엔진 결정, 실전 계좌, 다중 계좌 |

---

## 1. Overview

Plan에서 도입한 **Adapter 패턴 + SignalProvider Protocol**을 한 단계 일관화하여, **Broker도 동일 `typing.Protocol` 기반**으로 정의하는 Pragmatic Architecture(Option C)를 채택한다.

### 1.1 선택된 Architecture: Option C — Pragmatic Protocol

| 항목 | 결정 |
|------|------|
| 신규 파일 | 3개 (`kis_broker.py`, `signal_provider.py`, `data/kis_symbols.json`) |
| 수정 파일 | 8개 (trader/portfolio/scheduler/main/app/config/requirements/.env.example) |
| 신규 클래스 | 2개 Protocol + 1개 구상(`KISBroker`) + 1개 Mock(테스트용) |
| 추가 코드 | ~700 LOC |
| 회귀 위험 | 🟢 낮음 (기존 함수 시그니처에 broker 주입만 추가) |
| Plan SCOPE 준수 | ✅ |

### 1.2 핵심 원칙

| 원칙 | 적용 |
|------|------|
| **Source of Truth = KIS 계좌** | `portfolio.json`은 캐시. 매 주문 후 `sync_from_kis()` 호출로 갱신 |
| **Protocol 일관성** | Broker와 SignalProvider 모두 `typing.Protocol`로 정의 — 구상 클래스는 자동 만족 |
| **Adapter 격리** | python-kis 서드파티 의존을 `KISBroker` 내부로 격리. 라이브러리 교체 시 단일 파일만 수정 |
| **Safety First** | `KIS_PAPER_TRADING=true` 강제 검증 + `--dry-run` 플래그로 실수 방지 |
| **YAGNI** | Repository/DI 컨테이너 도입 안 함. 현재 Broker 1종 + SignalProvider 1.5종(finbert + gpt5 stub)으로는 과한 추상화 |

### 1.3 Discarded Options

| Option | Reject 사유 |
|--------|------------|
| A — Minimal Wrapper | trader.py가 `KISBroker` 구상 클래스에 직접 의존 → Plan FR-08(Protocol)과 패턴 불일치, Mock 작성 시 ABC 상속 강제 발생 |
| B — Hexagonal (Port/Adapter/Repository) | Broker 1종(KIS), SignalProvider 1.5종(finbert + gpt5 stub)뿐인 현 시점에서 Repository/DI 도입은 YAGNI 위반. Plan SCOPE 명시적 초과 |

---

## 2. Architecture

### 2.1 데이터 흐름

```
┌──────────────────────────────────────────────────────────────────┐
│                      scheduler.order_processing_job()            │
│                                                                  │
│   1. broker = KISBroker(...)                ← 인스턴스화         │
│   2. broker.connect()                       ← OAuth 토큰 발급    │
│   3. signals = load_signals_json()                               │
│   4. portfolio = load_portfolio()           ← 캐시               │
│   5. trades = trader.process_orders(signals, portfolio, broker)  │
│         │                                                        │
│         ├─ for each signal:                                      │
│         │     ├─ existing = broker.get_account().positions...    │
│         │     ├─ price = broker.get_quote(symbol)                │
│         │     ├─ result = broker.place_order(...) ← 실제 KIS호출 │
│         │     └─ if result.status == FILLED:                     │
│         │           Trade 객체 생성                              │
│         │                                                        │
│   6. portfolio = portfolio.sync_from_kis(broker)  ← Source 갱신  │
│   7. save_portfolio(portfolio)              ← 캐시 갱신          │
│   8. record_trades(trades)                  ← trades.csv 기록    │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│                  signals.generate_signals_for_all()              │
│                                                                  │
│   1. provider = signal_provider.get_provider(SIGNAL_ENGINE)      │
│         "finbert" → FinbertProvider() (기존 동작 wrapping)       │
│         "gpt5"    → NotImplementedError (Design 단계)            │
│   2. tradable = load_kis_symbols() ∩ config.SYMBOLS              │
│   3. signals = provider.generate_signals(tradable)               │
│   4. save_signals_json(signals)                                  │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### 2.2 모듈 구조 (Layer 관점)

| Layer | 모듈 | 책임 | 의존성 |
|-------|------|------|--------|
| **Protocol** | `kis_broker.Broker` (Protocol) | Broker 인터페이스 정의 | typing |
| **Protocol** | `signal_provider.SignalProvider` (Protocol) | 신호 엔진 인터페이스 정의 | typing |
| **Adapter** | `kis_broker.KISBroker` (impl) | python-kis 래핑, OAuth, place_order, get_account, get_quote, get_tradable_symbols | python-kis, config |
| **Adapter** | `signal_provider.FinbertProvider` (impl) | 기존 `signals.generate_signals_for_all()` 래핑 | signals, sentiment_provider |
| **Factory** | `kis_broker.get_broker()` | KISBroker 인스턴스 생성 | config |
| **Factory** | `signal_provider.get_provider(name)` | SIGNAL_ENGINE → Provider 매핑 | — |
| **Domain** | `portfolio.Portfolio` (기존) | 포지션 데이터 클래스 + sync_from_kis | kis_broker.Broker (Protocol) |
| **Use Case** | `trader.process_orders(signals, portfolio, broker)` | 주문 결정 + Broker 위임 | kis_broker.Broker (Protocol) |
| **Use Case** | `signals.generate_signals_for_all()` | Provider dispatcher | signal_provider |
| **Driver** | `scheduler.order_processing_job` | 인스턴스화 + 주문 흐름 + sync | kis_broker, trader, portfolio |
| **Driver** | `main.py` | CLI 진입점 (`--dry-run`, `--source kis`) | — |
| **Driver** | `app.py` | Streamlit 대시보드 (KIS 헤더, sync 버튼) | kis_broker, portfolio |
| **Test** | `tests/mock_broker.py` (권장) | Protocol을 만족하는 Mock | — |

**핵심 의존 방향**: Use Case (trader) → Protocol (Broker) ← Adapter (KISBroker). trader는 KISBroker 구상클래스를 모르고, Protocol만 본다.

---

## 3. Class & Protocol Design

### 3.1 Broker Protocol (`kis_broker.py`)

```python
from typing import Protocol, Literal
from dataclasses import dataclass

@dataclass(frozen=True)
class OrderResult:
    order_no: str
    status: Literal["FILLED", "REJECTED", "PENDING"]
    fill_price: float | None
    fill_shares: int | None
    timestamp: str               # ISO8601
    error_msg: str | None = None

@dataclass(frozen=True)
class AccountSnapshot:
    cash_usd: float
    positions: dict[str, "PositionSnapshot"]   # symbol -> snapshot
    updated_at: str

@dataclass(frozen=True)
class PositionSnapshot:
    shares: int
    avg_price: float
    current_price: float

class Broker(Protocol):
    """Broker Protocol — KIS, MockBroker, 향후 Alpaca/IBKR 모두 만족 가능."""
    def connect(self) -> None: ...
    def place_order(
        self, symbol: str, action: Literal["BUY", "SELL"],
        shares: int, price: float | None = None,   # None=시장가
    ) -> OrderResult: ...
    def get_account(self) -> AccountSnapshot: ...
    def get_quote(self, symbol: str) -> float: ...
    def get_tradable_symbols(self) -> list[str]: ...
```

### 3.2 KISBroker (Adapter, `kis_broker.py`)

```python
class KISBroker:
    """python-kis 래핑. Broker Protocol을 암묵적 만족."""

    def __init__(self, app_key: str, app_secret: str, account_no: str, paper: bool = True):
        self._app_key = app_key
        self._app_secret = app_secret
        self._account_no = account_no
        self._paper = paper
        self._kis = None                             # python-kis instance (lazy)
        self._token_cache_path = config.KIS_TOKEN_CACHE_FILE
        self._symbols_cache_path = config.KIS_SYMBOLS_FILE
        # FR-20: paper 모드 강제 검증
        if not paper:
            raise RuntimeError("실전 도메인 사용은 별도 피처(kis-real-trading)로 분리됨")

    def connect(self) -> None:
        # 토큰 캐시 로드 → 만료 임박(<5분)이면 재발급, 아니면 재사용
        # python-kis 자동 갱신 + 만료 5분 전 선제 갱신 (Plan §5 Risk 대응)
        ...

    def place_order(self, symbol, action, shares, price=None) -> OrderResult:
        # 모의투자 도메인 호출
        # 응답을 OrderResult로 정규화 (FILLED/REJECTED/PENDING)
        # 거부 응답(에러코드)은 REJECTED + error_msg에 기록 → trader가 분기 처리
        ...

    def get_account(self) -> AccountSnapshot:
        # KIS 해외주식 잔고조회 API → AccountSnapshot
        ...

    def get_quote(self, symbol: str) -> float:
        # KIS 해외주식 현재가 조회 API → float
        ...

    def get_tradable_symbols(self) -> list[str]:
        # data/kis_symbols.json 캐시 조회 → 만료(KIS_SYMBOLS_REFRESH_DAYS) 시 재조회
        # 첫 호출 또는 캐시 만료 시 KIS 종목 마스터 API 호출
        ...

def get_broker() -> Broker:
    """Factory — config 환경변수로 KISBroker 인스턴스 반환."""
    return KISBroker(
        app_key=config.KIS_APP_KEY,
        app_secret=config.KIS_APP_SECRET,
        account_no=config.KIS_ACCOUNT_NO,
        paper=config.KIS_PAPER_TRADING,
    )
```

### 3.3 SignalProvider Protocol (`signal_provider.py`)

```python
from typing import Protocol

class SignalProvider(Protocol):
    name: str
    def generate_signals(self, symbols: list[str]) -> dict[str, dict]: ...

class FinbertProvider:
    """기존 signals.generate_signals_for_all() 래핑."""
    name = "finbert"
    def generate_signals(self, symbols: list[str]) -> dict[str, dict]:
        from signals import generate_signals_for_all
        return generate_signals_for_all(symbols)   # 기존 코드 그대로

def get_provider(name: str) -> SignalProvider:
    if name == "finbert":
        return FinbertProvider()
    if name == "gpt5":
        raise NotImplementedError(
            "GPT-5 provider — 별도 피처 'signal-engine-decision'에서 결정"
        )
    raise ValueError(f"Unknown signal engine: {name}")
```

### 3.4 portfolio.py 변경

```python
def sync_from_kis(portfolio: Portfolio, broker: Broker) -> Portfolio:
    """KIS 계좌 잔고를 Source of Truth로 Portfolio 객체 재구성."""
    snap = broker.get_account()
    new_positions = {
        sym: Position(symbol=sym, shares=p.shares, avg_price=p.avg_price)
        for sym, p in snap.positions.items()
    }
    return Portfolio(cash=snap.cash_usd, positions=new_positions)

# 기존 apply_buy/apply_sell 함수: 더 이상 trader.process_orders에서 호출되지 않으나
# 백테스팅(backtester.py, reddit_backtester.py)에서는 계속 사용 → 유지
```

### 3.5 trader.py 변경 (시그니처 + 위임)

```python
def process_orders(
    signals: dict[str, dict],
    portfolio: Portfolio,
    broker: Broker,                          # ← 신규 인자
    dry_run: bool = False,                   # ← FR-20
) -> list[Trade]:
    """KIS Broker로 주문 위임. apply_buy/sell 호출 제거."""
    executed: list[Trade] = []
    account = broker.get_account()           # 잔고 1회 조회 (디바운싱)

    for symbol, sig_data in signals.items():
        signal = sig_data.get("signal", "NEUTRAL")
        existing_shares = account.positions.get(symbol, PositionSnapshot(0,0,0)).shares

        if signal in ("BUY", "STRONG_BUY"):
            trade = _process_buy(symbol, signal, account, broker, dry_run)
        elif signal in ("NEUTRAL", "SELL", "STRONG_SELL") and existing_shares > 0:
            trade = _process_sell(symbol, signal, account, broker, dry_run)
        else:
            continue

        if trade:
            executed.append(trade)

    return executed

def _process_buy(symbol, signal, account, broker, dry_run) -> Trade | None:
    quote = broker.get_quote(symbol)
    shares = _calc_shares(account.cash_usd, quote)
    if shares <= 0:
        return None

    if dry_run:
        logger.info(f"[DRY-RUN] BUY {symbol} {shares}주 @${quote}")
        return None

    result = broker.place_order(symbol, "BUY", shares)
    if result.status != "FILLED":
        logger.warning(f"[{symbol}] BUY 거부: {result.error_msg}")
        return None
    return Trade(
        symbol=symbol, action="BUY", signal=signal,
        price=result.fill_price, shares=result.fill_shares,
        amount=result.fill_price * result.fill_shares,
        date=result.timestamp,
        net_profit_pct=0.0, net_profit_usd=0.0,
        order_no=result.order_no,                # ← FR-19
        kis_status=result.status,                # ← FR-19
    )
```

---

## 4. API Contract (config 환경변수 + KIS API)

### 4.1 신규 config 상수

| 상수 | 환경변수 | 기본값 | 의미 |
|------|---------|--------|------|
| `KIS_APP_KEY` | `KIS_APP_KEY` | `""` | 모의투자용 App Key |
| `KIS_APP_SECRET` | `KIS_APP_SECRET` | `""` | 모의투자용 App Secret |
| `KIS_ACCOUNT_NO` | `KIS_ACCOUNT_NO` | `""` | "12345678-01" 형식 |
| `KIS_PAPER_TRADING` | `KIS_PAPER_TRADING` | `true` | False 시 RuntimeError 발생 (FR-20) |
| `KIS_BASE_URL_PAPER` | — | `https://openapivts.koreainvestment.com:29443` | 모의투자 도메인 |
| `KIS_TOKEN_CACHE_FILE` | — | `data/kis_token.json` | OAuth 토큰 24h 캐시 |
| `KIS_SYMBOLS_FILE` | — | `data/kis_symbols.json` | 매매 가능 종목 캐시 |
| `KIS_SYMBOLS_REFRESH_DAYS` | — | `7` | 종목 마스터 갱신 주기 |
| `SIGNAL_ENGINE` | `SIGNAL_ENGINE` | `"finbert"` | finbert | gpt5 |

### 4.2 KIS OpenAPI 엔드포인트 매핑

| 작업 | KIS API | python-kis 메서드 (예상) | 호출 빈도 |
|------|---------|--------------------------|---------|
| 토큰 발급 | `POST /oauth2/tokenP` | `kis.client.token` | 24h 1회 + 5분 전 선제 |
| 잔고조회 | `GET /uapi/overseas-stock/v1/trading/inquire-balance` | `kis.account.balance()` | 주문 직전/직후, sync 시 |
| 현재가 | `GET /uapi/overseas-price/v1/quotations/price` | `kis.quote(symbol)` | 신호 직전, app.py 폴백 |
| 매수/매도 | `POST /uapi/overseas-stock/v1/trading/order` | `kis.account.order_buy/sell()` | 신호별 1회 |
| 종목 마스터 | (다운로드 mst 파일) | `kis.market.symbols()` | 7일 1회 |
| 주문 취소 | `POST /uapi/overseas-stock/v1/trading/order-rvsecncl` | `kis.account.cancel()` | (현 SCOPE 외) |

### 4.3 wsb_signals 흐름 (변경 없음)

`wsb_signal_engine.py`, `reddit_*` 파일은 **변경 없음** — Reddit 신호는 별도 흐름이며 본 피처 영향 없음.

---

## 5. Data Model

### 5.1 OrderResult (신규)

```python
@dataclass(frozen=True)
class OrderResult:
    order_no: str            # KIS 주문번호 (e.g., "0123456")
    status: Literal["FILLED", "REJECTED", "PENDING"]
    fill_price: float | None  # FILLED일 때만
    fill_shares: int | None
    timestamp: str            # ISO8601 UTC
    error_msg: str | None     # REJECTED일 때
```

### 5.2 Trade 확장 (기존 + 2 컬럼)

```python
@dataclass
class Trade:
    symbol: str
    date: str
    action: Literal["BUY", "SELL"]
    signal: str
    price: float
    shares: int
    amount: float
    net_profit_pct: float
    net_profit_usd: float
    # ===== 신규 (FR-19) =====
    order_no: str | None = None
    kis_status: str | None = None    # "FILLED" | "REJECTED" | None
```

### 5.3 data/kis_symbols.json (신규)

```jsonc
{
  "updated_at": "2026-05-02T09:00:00Z",
  "refresh_days": 7,
  "tradable": ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", ...]   // KIS 모의투자 매매 가능 미국주
}
```

### 5.4 portfolio.json (기존 + sync 후 갱신)

기존 스키마 유지 — `sync_from_kis()` 호출 후 `cash` / `positions` 필드만 KIS Snapshot으로 덮어씀.

---

## 6. Error Handling

| 시나리오 | 처리 | 로그 레벨 |
|---------|------|---------|
| OAuth 토큰 발급 실패 | `RuntimeError` 즉시 raise (시스템 진입 불가) | ERROR |
| 토큰 만료 임박 | python-kis 자동 갱신 + 5분 전 선제 갱신 | DEBUG |
| `place_order` REJECTED (매매불가/잔고부족/거래정지) | `result.status="REJECTED"` + `error_msg` 기록, `Trade=None` 반환, 다음 종목 진행 | WARNING |
| `place_order` 네트워크 에러 | retry 1회 후 실패 → REJECTED 동등 처리 | WARNING |
| `get_account` 실패 | 캐시(`portfolio.json`) 폴백, dirty 플래그로 사용자에게 경고 | WARNING |
| `get_quote` 실패 | `collector.get_latest_open_price` 폴백 (FR-18) | WARNING |
| `get_tradable_symbols` 실패 | 마지막 캐시 사용, 7일 초과 시 강제 사용 + WARN | WARNING |
| `KIS_PAPER_TRADING=false` 호출 | `RuntimeError("실전 도메인은 별도 피처")` 즉시 raise (FR-20) | ERROR |
| `SIGNAL_ENGINE="gpt5"` 호출 | `NotImplementedError` (의도된 동작, SC-09) | INFO |
| portfolio.json ↔ KIS 잔고 불일치 (>$1) | sync 후 WARNING + Streamlit 빨간 점 표시 | WARNING |

**핵심 원칙**: KIS 호출 실패가 시스템 전체를 중단시키지 않도록 graceful degradation. 단, 토큰 발급 실패는 진입 불가 상황으로 즉시 중단.

---

## 7. Security & Safety

| 항목 | 정책 |
|------|------|
| API 키 저장 | `.env` 파일 (gitignore 등록 확인) — `.env.example`만 commit |
| 토큰 캐시 | `data/kis_token.json` — gitignore 등록 필수 |
| 도메인 안전장치 | `KIS_PAPER_TRADING=true` 미설정 시 `RuntimeError` (실수로 실전 호출 차단, FR-20) |
| Dry-run | `python main.py --order-now --dry-run` — `place_order` 직전 로그만, 실 주문 없음 |
| 계좌번호 마스킹 | Streamlit/로그 출력 시 `5012345*-01` 형태로 마스킹 |
| 호출 빈도 제한 | 잔고조회는 주문 후 1회 디바운싱 (`order_processing_job` 1회/분 * 잔고 1회 = ~분당 1회 KIS API 호출) |

---

## 8. Test Plan

### 8.1 Mock Broker (테스트 인프라, `tests/mock_broker.py` 권장)

```python
class MockBroker:
    """Broker Protocol 만족 — 단위 테스트용. 메모리 상태로 동작."""
    def __init__(self, initial_cash: float = 10000.0,
                 tradable: list[str] = None):
        self._cash = initial_cash
        self._positions: dict[str, PositionSnapshot] = {}
        self._tradable = tradable or ["AAPL", "MSFT"]
        self._order_seq = 0

    def connect(self) -> None: pass
    def get_quote(self, symbol: str) -> float:
        return 100.0   # fixture로 override 가능
    def place_order(self, symbol, action, shares, price=None) -> OrderResult:
        if symbol not in self._tradable:
            return OrderResult(order_no="", status="REJECTED",
                               fill_price=None, fill_shares=None,
                               timestamp="...", error_msg="not tradable")
        self._order_seq += 1
        # 매수/매도 체결 시뮬레이션 + cash/positions 갱신
        ...
        return OrderResult(order_no=str(self._order_seq), status="FILLED", ...)
    def get_account(self) -> AccountSnapshot:
        return AccountSnapshot(cash_usd=self._cash,
                               positions=self._positions.copy(),
                               updated_at="...")
    def get_tradable_symbols(self) -> list[str]:
        return self._tradable
```

### 8.2 시나리오별 Test (pytest 권장)

| # | 대상 | 시나리오 | 기대 |
|---|------|---------|------|
| T1 | `trader.process_orders` | MockBroker(cash=$10000), signal=BUY AAPL @$100 | Trade(BUY 100주), broker.cash=0 |
| T2 | 〃 | MockBroker(tradable=["AAPL"]), signal=BUY NVDA | Trade=None, REJECTED 로그 |
| T3 | 〃 | dry_run=True, signal=BUY AAPL | Trade=None, [DRY-RUN] 로그만 |
| T4 | 〃 | signal=SELL TSLA, 보유 0주 | 스킵, 로그 없음 |
| T5 | `signal_provider.get_provider` | name="gpt5" | NotImplementedError (SC-09) |
| T6 | 〃 | name="finbert" | FinbertProvider 인스턴스 |
| T7 | `portfolio.sync_from_kis` | MockBroker로 cash=$5000, AAPL 50주 | Portfolio(cash=5000, AAPL=Position(50, ...)) |
| T8 | `KISBroker.__init__` | paper=False | RuntimeError (FR-20) |
| T9 | `kis_broker.get_tradable_symbols` | 캐시 만료 7일 초과 | KIS 마스터 재호출 + 캐시 갱신 |

### 8.3 통합 테스트 (수동, 실 KIS 모의계좌)

| # | 명령 | 검증 (SC 매핑) |
|---|------|--------------|
| I1 | `python -c "from kis_broker import get_broker; get_broker().connect()"` | 토큰 발급 로그 (SC-01) |
| I2 | `python main.py --order-now --dry-run` | DRY-RUN 로그, 실주문 없음 (SC-02) |
| I3 | `python main.py --order-now` | KIS HTS에서 주문 확인 (SC-03) |
| I4 | `cat data/trades.csv | head` | order_no, kis_status 컬럼 (SC-04) |
| I5 | (NVDA 매매불가 시) `--order-now` | "[KIS] NVDA 모의투자 매매 불가" 로그 (SC-05) |
| I6 | 첫 실행 후 `data/kis_symbols.json` 확인 | 파일 생성 + tradable 배열 (SC-06) |
| I7 | `streamlit run app.py` → KIS 헤더 + sync 버튼 | UI 동작 (SC-07) |
| I8 | `SIGNAL_ENGINE=finbert` 기본값 회귀 테스트 | 기존 동작 100% 일치 (SC-08) |
| I9 | `SIGNAL_ENGINE=gpt5` 환경변수 설정 | NotImplementedError (SC-09) |
| I10 | `--order-now` 후 `portfolio.json` ↔ KIS 잔고 비교 | 일치 (SC-10) |
| I11 | 24h 후 재실행 | 토큰 재발급 + 정상 동작 (SC-11) |

---

## 9. Performance Considerations

| 항목 | 추정 | 근거 |
|------|------|------|
| 토큰 발급 | 1초 | 24h 1회만 발생 |
| 잔고조회 (`get_account`) | 0.5~1초 | `order_processing_job` 1회당 2회 (전/후) |
| 현재가 (`get_quote`) | 0.3~0.5초/종목 | SYMBOLS 10종 → ~5초 |
| 주문 (`place_order`) | 1~2초/건 | 실제 모의 체결 응답 대기 |
| 종목 마스터 (`get_tradable_symbols`) | 5~10초 | 7일 1회만 |
| 일일 KIS API 호출 총량 (정상) | ~50회 | 모의투자 한도(시간당 1000회) 대비 충분히 여유 |
| Streamlit 렌더링 | <2초 | `portfolio.json` 캐시 우선, sync 버튼 클릭 시에만 KIS API |

---

## 10. Backward Compatibility

| Aspect | 호환 | 검증 |
|--------|:--:|------|
| 기존 백테스팅(backtester/reddit_backtester) | ✅ | `apply_buy/apply_sell` 함수 보존, KIS 의존 없음 |
| `wsb_signal_engine` (Reddit 신호) | ✅ | 본 피처 영향 없음 |
| `signals.json` 파일 포맷 | ✅ | SignalProvider가 동일 dict 반환 |
| `portfolio.json` 스키마 | ✅ | 필드 동일, sync 후 값만 KIS Snapshot으로 덮어씀 |
| `trades.csv` 컬럼 | ⚠️ 추가 | `order_no`, `kis_status` 2개 컬럼 추가 — 기존 분석 도구 영향 미미 |
| `--run-now`, `--reddit-run-now` (기존 CLI) | ✅ | 변경 없음 |
| `SIGNAL_ENGINE` 미설정 | ✅ | 기본값 "finbert" → 기존 동작 (SC-08) |

---

## 11. Implementation Guide

### 11.1 변경 위치 요약

| # | 파일 | 변경 유형 | 핵심 |
|---|------|---------|------|
| 1 | `requirements.txt` | 추가 | `python-kis==<lockver>` |
| 2 | `.env.example` | 추가 | KIS_* 5개 + SIGNAL_ENGINE |
| 3 | `config.py` | 추가 | KIS_* 9개 상수 + SIGNAL_ENGINE |
| 4 | `kis_broker.py` | 신규 | Broker Protocol + KISBroker + dataclasses + factory |
| 5 | `signal_provider.py` | 신규 | SignalProvider Protocol + FinbertProvider + factory |
| 6 | `data/kis_symbols.json` | 신규 (자동 생성) | 첫 실행 시 KIS 마스터에서 채움 |
| 7 | `portfolio.py` | 추가 | `sync_from_kis(portfolio, broker)` |
| 8 | `trader.py` | 수정 | `process_orders` 시그니처 + `--dry-run` + 위임 |
| 9 | `signals.py` | 수정 | `generate_signals_for_all`이 dispatcher 경유 |
| 10 | `scheduler.py` | 수정 | `order_processing_job`에 broker 인스턴스화 + sync 호출 |
| 11 | `main.py` | 수정 | `--dry-run`, `--source kis` 옵션 추가 |
| 12 | `app.py` | 수정 | KIS 헤더 영역 + 동기화 버튼 + 거래내역 컬럼 |

### 11.2 구현 순서 (Plan §7 Module Map 준용)

1. M1: requirements.txt에 python-kis 추가, .env.example/config.py에 KIS 환경변수 (15분)
2. M2: kis_broker.py — Protocol + KISBroker.connect/get_account/get_quote (3시간)
3. M3: kis_broker.py — place_order + get_tradable_symbols + 캐시 (3시간)
4. M4: portfolio.py — sync_from_kis (1시간)
5. M5: trader.py — process_orders 시그니처 변경 + Adapter 위임 + Trade 확장 (3시간)
6. M6: scheduler.py + main.py — broker 인스턴스화, --dry-run (1.5시간)
7. M7: signal_provider.py — Protocol + FinbertProvider + factory (1시간)
8. M8: signals.py — SIGNAL_ENGINE 분기 (30분)
9. M9: app.py — KIS 헤더, sync 버튼, 거래내역 컬럼 (3시간)
10. **검증**: I1~I11 통합 테스트 순차 실행

### 11.3 Session Guide (Module Map)

| Module | Files | Items | Recommended Session |
|--------|-------|-------|---------------------|
| **module-1: KIS 기반** | requirements.txt, .env.example, config.py, kis_broker.py, data/kis_symbols.json | M1+M2+M3 (Broker Protocol + KISBroker + 캐시) | S1 (~7시간) |
| **module-2: 트레이더 통합** | portfolio.py, trader.py, scheduler.py, main.py | M4+M5+M6 (sync + 위임 + dry-run) | S2 (~5시간) |
| **module-3: 시그널 추상화** | signal_provider.py, signals.py | M7+M8 (Protocol + dispatcher) | S3 (~1.5시간) |
| **module-4: 대시보드** | app.py | M9 (Streamlit KIS UI) | S4 (~3시간) |

**Recommended Session Plan**: 4 sessions (총 ~16.5시간 / 약 2일)

```bash
# 부분 실행 예시
/pdca do kis-paper-trading --scope module-1
/pdca do kis-paper-trading --scope module-2
/pdca do kis-paper-trading --scope module-3
/pdca do kis-paper-trading --scope module-4
```

**Session 의존성**: S1 → S2 → S3 → S4 (순차). S3는 S1과 독립적이지만 trader.py와 동시 수정 시 충돌 가능 → S2 후 권장.

### 11.4 사전 조건 (운영자 직접 수행)

Plan §0의 발급 절차 6단계가 **완료되어야 S1 진입 가능**:
- [ ] eFriend Expert에서 해외 모의투자 리그 활성화
- [ ] APP_KEY/APP_SECRET 발급 이메일 수신
- [ ] 모의투자 가상자금 USD 잔고 확인
- [ ] 매매 가능 미국 종목 일부 사전 확인 (NVDA/TSLA가 가능한지)
- [ ] `.env`에 KIS_* 5개 환경변수 입력

---

## 12. Trade-offs & Open Questions

| 결정 사항 | 선택 | 대안 | 근거 |
|---------|------|------|------|
| Broker 1종에 Protocol 도입 | ✅ | 직접 구상클래스 의존 (Option A) | Plan FR-08(SignalProvider Protocol)과 일관성 + Mock 비용 절감 |
| Source of Truth = KIS 계좌 | ✅ | portfolio.json 직접 차감 (현재 방식) | Plan WHY: 실 환경 반영. 캐시는 1회 sync로 충분 |
| `apply_buy/sell` 보존 | ✅ | 완전 제거 | backtester가 사용 중 — 회귀 위험 vs 청결도 trade-off, 유지가 안전 |
| MockBroker는 별도 파일 | 권장 | KISBroker 내부 nested class | 테스트 인프라 분리 원칙 + tests/ 디렉토리 신설 트리거 (auto_stock G9와 시너지) |
| Trade 확장 vs 별도 KISTrade | Trade 확장 | 별도 dataclass | 컬럼 2개만 추가, 기존 처리 로직 호환 |
| `--dry-run` 위치 | trader 인자 | scheduler 분기 | trader 인자가 테스트 가능성 ↑ (T3) |

### Open Questions (Do 진입 전 확인 필요)

1. **python-kis 라이브러리 버전 고정**: 현재 활발히 개발 중인 서드파티. lockfile에 정확한 버전 명시 필요. → Do M1 진입 시 최신 안정 버전 확인.
2. **NVDA/TSLA 매매 가능 여부**: 운영자 사전 확인 필요. 불가 시 SYMBOLS에서 자동 제외되지만, 그 결과 SYMBOLS가 너무 줄어들면 신호 품질 저하 — 대체 종목 결정 필요.
3. **GPT-5 출시 일정**: Plan에서는 GPT-5 stub만 제공. 실제 구현은 별도 피처(`signal-engine-decision`) — 의도된 분리.

---

## 13. References

- **Plan**: `docs/01-plan/features/kis-paper-trading.plan.md` (FR-01~20, NFR, SC-01~11, Module Map)
- **KIS Developers**: https://apiportal.koreainvestment.com/intro
- **공식 GitHub**: https://github.com/koreainvestment/open-trading-api
- **python-kis (서드파티)**: https://github.com/Soju06/python-kis
- **모의투자 안내**: https://securities.koreainvestment.com/main/research/virtual/_static/TF07da010000.jsp
- **ARCHITECTURE.md §2**: portfolio.py / trader.py / scheduler.py 현재 역할 — 본 Design으로 수정됨

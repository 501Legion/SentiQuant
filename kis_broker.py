"""KIS (한국투자증권) 모의투자 OpenAPI Adapter.

Design Ref: §1.1 Option C (Pragmatic Protocol) — Broker도 typing.Protocol로 정의해
SignalProvider와 일관된 추상화를 유지. 구상클래스 KISBroker가 Protocol을 암묵적으로 만족.

Plan SC-01/SC-06/SC-11: 토큰 발급 + 종목 마스터 캐시 + 24h 자동 갱신.
Plan FR-20: KIS_PAPER_TRADING=False 호출 시 RuntimeError로 실전 도메인 차단.

Implementation note: python-kis 라이브러리는 실전 키 + 모의 키 동시 운영자를 가정해
인스턴스화 시 실전 키를 강제 요구하고 token property가 항상 실전 도메인을 호출한다.
모의투자만 사용하는 본 시스템 요구사항(FR-20)과 부적합하여, KIS OpenAPI를 requests로
직접 호출한다. Plan §5 Risk 'python-kis 서드파티 변경' 회피 + Adapter 격리 강화.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

import requests

import config

logger = logging.getLogger(__name__)


# =============================================================================
# Data Classes (Design §3.1, §5.1)
# =============================================================================

@dataclass(frozen=True)
class PositionSnapshot:
    shares: int
    avg_price: float
    current_price: float


@dataclass(frozen=True)
class AccountSnapshot:
    cash_usd: float
    positions: dict[str, PositionSnapshot]
    updated_at: str  # ISO8601 UTC


@dataclass(frozen=True)
class OrderResult:
    order_no: str
    status: Literal["FILLED", "REJECTED", "PENDING"]
    fill_price: float | None
    fill_shares: int | None
    timestamp: str  # ISO8601 UTC
    error_msg: str | None = None


@dataclass(frozen=True)
class FillRecord:
    order_no: str
    symbol: str
    action: Literal["BUY", "SELL"]
    fill_price: float
    fill_shares: int
    timestamp: str
    status: Literal["FILLED"] = "FILLED"


# =============================================================================
# Broker Protocol (Design §3.1)
# =============================================================================

class Broker(Protocol):
    """Broker 인터페이스 — KISBroker / MockBroker / 향후 Alpaca·IBKR 모두 만족 가능."""

    def connect(self) -> None: ...
    def place_order(
        self,
        symbol: str,
        action: Literal["BUY", "SELL"],
        shares: int,
        price: float | None = None,
    ) -> OrderResult: ...
    def get_account(self) -> AccountSnapshot: ...
    def get_order_history(self, start_date: str, end_date: str) -> list[FillRecord]: ...
    def get_quote(self, symbol: str) -> float: ...
    def get_tradable_symbols(self) -> list[str]: ...


# =============================================================================
# KIS OpenAPI 상수 (모의투자 도메인 전용)
# =============================================================================

# 토큰 만료 5분 전 선제 갱신 (Plan §5 Risk: 24h 만료 누락 방지)
_TOKEN_PREEMPT_REFRESH_SECONDS = 5 * 60

# place_order 네트워크 에러 시 1회 재시도 (Design §6 Error Handling)
_ORDER_RETRY_COUNT = 1

# 거래소별 잔고조회는 부분 실패를 전체 잔고로 오인하지 않도록 짧게만 재시도한다.
_BALANCE_RETRY_COUNT = 1

# 모의투자 tr_id (KIS Developers 포털 — 해외주식주문/잔고/시세)
_TR_ID_BUY_PAPER = "VTTT1002U"        # 미국주식 매수 — 모의
_TR_ID_SELL_PAPER = "VTTT1001U"       # 미국주식 매도 — 모의
_TR_ID_BALANCE_PAPER = "VTTS3012R"    # 미국주식 잔고(보유종목) — 모의
_TR_ID_PSAMOUNT_PAPER = "VTTS3007R"   # 미국주식 매수가능금액(가용현금) — 모의
_TR_ID_ORDER_HISTORY_PAPER = "VTTS3035R"  # 해외주식 주문체결내역 — 모의
_TR_ID_QUOTE = "HHDFS00000300"        # 해외주식 현재가 (실전/모의 공통)

# 같은 토큰으로 KIS는 "초당 거래건수" 제한 → 연속 호출 사이 짧은 sleep
_INTER_CALL_DELAY_SECONDS = 1.1

# 거래소 코드 (해외주식 미국 시장) — 종목마다 상장 거래소가 다름.
# price API는 EXCD(NAS/NYS/AMS), 주문/잔고 API는 OVRS_EXCG_CD(NASD/NYSE/AMEX)로 체계가 다름.
# 종목별 거래소는 _resolve_exchange가 price API 프로브로 판별 후 캐시. 미판별 시 NASDAQ 폴백.
_DEFAULT_EXCHANGE = "NASD"             # 폴백 OVRS_EXCG_CD (주문/잔고)
_QUOTE_EXCHANGE = "NAS"               # 폴백 EXCD (시세)
# (EXCD for price API, OVRS_EXCG_CD for order/balance API) — 프로브 순서 = 미국 상장 빈도순
_US_EXCHANGES: tuple[tuple[str, str], ...] = (
    ("NAS", "NASD"),   # 나스닥
    ("NYS", "NYSE"),   # 뉴욕거래소
    ("AMS", "AMEX"),   # NYSE American/Arca (ETF 다수)
)


# =============================================================================
# KISBroker Adapter (Design §3.2)
# =============================================================================

class KISBroker:
    """KIS 모의투자 OpenAPI 직접 호출 Adapter. Broker Protocol 암묵적 만족."""

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        account_no: str,
        paper: bool = True,
    ):
        # Plan SC: FR-20 — paper=False 호출 시 즉시 실전 도메인 차단
        if not paper:
            raise RuntimeError(
                "KIS 실전 도메인 사용은 별도 피처(kis-real-trading)로 분리됨. "
                "config.KIS_PAPER_TRADING=true 로 설정하세요."
            )
        if not app_key or not app_secret or not account_no:
            raise RuntimeError(
                "KIS 환경변수 누락 — .env에 KIS_APP_KEY/KIS_APP_SECRET/KIS_ACCOUNT_NO 설정 필요"
            )

        self._app_key = app_key
        self._app_secret = app_secret
        self._account_no = account_no  # "12345678-01"
        self._paper = paper
        self._base_url = config.KIS_BASE_URL_PAPER
        self._access_token: str | None = None
        self._expires_at: datetime | None = None
        self._token_cache_path = Path(config.KIS_TOKEN_CACHE_FILE)
        self._symbols_cache_path = Path(config.KIS_SYMBOLS_FILE)
        # 종목→거래소 판별 캐시 (price API 프로브 결과 영속화 — 일일 재프로브 절감)
        self._exchange_cache_path = self._symbols_cache_path.parent / "kis_exchange_map.json"
        self._exchange_cache: dict[str, tuple[str, str]] = self._load_exchange_cache()
        self._last_symbols_checked: list[str] = []
        self._last_symbols_rejected: list[str] = []

        # 계좌번호 분해: "12345678-01" → CANO="12345678", ACNT_PRDT_CD="01"
        # 상품코드 누락 시 종합계좌 기본값 "01" 사용
        if "-" in account_no:
            self._cano, self._acnt_prdt_cd = account_no.split("-", 1)
        else:
            self._cano, self._acnt_prdt_cd = account_no, "01"
            logger.warning(
                f"[KIS] KIS_ACCOUNT_NO product code missing - assuming '{account_no}-01'. "
                f"If errors occur, specify 'NNNNNNNN-XX' format in .env"
            )

        # KIS API '초당 거래건수' 회피용 last-call timestamp (EGW00201)
        self._last_call_ts: float = 0.0

    # -------------------------------------------------------------------------
    # connect — Plan SC-01, SC-11
    # -------------------------------------------------------------------------

    def connect(self) -> None:
        """OAuth 토큰 발급/갱신. 5분 전 선제 갱신으로 24h 만료 대응."""
        cached = self._load_token_cache()
        if cached and not self._token_expiring_soon(cached):
            logger.debug("[KIS] 토큰 캐시 유효 — 재사용")
            self._access_token = cached["access_token"]
            self._expires_at = datetime.fromisoformat(cached["expires_at"])
            return

        # 신규 발급 (또는 만료 임박 → 강제 재발급)
        token, expires_at = self._issue_token()
        self._access_token = token
        self._expires_at = expires_at
        self._save_token_cache(token, expires_at)
        logger.info(
            f"[KIS] 모의투자 토큰 발급 성공 (만료: {expires_at.isoformat()})"
        )

    def _issue_token(self) -> tuple[str, datetime]:
        """KIS OAuth — POST /oauth2/tokenP. KIS는 1분에 1회만 토큰 발급 허용 (EGW00133)."""
        url = f"{self._base_url}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
        }
        try:
            resp = requests.post(url, json=body, timeout=config.REQUEST_TIMEOUT)
        except requests.RequestException as e:
            raise RuntimeError(f"[KIS] 토큰 발급 네트워크 오류: {e}") from e

        if resp.status_code != 200:
            # EGW00133 (1분 1회 제한) 안내 강화
            try:
                err = resp.json()
                err_code = err.get("error_code", "")
                err_desc = err.get("error_description", resp.text)
            except (json.JSONDecodeError, ValueError):
                err_code, err_desc = "", resp.text
            raise RuntimeError(
                f"[KIS] 토큰 발급 실패 ({resp.status_code}) "
                f"[{err_code}] {err_desc}"
            )

        data = resp.json()
        token = data.get("access_token", "")
        if not token:
            raise RuntimeError(f"[KIS] 토큰 응답에 access_token 없음: {data}")
        # access_token_token_expired = "YYYY-MM-DD HH:MM:SS" (KST) 또는 expires_in (초)
        expires_in = int(data.get("expires_in", 24 * 3600))
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        return token, expires_at

    def _load_token_cache(self) -> dict | None:
        if not self._token_cache_path.exists():
            return None
        try:
            return json.loads(self._token_cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"[KIS] 토큰 캐시 읽기 실패 — 재발급: {e}")
            return None

    def _save_token_cache(self, token: str, expires_at: datetime) -> None:
        self._token_cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "access_token": token,
            "expires_at": expires_at.isoformat(),
            "paper": True,
        }
        self._token_cache_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _token_expiring_soon(cached: dict) -> bool:
        try:
            expires_at = datetime.fromisoformat(cached["expires_at"])
        except (KeyError, ValueError):
            return True
        remaining = (expires_at - datetime.now(timezone.utc)).total_seconds()
        return remaining < _TOKEN_PREEMPT_REFRESH_SECONDS

    # -------------------------------------------------------------------------
    # HTTP Helper (인증 헤더 + 에러 처리)
    # -------------------------------------------------------------------------

    def _ensure_connected(self) -> None:
        if not self._access_token:
            raise RuntimeError("[KIS] connect() 호출 전에 API 접근됨")

    def _auth_headers(self, tr_id: str) -> dict[str, str]:
        self._ensure_connected()
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._access_token}",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
            "tr_id": tr_id,
            "custtype": "P",  # 개인
        }

    def _throttle(self) -> None:
        """KIS '초당 거래건수 초과(EGW00201)' 회피 — 마지막 호출로부터 일정 간격 보장."""
        elapsed = time.time() - self._last_call_ts
        if elapsed < _INTER_CALL_DELAY_SECONDS:
            time.sleep(_INTER_CALL_DELAY_SECONDS - elapsed)
        self._last_call_ts = time.time()

    def _get(self, path: str, tr_id: str, params: dict[str, Any]) -> dict:
        self._throttle()
        headers = self._auth_headers(tr_id)
        url = f"{self._base_url}{path}"
        resp = requests.get(url, headers=headers, params=params, timeout=config.REQUEST_TIMEOUT)
        return self._parse_response(resp, f"GET {path}")

    def _post(self, path: str, tr_id: str, body: dict[str, Any]) -> dict:
        self._throttle()
        headers = self._auth_headers(tr_id)
        url = f"{self._base_url}{path}"
        resp = requests.post(url, headers=headers, json=body, timeout=config.REQUEST_TIMEOUT)
        return self._parse_response(resp, f"POST {path}")

    @staticmethod
    def _parse_response(resp: requests.Response, context: str) -> dict:
        if resp.status_code != 200:
            raise RuntimeError(f"[KIS] {context} HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        rt_cd = data.get("rt_cd", "")
        # rt_cd "0" = 성공. 그 외는 msg1 포함 에러
        if rt_cd != "0":
            msg = data.get("msg1", "") or data.get("msg", "")
            raise RuntimeError(f"[KIS] {context} 응답 오류 rt_cd={rt_cd} msg={msg}")
        return data

    # -------------------------------------------------------------------------
    # place_order — Plan FR-03, SC-03/SC-04
    # -------------------------------------------------------------------------

    def place_order(
        self,
        symbol: str,
        action: Literal["BUY", "SELL"],
        shares: int,
        price: float | None = None,  # None → 시장가 (KIS 해외주식은 지정가 강제이므로 시세로 대체)
    ) -> OrderResult:
        """KIS 모의투자 주문 위임. 실패 시 OrderResult.status='REJECTED' 반환 (raise 아님)."""
        self._ensure_connected()
        if shares <= 0:
            return OrderResult(
                order_no="", status="REJECTED",
                fill_price=None, fill_shares=None,
                timestamp=_now_iso(), error_msg=f"invalid shares: {shares}",
            )

        last_err: str | None = None
        for attempt in range(_ORDER_RETRY_COUNT + 1):
            try:
                return self._submit_order(symbol, action, shares, price)
            except Exception as e:  # noqa: BLE001 — 모든 실패를 REJECTED로 정규화
                last_err = str(e)
                logger.warning(
                    f"[KIS] {action} {symbol} 주문 실패 (attempt {attempt+1}): {last_err}"
                )
                if attempt < _ORDER_RETRY_COUNT:
                    time.sleep(0.5)

        return OrderResult(
            order_no="", status="REJECTED",
            fill_price=None, fill_shares=None,
            timestamp=_now_iso(), error_msg=last_err,
        )

    def _submit_order(
        self,
        symbol: str,
        action: Literal["BUY", "SELL"],
        shares: int,
        price: float | None,
    ) -> OrderResult:
        """KIS 해외주식 주문 — POST /uapi/overseas-stock/v1/trading/order."""
        # KIS 해외주식은 지정가 주문 — price=None이면 현재가로 대체
        if price is None or price <= 0:
            price = self.get_quote(symbol)

        _, ovrs_excg = self._resolve_exchange(symbol)   # 종목 상장 거래소 (NASD/NYSE/AMEX)
        tr_id = _TR_ID_BUY_PAPER if action == "BUY" else _TR_ID_SELL_PAPER
        body = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._acnt_prdt_cd,
            "OVRS_EXCG_CD": ovrs_excg,
            "PDNO": symbol,
            "ORD_QTY": str(shares),
            "OVRS_ORD_UNPR": f"{price:.2f}",     # 지정가 (USD)
            "ORD_SVR_DVSN_CD": "0",              # 주문서버구분 — 0
            "ORD_DVSN": "00",                    # 주문구분 — 00=지정가
        }

        data = self._post("/uapi/overseas-stock/v1/trading/order", tr_id, body)
        output = data.get("output", {}) or {}
        order_no = str(output.get("ODNO", "") or output.get("odno", ""))

        if not order_no:
            error_msg = data.get("msg1", "") or "order_no missing"
            return OrderResult(
                order_no="", status="REJECTED",
                fill_price=None, fill_shares=None,
                timestamp=_now_iso(), error_msg=error_msg,
            )

        # KIS 주문 응답은 접수 확인일 뿐 체결 확인이 아니다. 실제 체결은
        # get_order_history()와 sync_from_kis()로 후속 확인한다.
        return OrderResult(
            order_no=order_no,
            status="PENDING",
            fill_price=None,
            fill_shares=None,
            timestamp=_now_iso(),
            error_msg=None,
        )

    # -------------------------------------------------------------------------
    # get_account — Plan FR-04
    # -------------------------------------------------------------------------

    def get_account(self) -> AccountSnapshot:
        """KIS 해외주식 잔고 — Source of Truth (Design §1.2).

        KIS는 잔고와 가용현금을 별도 endpoint로 분리 제공:
          - inquire-balance: 보유종목 + 평가손익
          - inquire-psamount: 외화 가용현금 (USD 예수금)
        두 호출 사이 ~1초 spacing으로 'EGW00201 초당 거래건수' 회피.
        """
        # 두 API 모두 _throttle()이 자동 spacing 처리 (EGW00201 회피)
        positions = self._fetch_positions()
        cash_usd = self._fetch_buyable_cash()
        return AccountSnapshot(
            cash_usd=cash_usd,
            positions=positions,
            updated_at=_now_iso(),
        )

    def get_order_history(self, start_date: str, end_date: str) -> list[FillRecord]:
        """KIS 주문체결내역을 조회한다. 날짜는 YYYYMMDD 형식이다."""
        self._ensure_connected()
        fk200 = nk200 = ""
        fills: list[FillRecord] = []
        seen_pages: set[tuple[str, str]] = set()

        for _ in range(10):
            params = {
                "CANO": self._cano,
                "ACNT_PRDT_CD": self._acnt_prdt_cd,
                # 모의투자는 전체 조회 파라미터를 공란으로만 허용한다.
                "PDNO": "",
                "ORD_STRT_DT": start_date,
                "ORD_END_DT": end_date,
                "SLL_BUY_DVSN": "00",
                "CCLD_NCCS_DVSN": "00",
                "OVRS_EXCG_CD": "",
                "SORT_SQN": "DS",
                "ORD_DT": "",
                "ORD_GNO_BRNO": "",
                "ODNO": "",
                "CTX_AREA_FK200": fk200,
                "CTX_AREA_NK200": nk200,
            }
            data = self._get(
                "/uapi/overseas-stock/v1/trading/inquire-ccnl",
                _TR_ID_ORDER_HISTORY_PAPER,
                params,
            )
            for row in data.get("output", []) or []:
                fill = self._parse_fill_record(row)
                if fill is not None:
                    fills.append(fill)

            next_fk = str(data.get("ctx_area_fk200", "") or "")
            next_nk = str(data.get("ctx_area_nk200", "") or "")
            page_key = (next_fk, next_nk)
            if not next_fk and not next_nk or page_key in seen_pages:
                break
            seen_pages.add(page_key)
            fk200, nk200 = next_fk, next_nk

        unique = {
            (f.timestamp[:10], f.order_no, f.symbol, f.action): f
            for f in fills
        }
        return sorted(unique.values(), key=lambda f: f.timestamp)

    @staticmethod
    def _parse_fill_record(row: dict[str, Any]) -> FillRecord | None:
        shares = int(float(row.get("ft_ccld_qty", 0) or 0))
        price = float(row.get("ft_ccld_unpr3", 0) or 0)
        action = {"01": "SELL", "02": "BUY"}.get(str(row.get("sll_buy_dvsn_cd", "")))
        symbol = str(row.get("pdno", "") or "").strip().upper()
        order_no = str(row.get("odno", "") or "").strip()
        if not action or not symbol or not order_no or shares <= 0 or price <= 0:
            return None

        raw_dt = f"{row.get('ord_dt', '')}{str(row.get('ord_tmd', '')).zfill(6)}"
        try:
            timestamp = (
                datetime.strptime(raw_dt, "%Y%m%d%H%M%S")
                .replace(tzinfo=ZoneInfo("Asia/Seoul"))
                .astimezone(timezone.utc)
                .isoformat()
            )
        except ValueError:
            timestamp = _now_iso()
        return FillRecord(
            order_no=order_no,
            symbol=symbol,
            action=action,
            fill_price=price,
            fill_shares=shares,
            timestamp=timestamp,
        )

    def _fetch_positions(self) -> dict[str, PositionSnapshot]:
        """해외주식 잔고조회 — 보유종목만.

        주문이 거래소별로 라우팅되므로 미국 3개 거래소(NASD/NYSE/AMEX)를
        모두 조회해야 완전한 스냅샷이다. 한 거래소라도 끝까지 실패하면
        부분 잔고를 반환하지 않고 예외를 올려 포트폴리오 캐시 덮어쓰기를 막는다.
        """
        positions: dict[str, PositionSnapshot] = {}
        for _excd, ovrs in _US_EXCHANGES:
            params = {
                "CANO": self._cano,
                "ACNT_PRDT_CD": self._acnt_prdt_cd,
                "OVRS_EXCG_CD": ovrs,
                "TR_CRCY_CD": "USD",
                "CTX_AREA_FK200": "",
                "CTX_AREA_NK200": "",
            }
            last_exc: Exception | None = None
            for attempt in range(_BALANCE_RETRY_COUNT + 1):
                try:
                    data = self._get(
                        "/uapi/overseas-stock/v1/trading/inquire-balance",
                        _TR_ID_BALANCE_PAPER,
                        params,
                    )
                    break
                except Exception as e:  # noqa: BLE001 — 부분 잔고 반환 방지
                    last_exc = e
                    logger.warning(
                        f"[KIS] {ovrs} 잔고조회 실패 "
                        f"(attempt {attempt + 1}/{_BALANCE_RETRY_COUNT + 1}): {e}"
                    )
                    if attempt < _BALANCE_RETRY_COUNT:
                        time.sleep(0.5)
            else:
                raise RuntimeError(
                    f"KIS balance snapshot incomplete: {ovrs} 잔고조회 실패"
                ) from last_exc

            for row in data.get("output1", []) or []:
                sym = str(row.get("ovrs_pdno", "") or row.get("pdno", ""))
                if not sym:
                    continue
                shares = int(float(row.get("ovrs_cblc_qty", 0) or 0))
                if shares <= 0:
                    continue
                positions[sym] = PositionSnapshot(
                    shares=shares,
                    avg_price=float(row.get("pchs_avg_pric", 0) or 0),
                    current_price=float(row.get("now_pric2", 0) or row.get("ovrs_now_pric1", 0) or 0),
                )
        return positions

    def _fetch_buyable_cash(self) -> float:
        """해외주식 매수가능금액조회 — USD 가용현금. ITEM_CD는 임의 종목(결과는 종목 무관)."""
        params = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._acnt_prdt_cd,
            "OVRS_EXCG_CD": _DEFAULT_EXCHANGE,
            "OVRS_ORD_UNPR": "0",
            "ITEM_CD": "AAPL",
        }
        data = self._get(
            "/uapi/overseas-stock/v1/trading/inquire-psamount",
            _TR_ID_PSAMOUNT_PAPER,
            params,
        )
        out = data.get("output", {}) or {}
        return float(
            out.get("ord_psbl_frcr_amt", 0)
            or out.get("ovrs_ord_psbl_amt", 0)
            or 0.0
        )

    # -------------------------------------------------------------------------
    # get_quote — Plan FR-18 폴백 대상
    # -------------------------------------------------------------------------

    def _fetch_price(self, symbol: str, excd: str) -> float:
        """해외주식 현재가 1회 조회 (지정 EXCD). 'last'(현재가)→'base'(전일종가) 폴백.
        해당 거래소에 종목이 없으면 output이 빈 문자열뿐 → 0.0 반환(거부 아님)."""
        params = {"AUTH": "", "EXCD": excd, "SYMB": symbol}
        data = self._get(
            "/uapi/overseas-price/v1/quotations/price", _TR_ID_QUOTE, params,
        )
        output = data.get("output", {}) or {}
        return float(output.get("last", 0) or output.get("base", 0) or 0)

    def _resolve_exchange(self, symbol: str) -> tuple[str, str]:
        """종목의 미국 거래소 판별 → (EXCD, OVRS_EXCG_CD).
        price API를 NAS→NYS→AMS 순으로 프로브해 시세가 잡히는 거래소를 영속 캐시.
        모두 실패하면 NASDAQ 기본값 반환(캐시 안 함 — 다음 호출 때 재프로브)."""
        sym = symbol.upper()
        cached = self._exchange_cache.get(sym)
        if cached:
            return cached
        for excd, ovrs in _US_EXCHANGES:
            try:
                if self._fetch_price(sym, excd) > 0:
                    self._exchange_cache[sym] = (excd, ovrs)
                    self._save_exchange_cache()
                    logger.debug(f"[KIS] {sym} 거래소 판별: {ovrs} (EXCD={excd})")
                    return (excd, ovrs)
            except Exception as e:  # noqa: BLE001 — 프로브 실패는 다음 거래소로
                logger.debug(f"[KIS] {sym} {excd} 프로브 실패: {e}")
        logger.warning(
            f"[KIS] {symbol} 거래소 판별 실패 — NASDAQ 기본 사용(사후 REJECTED 학습)"
        )
        return (_QUOTE_EXCHANGE, _DEFAULT_EXCHANGE)

    def _load_exchange_cache(self) -> dict[str, tuple[str, str]]:
        try:
            raw = json.loads(self._exchange_cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return {
            k: (v[0], v[1]) for k, v in raw.items()
            if isinstance(v, (list, tuple)) and len(v) == 2
        }

    def _save_exchange_cache(self) -> None:
        try:
            self._exchange_cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._exchange_cache_path.write_text(
                json.dumps({k: list(v) for k, v in self._exchange_cache.items()},
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning(f"[KIS] 거래소 캐시 저장 실패: {e}")

    def get_quote(self, symbol: str) -> float:
        """KIS 해외주식 현재가 — GET /uapi/overseas-price/v1/quotations/price.
        상장 거래소를 _resolve_exchange로 판별(NAS/NYS/AMS)해 조회."""
        excd, _ = self._resolve_exchange(symbol)
        try:
            price = self._fetch_price(symbol, excd)
        except Exception as e:
            logger.warning(f"[KIS] {symbol} 현재가 조회 실패: {e}")
            raise
        if price <= 0:
            raise RuntimeError(f"invalid quote response for {symbol} (EXCD={excd})")
        return price

    # -------------------------------------------------------------------------
    # get_tradable_symbols — Plan FR-14, SC-06
    # -------------------------------------------------------------------------

    def get_tradable_symbols(self) -> list[str]:
        """매매 가능 미국 종목 캐시. KIS_SYMBOLS_REFRESH_DAYS 만료 시에만 재검증."""
        cached = self._load_symbols_cache()
        if cached is not None:
            return cached

        # 캐시 만료 또는 미존재 → KIS 시세 API로 운용 종목 검증
        try:
            symbols = self._fetch_tradable_symbols()
        except Exception as e:
            logger.warning(f"[KIS] 매매 가능 종목 검증 실패 — 마지막 캐시로 폴백: {e}")
            stale = self._load_symbols_cache(allow_stale=True)
            if stale is None:
                # 검증 실패 + 캐시 없음 → config.SYMBOLS 전체 허용 (사후 거부 학습)
                logger.warning(
                    "[KIS] 매매 가능 종목 검증 미확보 — config.SYMBOLS를 매매 가능으로 가정. "
                    "REJECTED 응답으로 사후 학습 필요"
                )
                fallback = _normalize_symbols(config.SYMBOLS)
                self._save_symbols_cache(
                    fallback,
                    source="config_symbols_fallback",
                    confidence="assumed",
                    checked_symbols=fallback,
                    rejected_symbols=[],
                )
                return fallback
            logger.warning("[KIS] 만료된 종목 캐시 강제 사용")
            return stale

        self._save_symbols_cache(
            symbols,
            source="kis_quote_probe",
            confidence="verified_quote",
            checked_symbols=self._last_symbols_checked,
            rejected_symbols=self._last_symbols_rejected,
        )
        return symbols

    def _fetch_tradable_symbols(self) -> list[str]:
        """KIS 운용 대상 종목 검증.

        KIS OpenAPI는 현재 프로젝트에서 바로 쓰기 좋은 "전체 미국 종목 마스터"
        엔드포인트를 제공하지 않는다. 대신 실제 운용 대상(config.SYMBOLS)을 KIS
        해외주식 시세 API로 조회해, KIS가 인식하는 종목만 매매 후보 캐시에 남긴다.
        주문 가능성의 최종 판단은 주문 응답이 담당하지만, 기존의 무조건 폴백보다
        거래소/종목 매핑을 명시적으로 검증할 수 있다.
        """
        symbols = _normalize_symbols(config.SYMBOLS)
        if not symbols:
            raise RuntimeError("config.SYMBOLS가 비어 있어 KIS 종목 검증 불가")
        if not self._access_token:
            self.connect()

        tradable: list[str] = []
        rejected: list[str] = []
        for symbol in symbols:
            try:
                self.get_quote(symbol)
                tradable.append(symbol)
            except Exception as e:  # noqa: BLE001 — 종목별 검증 실패는 전체 실패가 아님
                rejected.append(symbol)
                logger.warning(f"[KIS] {symbol} 시세 검증 실패 — 매매 가능 후보 제외: {e}")

        self._last_symbols_checked = symbols
        self._last_symbols_rejected = rejected

        if not tradable:
            raise RuntimeError("KIS 시세 검증을 통과한 운용 종목 없음")
        logger.info(
            "[KIS] 매매 가능 종목 검증 완료: "
            f"{len(tradable)}/{len(symbols)}개 통과"
        )
        return tradable

    def _load_symbols_cache(self, allow_stale: bool = False) -> list[str] | None:
        data = _read_symbols_cache(self._symbols_cache_path)
        if data is None:
            return None

        if not allow_stale:
            try:
                updated_at = datetime.fromisoformat(data["updated_at"])
            except (KeyError, ValueError):
                return None
            age = datetime.now(timezone.utc) - updated_at
            if age > timedelta(days=config.KIS_SYMBOLS_REFRESH_DAYS):
                return None

        return list(data.get("tradable", []))

    def _save_symbols_cache(
        self,
        symbols: list[str],
        *,
        source: str = "unknown",
        confidence: str = "unknown",
        checked_symbols: list[str] | None = None,
        rejected_symbols: list[str] | None = None,
    ) -> None:
        self._symbols_cache_path.parent.mkdir(parents=True, exist_ok=True)
        tradable = _normalize_symbols(symbols)
        payload = {
            "updated_at": _now_iso(),
            "refresh_days": config.KIS_SYMBOLS_REFRESH_DAYS,
            "source": source,
            "confidence": confidence,
            "scope": "configured_symbols",
            "checked_symbols": _normalize_symbols(checked_symbols or tradable),
            "rejected_symbols": _normalize_symbols(rejected_symbols or []),
            "tradable": tradable,
        }
        self._symbols_cache_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


# =============================================================================
# Factory (Design §3.2 get_broker)
# =============================================================================

def get_broker() -> Broker:
    """Factory — config 환경변수로 KISBroker 인스턴스 반환."""
    return KISBroker(
        app_key=config.KIS_APP_KEY,
        app_secret=config.KIS_APP_SECRET,
        account_no=config.KIS_ACCOUNT_NO,
        paper=config.KIS_PAPER_TRADING,
    )


def load_cached_tradable_symbols() -> list[str] | None:
    """data/kis_symbols.json 캐시에서 매매 가능 종목 목록을 읽는다.

    Plan FR-14: signals 디스패처가 SYMBOLS ∩ tradable 필터링에 사용한다.
    Broker 인스턴스/토큰 없이 캐시 파일만 읽으므로 app.py 등 어디서나 호출 가능.

    Returns:
        매매 가능 종목 list — 캐시 미존재/손상/빈 목록이면 None (호출자는 무필터 폴백)
    """
    data = _read_symbols_cache(Path(config.KIS_SYMBOLS_FILE))
    if data is None:
        return None
    tradable = data.get("tradable", [])
    return list(tradable) if tradable else None


# =============================================================================
# Helpers
# =============================================================================

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_symbols(symbols: list[str] | tuple[str, ...]) -> list[str]:
    """종목 코드를 대문자/중복 제거 형태로 정규화한다."""
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in symbols:
        symbol = str(raw).strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        normalized.append(symbol)
    return normalized


def _read_symbols_cache(path: Path) -> dict | None:
    """종목 캐시 파일을 읽어 JSON dict로 반환. 미존재/손상 시 None.

    KISBroker._load_symbols_cache(만료 검사 포함)와 load_cached_tradable_symbols
    (broker 불필요)가 공유하는 파일 I/O 레이어.
    """
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"[KIS] 종목 캐시 읽기 실패: {e}")
        return None

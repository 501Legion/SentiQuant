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
    def get_quote(self, symbol: str) -> float: ...
    def get_tradable_symbols(self) -> list[str]: ...


# =============================================================================
# KIS OpenAPI 상수 (모의투자 도메인 전용)
# =============================================================================

# 토큰 만료 5분 전 선제 갱신 (Plan §5 Risk: 24h 만료 누락 방지)
_TOKEN_PREEMPT_REFRESH_SECONDS = 5 * 60

# place_order 네트워크 에러 시 1회 재시도 (Design §6 Error Handling)
_ORDER_RETRY_COUNT = 1

# 모의투자 tr_id (KIS Developers 포털 — 해외주식주문/잔고/시세)
_TR_ID_BUY_PAPER = "VTTT1002U"        # 미국주식 매수 — 모의
_TR_ID_SELL_PAPER = "VTTT1001U"       # 미국주식 매도 — 모의
_TR_ID_BALANCE_PAPER = "VTTS3012R"    # 미국주식 잔고 — 모의
_TR_ID_QUOTE = "HHDFS00000300"        # 해외주식 현재가 (실전/모의 공통)

# 거래소 코드 (해외주식 미국 시장)
# NAS=나스닥, NYS=뉴욕, AMS=아멕스. 종목별로 다름 — config.SYMBOLS 기본은 NASDAQ.
_DEFAULT_EXCHANGE = "NASD"
_QUOTE_EXCHANGE = "NAS"               # 시세 조회용 EXCD (price endpoint는 NAS 사용)


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

    def _get(self, path: str, tr_id: str, params: dict[str, Any]) -> dict:
        headers = self._auth_headers(tr_id)
        url = f"{self._base_url}{path}"
        resp = requests.get(url, headers=headers, params=params, timeout=config.REQUEST_TIMEOUT)
        return self._parse_response(resp, f"GET {path}")

    def _post(self, path: str, tr_id: str, body: dict[str, Any]) -> dict:
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

        tr_id = _TR_ID_BUY_PAPER if action == "BUY" else _TR_ID_SELL_PAPER
        body = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._acnt_prdt_cd,
            "OVRS_EXCG_CD": _DEFAULT_EXCHANGE,   # NASD=나스닥
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

        # 모의투자는 즉시 체결 가정 — 정확한 체결가/수량은 잔고 조회로 사후 확인
        return OrderResult(
            order_no=order_no,
            status="FILLED",
            fill_price=price,
            fill_shares=shares,
            timestamp=_now_iso(),
            error_msg=None,
        )

    # -------------------------------------------------------------------------
    # get_account — Plan FR-04
    # -------------------------------------------------------------------------

    def get_account(self) -> AccountSnapshot:
        """KIS 해외주식 잔고 — Source of Truth (Design §1.2)."""
        params = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._acnt_prdt_cd,
            "OVRS_EXCG_CD": _DEFAULT_EXCHANGE,
            "TR_CRCY_CD": "USD",
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": "",
        }
        data = self._get(
            "/uapi/overseas-stock/v1/trading/inquire-balance",
            _TR_ID_BALANCE_PAPER,
            params,
        )

        # output1: 보유종목 리스트, output2: 외화예수금/평가금액 요약
        output2 = data.get("output2", {}) or {}
        cash_usd = float(
            output2.get("frcr_dncl_amt1", 0)        # 외화예수금
            or output2.get("frcr_evlu_amt2", 0)
            or 0.0
        )

        positions: dict[str, PositionSnapshot] = {}
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

        return AccountSnapshot(
            cash_usd=cash_usd,
            positions=positions,
            updated_at=_now_iso(),
        )

    # -------------------------------------------------------------------------
    # get_quote — Plan FR-18 폴백 대상
    # -------------------------------------------------------------------------

    def get_quote(self, symbol: str) -> float:
        """KIS 해외주식 현재가 — GET /uapi/overseas-price/v1/quotations/price."""
        params = {
            "AUTH": "",
            "EXCD": _QUOTE_EXCHANGE,             # NAS=나스닥 (price API 전용 코드)
            "SYMB": symbol,
        }
        try:
            data = self._get(
                "/uapi/overseas-price/v1/quotations/price",
                _TR_ID_QUOTE,
                params,
            )
        except Exception as e:
            logger.warning(f"[KIS] {symbol} 현재가 조회 실패: {e}")
            raise

        output = data.get("output", {}) or {}
        # 'last' = 현재가, 'base' = 전일종가
        price = float(output.get("last", 0) or output.get("base", 0) or 0)
        if price <= 0:
            raise RuntimeError(f"invalid quote response for {symbol}: {output}")
        return price

    # -------------------------------------------------------------------------
    # get_tradable_symbols — Plan FR-14, SC-06
    # -------------------------------------------------------------------------

    def get_tradable_symbols(self) -> list[str]:
        """매매 가능 미국 종목 캐시. KIS_SYMBOLS_REFRESH_DAYS 만료 시에만 마스터 재조회."""
        cached = self._load_symbols_cache()
        if cached is not None:
            return cached

        # 캐시 만료 또는 미존재 → 마스터 재조회
        try:
            symbols = self._fetch_tradable_symbols()
        except Exception as e:
            logger.warning(f"[KIS] 종목 마스터 조회 실패 — 마지막 캐시로 폴백: {e}")
            stale = self._load_symbols_cache(allow_stale=True)
            if stale is None:
                # 마스터 미노출 + 캐시 없음 → config.SYMBOLS 전체 허용 (사후 거부 학습)
                logger.warning(
                    "[KIS] 종목 마스터 미확보 — config.SYMBOLS를 매매 가능으로 가정. "
                    "REJECTED 응답으로 사후 학습 필요"
                )
                fallback = list(config.SYMBOLS)
                self._save_symbols_cache(fallback)
                return fallback
            logger.warning("[KIS] 만료된 종목 캐시 강제 사용")
            return stale

        self._save_symbols_cache(symbols)
        return symbols

    def _fetch_tradable_symbols(self) -> list[str]:
        """KIS 종목 마스터 — 공식 OpenAPI는 종목 전체 마스터 직접 제공 안 함.
        대신 NASDAQ 마스터 파일 다운로드(별도 인프라) 또는 사후 학습 방식 사용.

        본 구현은 폴백 전략 — config.SYMBOLS 전체를 매매 가능으로 가정하고
        place_order가 REJECTED 응답을 받으면 호출자(trader)가 제외 학습.
        """
        # TODO: NASDAQ 마스터 파일 다운로드 또는 KIS 종목조회 API가 노출되면 교체
        raise RuntimeError("KIS 종목 마스터 직접 조회 미구현 — 폴백 경로 사용")

    def _load_symbols_cache(self, allow_stale: bool = False) -> list[str] | None:
        if not self._symbols_cache_path.exists():
            return None
        try:
            data = json.loads(self._symbols_cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"[KIS] 종목 캐시 읽기 실패: {e}")
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

    def _save_symbols_cache(self, symbols: list[str]) -> None:
        self._symbols_cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": _now_iso(),
            "refresh_days": config.KIS_SYMBOLS_REFRESH_DAYS,
            "tradable": symbols,
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


# =============================================================================
# Helpers
# =============================================================================

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

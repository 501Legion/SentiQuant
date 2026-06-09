"""S&P500 / Nasdaq-100 전체 구성종목을 Wikipedia에서 가져와
data/universe/{sp500,nasdaq100}.json 갱신.

실행: venv/Scripts/python.exe scripts/build_universe.py
출처: Wikipedia (List of S&P 500 companies / Nasdaq-100). 멤버십은 수시 변동 →
필요 시 재실행으로 갱신. 티커 정규화: Wikipedia 'BRK.B' 유지(Polygon 호환).
"""
import io
import json
import os
import sys

import pandas as pd
import requests

_HEADERS = {"User-Agent": "Mozilla/5.0 (auto_stock universe builder)"}
_OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "universe")


def _fetch_tables(url: str):
    r = requests.get(url, headers=_HEADERS, timeout=30)
    r.raise_for_status()
    return pd.read_html(io.StringIO(r.text))


def _clean(sym: str) -> str:
    return str(sym).strip().upper().replace("\xa0", "")


def build_sp500() -> list[str]:
    tables = _fetch_tables("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
    # 구성종목 표: 'Symbol' 컬럼 보유
    for t in tables:
        cols = [str(c) for c in t.columns]
        if "Symbol" in cols:
            syms = [_clean(s) for s in t["Symbol"].tolist()]
            return sorted({s for s in syms if s and s != "NAN"})
    raise RuntimeError("S&P500 Symbol 컬럼 표를 찾지 못함")


def build_nasdaq100() -> list[str]:
    tables = _fetch_tables("https://en.wikipedia.org/wiki/Nasdaq-100")
    # 구성종목 표: 'Ticker' 또는 'Symbol' 컬럼 보유
    for t in tables:
        cols = [str(c) for c in t.columns]
        key = "Ticker" if "Ticker" in cols else ("Symbol" if "Symbol" in cols else None)
        if key and len(t) >= 90:   # 100여 종목 표만
            syms = [_clean(s) for s in t[key].tolist()]
            return sorted({s for s in syms if s and s != "NAN"})
    raise RuntimeError("Nasdaq-100 Ticker 컬럼 표를 찾지 못함")


def _write(index: str, symbols: list[str]):
    path = os.path.join(_OUT_DIR, f"{index}.json")
    payload = {
        "index": index,
        "as_of": pd.Timestamp.now().strftime("%Y-%m-%d"),
        "source": "Wikipedia",
        "note": f"{index} 전체 구성종목 (scripts/build_universe.py 자동 생성). "
                "멤버십은 수시 변동 — 재실행으로 갱신. market_caps는 선택(빈 값=유동성 기반).",
        "symbols": symbols,
        "market_caps": {},
    }
    os.makedirs(_OUT_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"  {index}.json ← {len(symbols)} 종목")


def main() -> int:
    print("Universe 구성종목 수집 (Wikipedia)...")
    sp = build_sp500()
    nq = build_nasdaq100()
    _write("sp500", sp)
    _write("nasdaq100", nq)
    # 교차 확인
    overlap = len(set(sp) & set(nq))
    print(f"교집합(CORE 후보): {overlap} · S&P500 {len(sp)} · Nasdaq100 {len(nq)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

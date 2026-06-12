# Design Ref: community-opinion-agent-live §1·§2·§3.2 — 라이브 1일 구동 드라이버.
# 매일 1회 에이전트 파이프라인(snapshot→universe→cost→memory→router)을 실구동하고,
# action==BUY/SELL/EXIT/REDUCE를 KIS 모의투자 주문(dry-run 기본)에 연결한다.
#
# 핵심 제약 (Plan §8 / Design §5):
#   - dry-run 기본 → place_order 호출 0 (실모의주문은 --no-dry-run 명시 시에만)
#   - reddit_backtester / signals.py 불가침 (백테스트·뉴스 회귀 0)
#   - 에이전트 5모듈(universe/cost/memory/reflection/router) 재사용, agent_gate 순수 helper 호출
#   - 라이브는 영속 상태(score_history·memory jsonl·portfolio state) 사용
#   - LLM 보조 라우터 ON + 일일 호출 상한(COMMUNITY_LLM_LIVE_MAX_CALLS) → 초과 시 rule fallback
import json
import logging
import os
from datetime import date as _date, datetime, timedelta

import config
import indicators
import wsb_state
from agent_gate import evaluate_candidate
from cost_aware_trade_filter import CostAwareTradeFilter
from community_memory import CommunityMemoryStore
from decision_log import (
    append_decision_log,
    build_decision_record,
    decision_log_path,
    make_decision_id,
)
from decision_router import DecisionRouter
from opinion_reflection import build_high_level, build_low_level
from reddit_collector import RedditCollector
from reddit_portfolio import RedditPortfolio
from sentiment_provider import get_provider
from universe_filter import UniverseFilter
from wsb_signal_engine import WSBSignalEngine, build_daily_snapshot

logger = logging.getLogger(__name__)

# 라이브 전략 정체성 (백테스트 검증 완료 전략: finbert-wsb / sentiment / opinion_trend)
_LIVE_MODEL = "finbert-wsb"
_LIVE_RANKING = "sentiment"
_LIVE_SIZING = "opinion_trend"


def append_run_summary(summary: dict, path: str = None) -> None:
    """라이브 실행 1회 요약을 jsonl에 저장한다. 후보 0개인 날도 대시보드 날짜 갱신에 사용."""
    path = path or config.COMMUNITY_LIVE_RUN_SUMMARIES_FILE
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        rec = dict(summary)
        rec["created_at"] = datetime.now().astimezone().isoformat()
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"run summary append 실패(무시): {e}")


# =============================================================================
# OrderExecutor (Design §3.2) — dry-run이면 의도만 로그, 아니면 KIS 모의주문
# =============================================================================
class OrderExecutor:
    """OrderIntent → 실행. dry_run=True면 place_order 미호출(의도만 로그, SC-02).
    dry_run=False면 broker.place_order(symbol, side, shares) (KIS 해외주식 모의, SC-03)."""

    def __init__(self, broker=None, dry_run: bool = True):
        self.broker = broker
        self.dry_run = dry_run
        self.placed: list[dict] = []

    def execute(self, intent) -> dict:
        if not intent.side or intent.shares <= 0:
            return {"symbol": intent.symbol, "executed": False, "reason": "no_order"}

        if self.dry_run:
            logger.info(
                f"[DRY-RUN] 주문의도 {intent.side} {intent.symbol} x{intent.shares} "
                f"(action={intent.action}, size_factor={intent.size_factor}) — 실주문 없음"
            )
            rec = {"symbol": intent.symbol, "side": intent.side, "shares": intent.shares,
                   "executed": False, "dry_run": True, "decision_id": intent.decision_id}
            self.placed.append(rec)
            return rec

        # 실모의주문 — broker는 명시적으로 켰을 때만 사용 (FR-20: 실자금은 KIS_PAPER_TRADING 차단)
        if self.broker is None:
            from kis_broker import get_broker
            self.broker = get_broker()
            self.broker.connect()
        result = self.broker.place_order(intent.symbol, intent.side, intent.shares)
        rec = {"symbol": intent.symbol, "side": intent.side, "shares": intent.shares,
               "executed": getattr(result, "status", "") == "FILLED",
               "dry_run": False, "decision_id": intent.decision_id,
               "order_no": getattr(result, "order_no", ""),
               "status": getattr(result, "status", ""),
               "fill_price": getattr(result, "fill_price", None)}
        self.placed.append(rec)
        logger.info(
            f"[LIVE] place_order {intent.side} {intent.symbol} x{intent.shares} "
            f"→ {rec['status']} (order_no={rec['order_no']})"
        )
        return rec


# =============================================================================
# OHLCV (라이브: 최근 ~100일 캐시 슬라이스 — backtester._get_ohlcv_snapshot 재사용)
# =============================================================================
def _recent_cached_ohlcv(sym: str, end_date: str, max_age_days: int):
    """종목의 최근 ohlcv 스냅샷이 max_age_days 내면 재사용(정확 범위 무관). 없으면 None.
    # live-scheduler: 매일 end_date가 바뀌어도 최근 캐시 재사용 → Polygon 429 회피."""
    import glob
    import os
    import pandas as pd
    from backtester import _normalize_ohlcv_df

    paths = glob.glob(os.path.join(config.BACKTEST_SNAPSHOT_DIR, "v2", "ohlcv", f"{sym}_*.csv"))
    if not paths:
        return None

    def _end_tag(p):  # 파일명 끝 토큰 = end date(YYYY-MM-DD)
        return os.path.basename(p)[:-4].rsplit("_", 1)[-1]

    latest = max(paths, key=_end_tag)
    try:
        age = (datetime.strptime(end_date, "%Y-%m-%d")
               - datetime.strptime(_end_tag(latest), "%Y-%m-%d")).days
    except ValueError:
        return None
    if age < 0 or age > max_age_days:
        return None
    try:
        df = pd.read_csv(latest)
    except Exception:  # noqa: BLE001
        return None
    return _normalize_ohlcv_df(df) if not df.empty else None


def _fetch_ohlcv_full(symbols, end_date: str) -> dict:
    """종목별 (end_date-120일 ~ end_date) OHLCV DataFrame. 최근 캐시 재사용 + 신규만 throttle 수집."""
    import time
    from backtester import _get_ohlcv_snapshot

    start = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=120)).strftime("%Y-%m-%d")
    out: dict[str, "pd.DataFrame"] = {}
    fetched = 0
    for sym in sorted(set(symbols)):
        cached = _recent_cached_ohlcv(sym, end_date, config.LIVE_OHLCV_CACHE_MAX_AGE_DAYS)
        if cached is not None:
            out[sym] = cached
            continue
        try:
            if fetched > 0 and config.POLYGON_REQUEST_DELAY > 0:
                time.sleep(config.POLYGON_REQUEST_DELAY)   # 무료 플랜 분당 5회 회피
            df = _get_ohlcv_snapshot(sym, start, end_date)
            fetched += 1
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[{sym}] OHLCV 수집 실패: {e} — 제외")
            df = None
        if df is not None and not getattr(df, "empty", True):
            out[sym] = df
    logger.info(f"[OHLCV] 캐시재사용 {len(out)-fetched} · 신규수집 {fetched}"
                f"(throttle {config.POLYGON_REQUEST_DELAY}s)")
    return out


def _slice_cache(ohlcv_full: dict, symbols, date_str: str) -> dict:
    """run_pipeline용: 종목별 date_str 이하 DataFrame 슬라이스."""
    cache = {}
    for sym in symbols:
        full = ohlcv_full.get(sym)
        if full is None or getattr(full, "empty", True):
            continue
        sliced = full[full["date"] <= date_str]
        if not sliced.empty:
            cache[sym] = sliced
    return cache


def _today_cache(ohlcv_full: dict, symbols, date_str: str) -> dict:
    """스칼라 OHLCV {open, close, prev_close, rsi}.
    라이브 보강(①): 09:35 ET엔 당일 일봉이 미마감 → **최신 가용 봉**(어제 종가)을 proxy로
    사용한다. 실제 체결가는 _resolve_live_prices가 broker.get_quote로 덮어쓴다."""
    cache: dict[str, dict] = {}
    for sym in symbols:
        full = ohlcv_full.get(sym)
        if full is None or getattr(full, "empty", True):
            continue
        sliced = full[full["date"] <= date_str].reset_index(drop=True)
        if sliced.empty:
            continue
        last = sliced.iloc[-1]
        is_today = (last["date"] == date_str)
        prev_close = float(sliced.iloc[-2]["close"]) if len(sliced) >= 2 else None
        rsi, _ = indicators.get_latest_rsi(sym, sliced)
        cache[sym] = {
            # 당일 봉이 있으면 그대로, 없으면 최신 종가를 open/close proxy로
            "open": float(last["open"]) if is_today else float(last["close"]),
            "close": float(last["close"]),
            "prev_close": prev_close if is_today else float(last["close"]),
            "rsi": rsi, "stale": not is_today,
        }
    return cache


def _resolve_live_prices(today_ohlcv: dict, symbols, broker) -> dict:
    """라이브 보강(①): broker가 있으면 실시간 현재가(get_quote)로 open/close/price를 덮어쓴다.
    broker 없으면(dry-run 등) today_ohlcv proxy 그대로. 실패 종목은 proxy 유지."""
    if broker is None:
        return today_ohlcv
    for sym in symbols:
        try:
            q = float(broker.get_quote(sym))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[{sym}] get_quote 실패 — proxy 가격 유지: {e}")
            continue
        if q <= 0:
            continue
        cur = today_ohlcv.get(sym, {})
        cur["open"] = q          # 09:35 체결 기준가 = 실시간 현재가
        cur["price"] = q
        cur.setdefault("prev_close", cur.get("close"))
        cur["close"] = q
        cur["stale"] = False
        today_ohlcv[sym] = cur
    return today_ohlcv


# =============================================================================
# Reflection (FR-09) — 청산분 high-level + forward 확정분 low-level (storage-only)
# =============================================================================
def _forward_prices(df, base_date: str) -> tuple[float, dict] | None:
    """base_date 종가(entry) + 이후 1/3/7/14 거래일 종가 → (entry_price, {1,3,7,14: price}).
    14거래일이 아직 안 지났으면(미확정) None."""
    rows = df[df["date"] <= base_date]
    if rows.empty or rows.iloc[-1]["date"] != base_date:
        return None
    base_idx = len(rows) - 1
    full = df.reset_index(drop=True)
    if base_idx + 14 >= len(full):       # forward 14일 미확정
        return None
    entry = float(full.iloc[base_idx]["close"])
    fp = {n: float(full.iloc[base_idx + n]["close"]) for n in (1, 3, 7, 14)}
    return entry, fp


def _build_reflections(memory, ohlcv_full: dict, today: str,
                       sell_trades: list, snap_by_key: dict) -> dict:
    """청산 trade → HighLevelReflection, forward 확정 snapshot → LowLevelReflection.
    flag OFF면 no-op (회귀 0). 부수효과는 memory append + (없음). decision_id join."""
    counts = {"high": 0, "low": 0}
    if not config.COMMUNITY_REFLECTION_ENABLED:
        return counts

    # High-level: 오늘 청산분 (entry/exit snapshot + trade record)
    if config.COMMUNITY_HIGH_LEVEL_REFLECTION_ENABLED:
        snaps = None
        for tr in sell_trades:
            sym = tr.get("symbol")
            entry_date = tr.get("entry_date", "")
            entry_snap = snap_by_key.get((sym, entry_date))
            if entry_snap is None:               # 과거 진입 → 영속 snapshot에서 조회
                if snaps is None:
                    snaps = {(s.get("symbol"), s.get("date")): s
                             for s in wsb_state.load_daily_snapshots()}
                entry_snap = snaps.get((sym, entry_date), {})
            exit_snap = snap_by_key.get((sym, today), entry_snap)
            try:
                refl = build_high_level(entry_snap, exit_snap, tr,
                                        decision_id=tr.get("entry_decision_id", ""))
                memory.add_high_level_reflection(refl)
                counts["high"] += 1
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[{sym}] high-level reflection 실패: {e}")

    # Low-level: forward 14거래일 확정된 단일 cohort (today 기준 14거래일 전)
    if config.COMMUNITY_LOW_LEVEL_REFLECTION_ENABLED and ohlcv_full:
        all_dates = sorted({d for df in ohlcv_full.values()
                            for d in df["date"].tolist() if d <= today})
        if len(all_dates) >= 15:
            cohort = all_dates[-15]              # today=all_dates[-1] 기준 14거래일 전
            for snap in wsb_state.load_daily_snapshots():
                if snap.get("date") != cohort:
                    continue
                sym = snap.get("symbol")
                df = ohlcv_full.get(sym)
                if df is None or getattr(df, "empty", True):
                    continue
                fp = _forward_prices(df, cohort)
                if fp is None:
                    continue
                entry_price, forward_prices = fp
                try:
                    refl = build_low_level(snap, forward_prices, entry_price,
                                           decision_id=snap.get("decision_id", ""))
                    memory.add_low_level_reflection(refl)
                    counts["low"] += 1
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[{sym}] low-level reflection 실패: {e}")
    return counts


# =============================================================================
# run_live — 라이브 1일 구동
# =============================================================================
def run_live(
    date: str = None,
    dry_run: bool = None,
    llm_router: bool = None,
    universe_mode: str = None,
    *,
    broker=None,
    posts_by_symbol: dict = None,   # 테스트/오프라인 주입 (None → load_posts)
    ohlcv_full: dict = None,        # 테스트/오프라인 주입 (None → fetch)
    portfolio: RedditPortfolio = None,
    memory: CommunityMemoryStore = None,
) -> dict:
    """라이브 1일 구동 → {decisions, orders, decision_log_path, summary}.

    dry_run 기본 = config.COMMUNITY_LIVE_DRY_RUN_DEFAULT (True) → 실주문 0.
    뉴스/백테스트 경로 비침습 (신규 드라이버, LIVE_STRATEGY 스위치로 호출).
    """
    if date is None:
        date = _date.today().isoformat()
    if dry_run is None:
        dry_run = config.COMMUNITY_LIVE_DRY_RUN_DEFAULT
    if universe_mode is None:
        universe_mode = config.COMMUNITY_LIVE_UNIVERSE_MODE

    label = "DRY-RUN" if dry_run else "LIVE(모의주문)"
    logger.info(f"=== community_live.run_live 시작 ({date}, {label}, universe={universe_mode}) ===")

    # 1. posts (영속 로드). 보강②: 오늘 수집분 없으면 최근 수집일 글 사용
    #    (전일 16:30 신호잡 수집 → 익일 09:35 주문잡이 그 여론으로 시가 매매)
    posts_date = date
    if posts_by_symbol is None:
        posts_by_symbol = RedditCollector.load_posts(date)
        if not posts_by_symbol:
            prior = RedditCollector.discover_dates("2000-01-01", date)
            if prior:
                posts_date = prior[-1]
                posts_by_symbol = RedditCollector.load_posts(posts_date)
                # stale 가시화: 캐시가 며칠 전 것인지 명시. 한도 초과 시 WARNING (조용한 stale 방지)
                try:
                    age_days = (_date.fromisoformat(date) - _date.fromisoformat(posts_date)).days
                except ValueError:
                    age_days = -1
                if age_days > config.COMMUNITY_LIVE_MAX_POSTS_AGE_DAYS:
                    logger.warning(
                        f"오늘({date}) 수집분 없음 → 최근 수집일({posts_date}, {age_days}일 전) 여론 사용"
                        f" — 한도 {config.COMMUNITY_LIVE_MAX_POSTS_AGE_DAYS}일 초과(stale)."
                        f" 신선한 수집 권장: python main.py --reddit-run-now"
                    )
                else:
                    logger.info(f"오늘({date}) 수집분 없음 → 최근 수집일({posts_date}, {age_days}일 전) 여론 사용")
        if not posts_by_symbol:
            try:
                posts_by_symbol = RedditCollector().collect(date) or {}
                posts_date = date
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Reddit 수집 실패: {e}")
                posts_by_symbol = {}
    if not posts_by_symbol:
        logger.warning("Reddit 게시글 없음 — 빈 결과 반환")
        summary = {"date": date, "posts_date": posts_date, "candidates": 0,
                   "buys": 0, "sells": 0, "llm_calls": 0,
                   "dry_run": dry_run, "placed": 0,
                   "reflections": {"high": 0, "low": 0}}
        append_run_summary(summary)
        return {"decisions": [], "orders": [], "decision_log_path": decision_log_path(live=True),
                "summary": summary}

    # 2. 영속 상태 로드
    history = wsb_state.load_score_history()                 # 영속 score_history
    if memory is None:
        memory = CommunityMemoryStore()                      # 영속 jsonl backend
    if portfolio is None:
        portfolio = RedditPortfolio(config.COMMUNITY_LIVE_STRATEGY_KEY)

    # 3. 신호 엔진 파이프라인
    provider = get_provider(_LIVE_MODEL)
    engine = WSBSignalEngine(provider, ranking=_LIVE_RANKING)

    watch = set(posts_by_symbol) | set(portfolio.positions)
    if ohlcv_full is None:
        ohlcv_full = _fetch_ohlcv_full(watch, date)
    df_cache = _slice_cache(ohlcv_full, watch, date)
    today_ohlcv = _today_cache(ohlcv_full, watch, date)

    # 보강①: 라이브(실모의주문)면 broker 실시간 현재가로 체결가 보정.
    #   09:35 ET엔 당일 일봉이 미마감 → get_quote로 정확한 시가/현재가 확보.
    #   dry-run이고 broker 미주입이면 today_ohlcv proxy(최신 종가) 사용.
    if not dry_run and broker is None:
        try:
            from kis_broker import get_broker
            broker = get_broker()
            broker.connect()
        except Exception as e:  # noqa: BLE001
            logger.error(f"[KIS] Broker 초기화 실패 — 라이브 가격/주문 불가: {e}")
    today_ohlcv = _resolve_live_prices(today_ohlcv, watch, broker)

    top_n, signal_details = engine.run_pipeline(posts_by_symbol, df_cache, date)
    scored = {d["symbol"]: d for d in signal_details}

    # 4. score_history 영속 누적 (라이브: 전역 파일 갱신)
    for sym, d in scored.items():
        wsb_state.update_score_entry(history, sym, {
            "date": date, "score": d["score"], "bullish": d["bullish"],
            "bearish": d["bearish"], "neutral": d["neutral"],
            "neutral_ratio": d["neutral_ratio"],
        })
    wsb_state.save_score_history(history)

    # 5. 에이전트 모듈 (universe/cost/memory/router) + LLM 일일 상한 가드
    universe_filter = UniverseFilter(universe_mode)
    cost_filter = CostAwareTradeFilter()
    router = DecisionRouter(llm_router=(bool(llm_router) if llm_router is not None else None))
    llm_cap = config.COMMUNITY_LLM_LIVE_MAX_CALLS
    llm_calls = 0

    run_id = f"{config.COMMUNITY_LIVE_STRATEGY_KEY}_{universe_mode}_{date}"
    log_path = decision_log_path(live=True)
    run_meta = {"date": date, "source": "reddit", "model": _LIVE_MODEL,
                "ranking": _LIVE_RANKING, "sizing": _LIVE_SIZING,
                "universe_mode": universe_mode, "run_id": run_id}

    # 계좌 equity (사이징 기준) — 라이브 포트폴리오 미러 (cash + 보유 평가액)
    account_equity = portfolio.cash + sum(
        pos.shares * today_ohlcv.get(sym, {}).get("close", pos.entry_price)
        for sym, pos in portfolio.positions.items()
    )

    # 6. 후보 평가 (top_n ∪ 보유) — 모든 판단 영속 로그
    candidates = list(dict.fromkeys(list(top_n) + list(portfolio.positions)))
    decisions: list[dict] = []
    buy_intents: list = []
    sell_intents: list = []
    snap_by_key: dict[tuple, object] = {}      # (sym, date) → snapshot (reflection join용)

    position_scores = wsb_state.load_position_scores()

    for sym in candidates:
        d = scored.get(sym)
        if d is None:
            continue  # 보유 중이나 오늘 게시글 없음 → check_exit(5단계)가 별도 처리
        hist = history.get(sym, [])
        posts = posts_by_symbol.get(sym, [])
        texts = [f"{p.get('title', '')} {p.get('body_excerpt', '')}" for p in posts]
        pos = portfolio.positions.get(sym)
        current_position = {"symbol": sym, "shares": pos.shares} if pos else None
        t = today_ohlcv.get(sym, {})
        open_price = t.get("open") or t.get("close") or 0.0

        decision, intent, snap = evaluate_candidate(
            symbol=sym, scored_entry=d, history=hist, run_meta=run_meta,
            universe_filter=universe_filter, cost_filter=cost_filter,
            memory=memory, router=router,
            open_price=open_price, account_equity=account_equity,
            ohlcv=df_cache.get(sym), price=t.get("close"), rsi=t.get("rsi"),
            current_position=current_position, texts=texts,
        )
        snap_by_key[(sym, date)] = snap

        # snapshot 영속 누적 (Design §2/§4, Plan D5 — 라이브 memory 성장)
        try:
            wsb_state.append_daily_snapshot(snap)
            memory.add_opinion_snapshot(snap)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[{sym}] snapshot 영속 실패: {e}")

        # DecisionLog 영속 저장 (BUY/SKIP/HOLD/SELL/... 모두 — FR-05/SC-04, snapshot 보강)
        record = build_decision_record(
            decision=decision, snapshot=snap, date=date, symbol=sym, source="reddit",
            model=_LIVE_MODEL, ranking=_LIVE_RANKING, sizing=_LIVE_SIZING,
            universe_mode=universe_mode, run_id=run_id,
            current_signal=d.get("signal", ""), llm_enabled=router.llm_router,
            llm_model=(config.GPT_MODEL if router.llm_router else ""),
        )
        append_decision_log(record, path=log_path)
        decisions.append({"symbol": sym, "action": decision.action,
                          "size_factor": intent.size_factor,
                          "decision_id": intent.decision_id,
                          "router_mode": getattr(decision, "router_mode", "rule_based")})

        # LLM 일일 상한 — 초과 시 rule-only로 강등 (FR-06/SC-05)
        if getattr(decision, "router_mode", "") == "llm_assisted":
            llm_calls += 1
            if llm_calls >= llm_cap and router.llm_router:
                router.llm_router = False
                logger.warning(
                    f"[LLM] 일일 호출 상한 {llm_cap} 도달 — 이후 rule-based로 강등")

        if intent.action == "BUY" and pos is None:
            buy_intents.append((intent, open_price))
        elif intent.action in ("SELL", "EXIT", "REDUCE") and pos is not None:
            sell_intents.append((intent, t.get("close") or open_price, decision.action))

    # 7. check_exit 5단계 안전망 (보유 포지션) — 라우터 미감지분 보강 (NFR-04)
    sell_syms = {i.symbol for i, _, _ in sell_intents}
    for sym in list(portfolio.positions.keys()):
        if sym in sell_syms:
            continue
        t = today_ohlcv.get(sym, {})
        should_exit, reason = engine.check_exit(
            position={"symbol": sym, "entry_price": portfolio.positions[sym].entry_price,
                      "highest_price": portfolio.positions[sym].highest_price,
                      "shares": portfolio.positions[sym].shares},
            today_ohlcv=t, scored=scored, ohlcv_cache=df_cache,
            position_scores=position_scores,
            velocity_state=scored.get(sym, {}).get("velocity_state", "NORMAL"),
            opinion_mode=True,
        )
        if should_exit:
            from agent_gate import OrderIntent
            shares = portfolio.positions[sym].shares
            dec_id = make_decision_id(date, sym, "reddit", _LIVE_MODEL,
                                      _LIVE_RANKING, _LIVE_SIZING, universe_mode)
            intent = OrderIntent(symbol=sym, action="EXIT", side="SELL", shares=shares,
                                 size_factor=0.0, decision_id=dec_id, reason=reason)
            sell_intents.append((intent, t.get("close") or portfolio.positions[sym].entry_price, "EXIT"))
    wsb_state.save_position_scores(position_scores)

    # 8. 주문 실행 (매도 먼저 → 현금 확보 → 매수) + 포트폴리오 미러 갱신
    executor = OrderExecutor(broker=broker, dry_run=dry_run)
    orders: list[dict] = []

    sell_trades: list[dict] = []
    for intent, price, action in sell_intents:
        orders.append(executor.execute(intent))
        trade = portfolio._sell(intent.symbol, price, date, reason=action.lower())  # 미러 갱신
        if trade:
            sell_trades.append(trade)
        wsb_state.remove_position_score(position_scores, intent.symbol)

    # live-scheduler-deploy §6.2 D2 — 매수 실행 전 일일/노출 한도 게이트 (Plan SC-04)
    #   매도 실행 직후라 portfolio.positions가 현재 보유를 반영. today_buy_count=0(런당 상한);
    #   교차-런 누적은 절대 노출%(현재 포지션 기준)가 방어. 신호/사이징 판단은 불변.
    import runtime_guard
    _pos_val = {s: p.shares * (today_ohlcv.get(s, {}).get("close") or p.entry_price)
                for s, p in portfolio.positions.items()}
    buy_intents, _blocked = runtime_guard.filter_by_limits(
        buy_intents, equity=account_equity, positions_value=sum(_pos_val.values()),
        position_value_by_symbol=_pos_val, today_buy_count=0)
    if _blocked:
        logger.info(f"[한도 게이트] 매수 차단 {len(_blocked)}건: {_blocked}")

    for intent, price in buy_intents:
        if price <= 0 or intent.shares <= 0:
            continue
        orders.append(executor.execute(intent))
        portfolio._buy(intent.symbol, price, intent.shares, date)  # 미러 갱신

    # 8.5 reflection (FR-09): 청산분 → high-level, forward 확정분 → low-level (decision_id join)
    refl_counts = _build_reflections(memory, ohlcv_full, date, sell_trades, snap_by_key)

    # 9. 영속 상태 저장
    try:
        portfolio.save_state(date, ohlcv=today_ohlcv)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"포트폴리오 상태 저장 실패: {e}")

    summary = {
        "date": date, "posts_date": posts_date, "candidates": len(decisions),
        "buys": sum(1 for o in orders if o.get("side") == "BUY"),
        "sells": sum(1 for o in orders if o.get("side") == "SELL"),
        "llm_calls": llm_calls, "dry_run": dry_run,
        "placed": sum(1 for o in orders if o.get("executed")),
        "reflections": refl_counts,
    }
    append_run_summary(summary)
    logger.info(f"=== run_live 완료 — {summary} ===")

    # 10. 일일 결정 보고서 (daily-decision-report §6.2) — read-only, 비침습(D3/NFR-01).
    #     보고서 생성 실패가 run_live 결과·주문에 영향 0 (try/except 격리).
    report_path = None
    try:
        from decision_report import ReportContext, build_daily_report
        report_path = build_daily_report(ReportContext(
            date=date, signal_details=signal_details, decisions=decisions,
            orders=orders, snapshots=snap_by_key, summary=summary,
        ))
    except Exception as e:  # noqa: BLE001 — 보고서 실패 ≠ 매매 실패
        logger.warning(f"decision report 생성 실패(무시): {e}")

    return {"decisions": decisions, "orders": orders,
            "decision_log_path": log_path, "summary": summary,
            "report_path": report_path}

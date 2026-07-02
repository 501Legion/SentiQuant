# Design Ref: §2.7 — APScheduler BlockingScheduler, ET 기준 2개 잡, NYSE 휴장일 제외
# Plan SC-01: 매 거래일 신호 자동 생성
# Plan SC-06: NYSE 휴장일에는 스케줄러 실행 안 함
import logging
from datetime import datetime, timedelta

import pandas_market_calendars as mcal
import pytz
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

import config
import collector
import notifier
import runtime_guard
import signals as sig_module
import trader
from kis_broker import get_broker
from portfolio import (
    load_portfolio,
    load_signals,
    print_portfolio_report,
    reconcile_trades_from_kis,
    record_trade,
    save_portfolio,
    save_signals,
    sync_from_kis,
)

logger = logging.getLogger(__name__)
ET = pytz.timezone(config.TIMEZONE)


def is_trading_day(dt: datetime = None) -> bool:
    """
    NYSE 캘린더로 해당 날짜가 거래일인지 확인한다.
    Plan SC-06: 휴장일에는 잡이 실행되지 않아야 함.
    """
    if dt is None:
        dt = datetime.now(ET)
    date_str = dt.strftime("%Y-%m-%d")
    nyse = mcal.get_calendar("NYSE")
    schedule = nyse.schedule(start_date=date_str, end_date=date_str)
    return not schedule.empty


def _fetch_recent_kis_fills(broker, lookback_days: int = 14, chunk_days: int = 3):
    """KIS 체결내역을 짧은 날짜 구간으로 나눠 조회한다.

    모의투자 체결 조회는 넓은 기간을 한 번에 조회하면 일부 오래된 체결만
    반환될 수 있어 최신 체결이 누락된다. 스케줄러 정합화는 최근 체결만
    필요하므로 짧은 구간을 합쳐서 사용한다.
    """
    now = datetime.now(ET).date()
    start = now - timedelta(days=max(lookback_days - 1, 0))
    chunk_days = max(int(chunk_days or 1), 1)

    fills = []
    cursor = start
    while cursor <= now:
        chunk_end = min(cursor + timedelta(days=chunk_days - 1), now)
        start_label = cursor.strftime("%Y%m%d")
        end_label = chunk_end.strftime("%Y%m%d")
        try:
            fills.extend(broker.get_order_history(start_label, end_label))
        except Exception as e:
            logger.warning(
                "[KIS] 체결내역 구간 조회 실패 (%s~%s): %s",
                start_label, end_label, e,
            )
        cursor = chunk_end + timedelta(days=1)

    unique = {
        (f.timestamp[:10], f.order_no, f.symbol, f.action): f
        for f in fills
    }
    return sorted(unique.values(), key=lambda f: f.timestamp)


def signal_calculation_job() -> None:
    """
    매일 SIGNAL_JOB_HOUR:MINUTE ET 실행 (timing-fix: 08:45 ET — 장 시작 직전 수집).
    1. NYSE 휴장일 체크
    2. agent: Reddit 수집 / news: 모든 종목 신호 계산
    3. signals.json 저장 (news 경로)
    """
    logger.info(
        f"=== 신호 계산 잡 시작 ({config.SIGNAL_JOB_HOUR:02d}:{config.SIGNAL_JOB_MINUTE:02d} ET) ==="
    )

    if not is_trading_day():
        logger.info("오늘은 NYSE 휴장일 — 신호 계산 잡 스킵")
        return

    # community-opinion-agent-live FR-03/D2: LIVE_STRATEGY="agent"이면 에이전트 경로로 분기.
    # "news"(기본 외)면 아래 기존 뉴스-RSI 신호 계산이 그대로 실행 (회귀 0).
    if config.LIVE_STRATEGY == "agent":
        logger.info("[LIVE_STRATEGY=agent] 신호 잡 → Reddit 수집 (에이전트 입력 준비)")
        try:
            from reddit_collector import RedditCollector
            RedditCollector().collect()
            runtime_guard.write_heartbeat("signal")   # 성공 시각 기록(워치독)
        except Exception as e:
            logger.error(f"[agent] Reddit 수집 실패: {e}", exc_info=True)
            notifier.notify("error", f"agent Reddit 수집 실패: {e}")
        logger.info("=== 신호 계산 잡 완료 (agent) ===")
        return

    try:
        # FR-14/SC-06: KIS 매매 가능 종목 마스터 갱신 — data/kis_symbols.json 캐시 생성/갱신.
        # 신호 생성 시 generate_signals_for_all이 이 캐시로 종목을 필터링한다.
        try:
            get_broker().get_tradable_symbols()
        except Exception as e:
            logger.warning(f"[KIS] 종목 마스터 갱신 실패 — 기존 캐시/무필터로 진행: {e}")

        signals = sig_module.generate_signals_for_all(config.SYMBOLS)
        if signals:
            save_signals(signals)
            print(sig_module.format_signals_summary(signals))
            logger.info(f"신호 계산 완료: {len(signals)}개 종목")
        else:
            logger.warning("신호 계산 결과 없음 (API 오류 또는 데이터 부족)")
    except Exception as e:
        logger.error(f"신호 계산 잡 실패: {e}", exc_info=True)

    logger.info("=== 신호 계산 잡 완료 ===")


def order_processing_job(dry_run: bool = False) -> None:
    """
    매일 09:35 ET 실행. Plan FR-05~07, FR-11~13, FR-20.
    1. NYSE 휴장일 체크
    2. 전날 신호 로드
    3. KIS Broker 인스턴스화 + connect
    4. trader.process_orders로 KIS에 주문 위임 (dry_run 옵션)
    5. portfolio = sync_from_kis(broker) — Source of Truth 갱신
    6. 캐시 저장 (save_portfolio) + 거래 이력 기록
    7. 리포트 출력
    """
    label = "DRY-RUN" if dry_run else "LIVE"
    logger.info(
        f"=== 주문 처리 잡 시작 ({config.ORDER_JOB_HOUR:02d}:{config.ORDER_JOB_MINUTE:02d} ET, {label}) ==="
    )

    if not is_trading_day():
        logger.info("오늘은 NYSE 휴장일 — 주문 처리 잡 스킵")
        return

    # live-scheduler-deploy §6.2 — 주문 전 안전 게이트 (Plan SC-03/06)
    # Design Ref: §6.2 D4 — 자가점검 실패 시 주문 차단 + 알림
    _fails = runtime_guard.selfcheck()
    if _fails:
        logger.error(f"기동 자가점검 실패 — 주문 차단: {_fails}")
        notifier.notify("healthcheck", "기동 자가점검 실패 — 주문 차단", {"fails": _fails})
        return
    # Design Ref: §6.2 D3 — 키스위치(파일/env) 활성 시 주문만 스킵(스케줄러 유지)
    if runtime_guard.is_halted():
        logger.warning("TRADING_HALT 활성 — 주문 스킵(스케줄러는 유지)")
        notifier.notify("halt", "키스위치 활성 — 주문 스킵")
        return

    # community-opinion-agent-live FR-03/D2: LIVE_STRATEGY="agent"이면 에이전트 라이브 구동으로 분기.
    # "news"(기본 외)면 아래 기존 뉴스-RSI 주문 경로가 그대로 실행 (회귀 0).
    if config.LIVE_STRATEGY == "agent":
        logger.info(f"[LIVE_STRATEGY=agent] 주문 잡 → community_live.run_live ({label})")
        try:
            import community_live
            community_live.run_live(dry_run=dry_run)

            # KIS Sync (portfolio.json 캐시 갱신)
            if not dry_run:
                try:
                    broker = get_broker()
                    broker.connect()
                    portfolio = load_portfolio()
                    portfolio = sync_from_kis(portfolio, broker)
                    save_portfolio(portfolio)
                    fills = _fetch_recent_kis_fills(broker)
                    reconcile_trades_from_kis(fills)
                    logger.info("[KIS] Agent 라이브 완료 후 sync_from_kis 성공")
                except Exception as e:
                    logger.warning(f"[KIS] Agent 라이브 완료 후 sync_from_kis 실패: {e}")

            runtime_guard.write_heartbeat("order")   # 성공 시각 기록(워치독, SC-09)
        except Exception as e:
            logger.error(f"[agent] 라이브 구동 실패: {e}", exc_info=True)
            notifier.notify("error", f"agent 라이브 구동 실패: {e}")
        logger.info(f"=== 주문 처리 잡 완료 (agent, {label}) ===")
        return

    try:
        # 전날 신호 로드
        signals = load_signals()
        if not signals:
            logger.warning("신호 없음 — 주문 처리 스킵 (전날 신호 계산이 실행됐는지 확인)")
            return

        # 포트폴리오 캐시 로드 (buy_date 등 비즈니스 룰 추적용)
        portfolio = load_portfolio()

        # KIS Broker 인스턴스화 + 토큰 발급. 실패 시 잡 중단 (graceful degradation: 캐시는 그대로)
        try:
            broker = get_broker()
            broker.connect()
        except Exception as e:
            logger.error(f"[KIS] Broker 초기화 실패 — 주문 처리 중단: {e}", exc_info=True)
            return

        # KIS Adapter로 주문 위임 (Plan SC-03)
        trades = trader.process_orders(signals, portfolio, broker, dry_run=dry_run)

        # 체결된 거래 기록 (FR-19: order_no/kis_status 포함)
        for trade in trades:
            record_trade(trade)

        # Plan FR-11~13, SC-10: KIS 계좌를 Source of Truth로 portfolio.json 갱신
        # dry_run 시에는 실제 체결이 없으므로 sync도 skip (캐시 유지)
        if not dry_run:
            try:
                portfolio = sync_from_kis(portfolio, broker)
                save_portfolio(portfolio)
                fills = _fetch_recent_kis_fills(broker)
                reconcile_trades_from_kis(fills)
            except Exception as e:
                logger.warning(f"[KIS] sync_from_kis 실패 — 캐시 유지: {e}")

        # 현재가 수집 (리포트용, 실패해도 무방)
        current_prices = {}
        for symbol in portfolio.positions:
            try:
                current_prices[symbol] = broker.get_quote(symbol)
            except Exception:
                # 폴백: collector
                price = collector.get_latest_open_price(symbol)
                if price:
                    current_prices[symbol] = price

        print_portfolio_report(portfolio, current_prices)
        logger.info(f"주문 처리 완료: {len(trades)}건 체결 ({label})")
        runtime_guard.write_heartbeat("order")   # 성공 시각 기록(워치독, SC-09)

    except Exception as e:
        logger.error(f"주문 처리 잡 실패: {e}", exc_info=True)
        notifier.notify("error", f"주문 처리 잡 실패: {e}")

    logger.info(f"=== 주문 처리 잡 완료 ({label}) ===")


def start_scheduler() -> None:
    """
    APScheduler를 시작한다 (블로킹 루프).
    Ctrl+C로 중단 가능.
    """
    scheduler = BlockingScheduler(timezone=config.TIMEZONE)

    # SIGNAL_JOB (08:45 ET, timing-fix) — Reddit 수집/신호 준비
    scheduler.add_job(
        signal_calculation_job,
        CronTrigger(
            hour=config.SIGNAL_JOB_HOUR,
            minute=config.SIGNAL_JOB_MINUTE,
            timezone=config.TIMEZONE,
        ),
        id="signal_calculation",
        name="Signal Calculation Job",
        misfire_grace_time=300,  # 5분 내 실행 누락 허용
    )

    # 09:35 ET — 가상 주문 처리
    scheduler.add_job(
        order_processing_job,
        CronTrigger(
            hour=config.ORDER_JOB_HOUR,
            minute=config.ORDER_JOB_MINUTE,
            timezone=config.TIMEZONE,
        ),
        id="order_processing",
        name="Order Processing Job",
        misfire_grace_time=300,
    )

    # alive heartbeat — 워치독은 이걸로 프로세스 hang을 판단(SC-09).
    # order heartbeat는 하루 1회 갱신이라 짧은 한도로 감시하면 오탐 → 무한 재시작.
    scheduler.add_job(
        lambda: runtime_guard.write_heartbeat("alive"),
        "interval",
        minutes=config.HEARTBEAT_ALIVE_INTERVAL_MINUTES,
        id="alive_heartbeat",
        name="Alive Heartbeat Job",
    )
    runtime_guard.write_heartbeat("alive")  # 기동 직후 1회 — 초기 stale 오탐 방지

    print(f"\n스케줄러 시작 ({config.TIMEZONE})")
    print(f"  신호 계산: 매 거래일 {config.SIGNAL_JOB_HOUR:02d}:{config.SIGNAL_JOB_MINUTE:02d} ET")
    print(f"  주문 처리: 매 거래일 {config.ORDER_JOB_HOUR:02d}:{config.ORDER_JOB_MINUTE:02d} ET")
    print(f"  대상 종목: {', '.join(config.SYMBOLS)}")
    print("  종료: Ctrl+C\n")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("스케줄러 종료")

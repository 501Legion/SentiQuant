# Design Ref: §2.7 — APScheduler BlockingScheduler, ET 기준 2개 잡, NYSE 휴장일 제외
# Plan SC-01: 매 거래일 신호 자동 생성
# Plan SC-06: NYSE 휴장일에는 스케줄러 실행 안 함
import logging
from datetime import datetime

import pandas_market_calendars as mcal
import pytz
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

import config
import collector
import signals as sig_module
import trader
from portfolio import (
    load_portfolio,
    load_signals,
    print_portfolio_report,
    record_trade,
    save_portfolio,
    save_signals,
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


def signal_calculation_job() -> None:
    """
    매일 16:30 ET 실행.
    1. NYSE 휴장일 체크
    2. 모든 종목에 대해 신호 계산
    3. signals.json 저장
    """
    logger.info("=== 신호 계산 잡 시작 (16:30 ET) ===")

    if not is_trading_day():
        logger.info("오늘은 NYSE 휴장일 — 신호 계산 잡 스킵")
        return

    try:
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


def order_processing_job() -> None:
    """
    매일 09:35 ET 실행.
    1. NYSE 휴장일 체크
    2. 전날 신호 로드
    3. 오늘 시가 기반 가상 주문 처리
    4. 포트폴리오 저장 및 리포트 출력
    """
    logger.info("=== 주문 처리 잡 시작 (09:35 ET) ===")

    if not is_trading_day():
        logger.info("오늘은 NYSE 휴장일 — 주문 처리 잡 스킵")
        return

    try:
        # 전날 신호 로드
        signals = load_signals()
        if not signals:
            logger.warning("신호 없음 — 주문 처리 스킵 (전날 신호 계산이 실행됐는지 확인)")
            return

        # 포트폴리오 로드
        portfolio = load_portfolio()

        # 가상 주문 처리
        trades = trader.process_orders(signals, portfolio)

        # 체결된 거래 저장
        for trade in trades:
            record_trade(trade)

        # 포트폴리오 저장
        save_portfolio(portfolio)

        # 현재가 수집 (리포트용, 실패해도 무방)
        current_prices = {}
        for symbol in portfolio.positions:
            price = collector.get_latest_open_price(symbol)
            if price:
                current_prices[symbol] = price

        # 리포트 출력
        print_portfolio_report(portfolio, current_prices)
        logger.info(f"주문 처리 완료: {len(trades)}건 체결")

    except Exception as e:
        logger.error(f"주문 처리 잡 실패: {e}", exc_info=True)

    logger.info("=== 주문 처리 잡 완료 ===")


def start_scheduler() -> None:
    """
    APScheduler를 시작한다 (블로킹 루프).
    Ctrl+C로 중단 가능.
    """
    scheduler = BlockingScheduler(timezone=config.TIMEZONE)

    # 16:30 ET — 신호 계산
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

    print(f"\n스케줄러 시작 ({config.TIMEZONE})")
    print(f"  신호 계산: 매 거래일 {config.SIGNAL_JOB_HOUR:02d}:{config.SIGNAL_JOB_MINUTE:02d} ET")
    print(f"  주문 처리: 매 거래일 {config.ORDER_JOB_HOUR:02d}:{config.ORDER_JOB_MINUTE:02d} ET")
    print(f"  대상 종목: {', '.join(config.SYMBOLS)}")
    print("  종료: Ctrl+C\n")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("스케줄러 종료")

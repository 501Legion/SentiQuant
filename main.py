"""
News-RSI Stock Trading System
뉴스 감성 분석 + RSI 기반 미국 주식 페이퍼 트레이딩

사용법:
    python main.py                              # 스케줄러 시작 (실시간 페이퍼 트레이딩)
    python main.py --run-now                    # 즉시 신호 계산 후 종료 (API 연결 테스트)
    python main.py --report                     # 포트폴리오 현황 출력 후 종료
    python main.py --order-now                  # 즉시 주문 처리 실행 (--run-now 후 테스트용)
    python main.py --backtest                   # 백테스팅 (3개 모델 비교, combined 기본)
    python main.py --backtest --model textblob  # TextBlob 단독 백테스팅
    python main.py --backtest --model finbert   # FinBERT 단독 백테스팅
"""
import argparse
import logging
import os
import sys

# 로깅 설정 (Design Ref: §9)
os.makedirs("data", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("data/trading.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


def _check_env(require_finnhub: bool = False) -> None:
    """API 키 설정 여부를 확인한다."""
    import config
    missing = []
    if not config.POLYGON_API_KEY:
        missing.append("POLYGON_API_KEY")
    if not config.FINNHUB_API_KEY:
        missing.append("FINNHUB_API_KEY")
    if missing:
        logger.error(f".env 파일에 다음 API 키가 없습니다: {', '.join(missing)}")
        logger.error(".env.example을 복사해 .env를 만들고 API 키를 입력하세요.")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="News-RSI 페이퍼 트레이딩 시스템",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--run-now",
        action="store_true",
        help="즉시 신호 계산 후 종료 (API 연결 테스트용)",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="포트폴리오 현황 출력 후 종료",
    )
    parser.add_argument(
        "--order-now",
        action="store_true",
        help="즉시 주문 처리 실행",
    )
    parser.add_argument(
        "--backtest",
        action="store_true",
        help="백테스팅 실행 (2026-02-01 ~ 2026-04-01)",
    )
    parser.add_argument(
        "--model",
        choices=["textblob", "finbert", "combined"],
        default="combined",
        help="백테스팅 감성 모델 (기본값: combined, 3개 모델 비교 출력)",
    )
    args = parser.parse_args()

    _check_env()

    if args.backtest:
        import config
        import backtester

        if args.model == "combined":
            # 3개 모델 모두 실행 → 비교 출력
            results = backtester.run_all_models(config.SYMBOLS)
            backtester.print_comparison(results)
        else:
            # 단일 모델 실행
            engine = backtester.BacktestEngine(args.model)
            result = engine.run(config.SYMBOLS)
            backtester.print_comparison({args.model: result})

    elif args.run_now:
        import config
        import signals as sig_module
        from portfolio import save_signals

        logger.info("즉시 신호 계산 실행")
        result = sig_module.generate_signals_for_all(config.SYMBOLS)
        if result:
            save_signals(result)
            print(sig_module.format_signals_summary(result))
        else:
            print("신호 계산 결과 없음 (API 키 및 네트워크 확인)")

    elif args.report:
        import collector
        import config
        from portfolio import load_portfolio, print_portfolio_report

        portfolio = load_portfolio()
        current_prices = {}
        for symbol in portfolio.positions:
            price = collector.get_latest_open_price(symbol)
            if price:
                current_prices[symbol] = price
        print_portfolio_report(portfolio, current_prices)

    elif args.order_now:
        from scheduler import order_processing_job
        logger.info("즉시 주문 처리 실행")
        order_processing_job()

    else:
        from scheduler import start_scheduler
        start_scheduler()


if __name__ == "__main__":
    main()

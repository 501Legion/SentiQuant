"""
News-RSI Stock Trading System
뉴스 감성 분석 + RSI 기반 미국 주식 페이퍼 트레이딩

사용법:
    python main.py                              # 스케줄러 시작 (실시간 페이퍼 트레이딩)
    python main.py --run-now                    # 즉시 신호 계산 후 종료 (API 연결 테스트)
    python main.py --report                     # 포트폴리오 현황 출력 후 종료
    python main.py --order-now                  # 즉시 주문 처리 실행 (--run-now 후 테스트용)

    # 뉴스 백테스팅 (기존 + gpt5 추가; 실제 호출 모델은 config.GPT_MODEL)
    python main.py --backtest                   # combined: textblob+finbert+gpt5 3종 비교
    python main.py --backtest --model textblob  # TextBlob 단독
    python main.py --backtest --model finbert   # FinBERT 단독
    python main.py --backtest --model gpt5      # GPT-5.4 Mini 단독

    # Reddit Forward Testing
    python main.py --reddit-run-now             # 오늘 Reddit 데이터 수집 (크론탭용)
    python main.py --report-reddit              # 12전략 수익률 비교 (전체 기간)
    python main.py --report-reddit --from 2026-04-17 --to 2026-05-17

    # Reddit Replay 백테스팅 (단일 전략)
    python main.py --backtest --source reddit \\
        --model finbert --ranking mentions --sizing equal \\
        --from 2026-04-17 --to 2026-05-17
"""
import argparse
import logging
import os
import sys

import config

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


def _run_news_backtest(args) -> None:
    """뉴스 백테스팅: textblob | finbert | gpt5 | combined(3종)."""
    import backtester

    if args.model == "combined":
        # Plan SC: combined = textblob + finbert + gpt5 3종 비교
        results = backtester.run_all_models(
            config.SYMBOLS, models=("textblob", "finbert", config.GPT_MODEL_ALIAS)
        )
        backtester.print_comparison(results)
    else:
        engine = backtester.BacktestEngine(args.model)
        result = engine.run(config.SYMBOLS)
        backtester.print_comparison({args.model: result})


def _run_reddit_backtest(args) -> None:
    """Reddit replay 백테스팅 -단일 전략 지정 필수.
    # Plan SC FR-21: --source reddit --from DATE --to DATE replay 백테스팅
    """
    missing = []
    if not args.model or args.model == "combined":
        missing.append(f"--model {{finbert|finbert-wsb|{config.GPT_MODEL_ALIAS}}}")
    if not args.ranking:
        missing.append("--ranking {mentions|ratio}")
    if not args.sizing:
        missing.append("--sizing {equal|sentiment|volatility}")
    if not args.from_date:
        missing.append("--from YYYY-MM-DD")
    if not args.to_date:
        missing.append("--to YYYY-MM-DD")

    if missing:
        print(f"[오류] --source reddit 백테스팅에 필요한 옵션 누락: {', '.join(missing)}")
        print("예시: python main.py --backtest --source reddit "
              "--model finbert --ranking mentions --sizing equal "
              "--from 2026-04-17 --to 2026-05-17")
        import sys; sys.exit(1)

    from reddit_backtester import RedditReplayBacktester, print_reddit_comparison

    replayer = RedditReplayBacktester(
        model=args.model,
        ranking=args.ranking,
        sizing=args.sizing,
        from_date=args.from_date,
        to_date=args.to_date,
    )
    result = replayer.run()
    print_reddit_comparison({f"{args.model}_{args.ranking}_{args.sizing}": result})


def _run_reddit_collect() -> None:
    """Reddit 데이터 수집 즉시 실행 (크론탭 매일 16:30 ET).
    # Plan SC FR-15: 크론탭 자동 실행 → data/reddit/YYYY-MM-DD/ 저장
    """
    from datetime import date as date_cls
    from reddit_collector import RedditCollector

    today_str = date_cls.today().isoformat()
    logger.info(f"[Reddit] 수집 시작: {today_str}")

    collector = RedditCollector()
    posts_by_symbol = collector.collect(today_str)

    if posts_by_symbol:
        logger.info(
            f"[Reddit] 수집 완료: {len(posts_by_symbol)}개 종목"
            f" → data/reddit/{today_str}/wsb_posts.json 저장"
        )
        print(f"Reddit 수집 완료: {len(posts_by_symbol)}개 종목 ({today_str})")
        print(f"저장 경로: data/reddit/{today_str}/wsb_posts.json")
    else:
        logger.warning("[Reddit] 수집 결과 없음")
        print("Reddit 수집 결과 없음 -PRAW API 키 및 네트워크 확인")


def _run_report_reddit(from_date: str | None, to_date: str | None) -> None:
    """Reddit 12전략 수익률 비교 출력.
    # Plan SC FR-17: --report-reddit → 12전략 비교
    """
    import os
    import re
    import config
    from reddit_backtester import run_all_reddit_strategies, print_reddit_comparison

    # 날짜 범위 자동 탐지 (--from/--to 미지정 시 전체 기간)
    root = config.REDDIT_DATA_DIR
    if not os.path.isdir(root):
        print(f"Reddit 데이터 없음: {root}")
        print("먼저 --reddit-run-now 로 데이터를 수집하세요.")
        return

    date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    all_dates = sorted(
        e for e in os.listdir(root)
        if date_pattern.match(e)
        and os.path.isfile(os.path.join(root, e, "wsb_posts.json"))
    )

    if not all_dates:
        print("Reddit 데이터 없음 ---reddit-run-now 로 먼저 수집하세요.")
        return

    effective_from = from_date or all_dates[0]
    effective_to = to_date or all_dates[-1]

    logger.info(
        f"[Reddit Report] {effective_from} ~ {effective_to} "
        f"12전략 replay 시작..."
    )
    results = run_all_reddit_strategies(effective_from, effective_to)
    print_reddit_comparison(results)


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
    # Plan FR-20: --dry-run은 KIS place_order 직전까지만 실행, 실주문 없음 (SC-02)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="--order-now와 함께 사용 — KIS 주문 직전까지 시뮬레이션 (실주문 없음)",
    )
    parser.add_argument(
        "--backtest",
        action="store_true",
        help="백테스팅 실행",
    )
    parser.add_argument(
        "--model",
        choices=["textblob", "finbert", "finbert-wsb", config.GPT_MODEL_ALIAS, "combined"],
        default="combined",
        help="감성 모델 (combined=3종 비교, finbert-wsb=WSB 전처리 FinBERT, 기본값: combined)",
    )
    # Plan SC NFR-01: --source 미지정 시 기존 뉴스 동작 유지
    parser.add_argument(
        "--source",
        choices=["news", "reddit", "kis"],
        default="news",
        help="데이터 소스 (기본값: news). --report와 함께 'kis' 지정 시 KIS 동기화 후 출력",
    )
    parser.add_argument(
        "--ranking",
        choices=["mentions", "ratio"],
        help="Reddit Ranking 방식 (--source reddit 필수)",
    )
    parser.add_argument(
        "--sizing",
        choices=["equal", "sentiment", "volatility"],
        help="Position Sizing 방식 (--source reddit 필수)",
    )
    parser.add_argument(
        "--from",
        dest="from_date",
        metavar="YYYY-MM-DD",
        help="Reddit replay 백테스팅 시작일",
    )
    parser.add_argument(
        "--to",
        dest="to_date",
        metavar="YYYY-MM-DD",
        help="Reddit replay 백테스팅 종료일",
    )
    # Plan SC FR-15: 크론탭 매일 16:30 ET 실행
    parser.add_argument(
        "--reddit-run-now",
        action="store_true",
        help="Reddit 데이터 수집 즉시 실행 (Forward Testing 일일 수집용)",
    )
    # Plan SC FR-17: --report-reddit → 12전략 비교 출력
    parser.add_argument(
        "--report-reddit",
        action="store_true",
        help="Reddit 12전략 수익률 비교 출력",
    )
    args = parser.parse_args()

    _check_env()

    if args.reddit_run_now:
        _run_reddit_collect()

    elif args.report_reddit:
        _run_report_reddit(args.from_date, args.to_date)

    elif args.backtest:
        if args.source == "reddit":
            _run_reddit_backtest(args)
        else:
            _run_news_backtest(args)

    elif args.run_now:
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
        from portfolio import load_portfolio, print_portfolio_report, save_portfolio

        portfolio = load_portfolio()

        # Plan FR-13: --source kis 시 KIS 잔고를 Source of Truth로 동기화
        if args.source == "kis":
            from kis_broker import get_broker
            from portfolio import sync_from_kis
            try:
                broker = get_broker()
                broker.connect()
                portfolio = sync_from_kis(portfolio, broker)
                save_portfolio(portfolio)
                logger.info("[KIS] 잔고 동기화 완료 — portfolio.json 갱신")
            except Exception as e:
                logger.error(f"[KIS] 동기화 실패 — 캐시 그대로 사용: {e}")

        current_prices = {}
        for symbol in portfolio.positions:
            price = collector.get_latest_open_price(symbol)
            if price:
                current_prices[symbol] = price
        print_portfolio_report(portfolio, current_prices)

    elif args.order_now:
        from scheduler import order_processing_job
        label = "DRY-RUN" if args.dry_run else "LIVE"
        logger.info(f"즉시 주문 처리 실행 ({label})")
        order_processing_job(dry_run=args.dry_run)

    else:
        from scheduler import start_scheduler
        start_scheduler()


if __name__ == "__main__":
    main()

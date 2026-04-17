# Design Ref: §2.4 — RedditCollector (PRAW 3 subreddits + ticker extract + daily storage)
# Plan SC FR-04: PRAW 3개 서브레딧 (wsb/investing/stocks) 수집
# Plan SC FR-05: Flair 필터 (DD/Discussion/Fundamentals/Daily Discussion/Earnings)
# Plan SC FR-06: 티커 추출 + Polygon OHLCV 유효성 검사
# Plan SC FR-07: 날짜별 저장 data/reddit/YYYY-MM-DD/wsb_posts.json
# Plan SC FR-19: Market Cap/Short Interest 필터 없음 — Polygon 조회 성공 = 유효
# Plan SC FR-20: GPT-4 텍스트 최적화 (title 200자 + body 300자 + top comments 3개×100자)
import json
import logging
import os
import re
import time
from datetime import date, datetime, timezone

import config

logger = logging.getLogger(__name__)

# 알려진 비-티커 단어 (너무 흔한 대문자 약어 제외)
_COMMON_WORDS = frozenset({
    "I", "A", "AI", "IT", "GO", "BE", "DO", "DD", "SO", "US", "AM",
    "PM", "OP", "EV", "IPO", "IMO", "TBH", "YOLO", "WSB", "CEO", "CFO",
    "CTO", "SEC", "ETF", "FED", "GDP", "CPI", "ATH", "ATL", "EPS",
    "PE", "PB", "ROE", "ROI", "YTD", "QOQ", "YOY",
})

# $TICKER 패턴
_TICKER_PATTERN = re.compile(r'\$([A-Z]{1,5})\b')
# 단순 대문자 단어 패턴 (2~5자)
_WORD_TICKER_PATTERN = re.compile(r'\b([A-Z]{2,5})\b')


class RedditCollector:
    """
    PRAW 기반 3개 서브레딧 수집, 종목별 분류, 날짜별 파일 저장.
    """

    def __init__(self):
        """PRAW Reddit 인스턴스 초기화."""
        try:
            import praw
            self._reddit = praw.Reddit(
                client_id=config.REDDIT_CLIENT_ID,
                client_secret=config.REDDIT_CLIENT_SECRET,
                user_agent=config.REDDIT_USER_AGENT,
            )
            logger.info("PRAW Reddit 초기화 완료")
        except Exception as e:
            logger.error(f"PRAW 초기화 실패: {e}")
            self._reddit = None

    def collect(self, date_str: str = None) -> dict[str, list[dict]]:
        """
        3개 서브레딧에서 게시글 수집 후 종목별로 분류, 저장.

        Args:
            date_str: "YYYY-MM-DD" 저장 디렉터리명. None이면 오늘.

        Returns:
            {"NVDA": [post_dict, ...], ...}
            post_dict: {title, body_excerpt, top_comments, subreddit, created_utc, bullish}
        """
        if date_str is None:
            date_str = date.today().isoformat()

        if self._reddit is None:
            logger.error("PRAW 미초기화 — 빈 결과 반환")
            return {}

        all_posts = []
        for sub_name in config.REDDIT_SUBREDDITS:
            posts = self._fetch_subreddit(sub_name)
            all_posts.extend(posts)
            logger.info(f"r/{sub_name}: {len(posts)}개 게시글 수집")

        posts_by_symbol = self._extract_tickers(all_posts)
        if not posts_by_symbol:
            logger.warning("유효 종목 없음 — 저장 건너뜀")
            return {}

        valid_symbols = self._validate_polygon(list(posts_by_symbol.keys()))
        filtered = {s: posts_by_symbol[s] for s in valid_symbols}

        logger.info(
            f"유효 종목: {len(filtered)}/{len(posts_by_symbol)}개"
            f" — {list(filtered.keys())}"
        )
        self._save_posts(date_str, filtered)
        return filtered

    def _fetch_subreddit(self, name: str) -> list[dict]:
        """
        단일 서브레딧 최근 게시글 수집.
        Flair 필터: REDDIT_ALLOWED_FLAIRS. 제외: Gain/Loss/Meme/YOLO.
        24시간 이내 게시글만.
        """
        excluded_flairs = {"Gain", "Loss", "Meme", "YOLO", "Daily Discussion - Meme"}
        cutoff_utc = datetime.now(timezone.utc).timestamp() - (
            config.REDDIT_LOOKBACK_HOURS * 3600
        )

        posts = []
        try:
            subreddit = self._reddit.subreddit(name)
            for submission in subreddit.new(limit=200):
                # 시간 필터
                if submission.created_utc < cutoff_utc:
                    continue

                # Flair 필터
                flair = submission.link_flair_text or ""
                flair_clean = flair.strip()
                if flair_clean in excluded_flairs:
                    continue
                if not any(
                    allowed.lower() in flair_clean.lower()
                    for allowed in config.REDDIT_ALLOWED_FLAIRS
                ):
                    # flair 없는 게시글도 수집 (flair 없이 올라오는 경우 있음)
                    if flair_clean:
                        continue

                # 댓글 상위 3개 수집
                top_comments = []
                try:
                    submission.comments.replace_more(limit=0)
                    for comment in submission.comments[:config.GPT_TOP_COMMENTS]:
                        if hasattr(comment, "body"):
                            top_comments.append(
                                comment.body[:config.GPT_COMMENT_MAX]
                            )
                except Exception:
                    pass

                posts.append({
                    "title": submission.title[:config.GPT_POST_TITLE_MAX],
                    "body_excerpt": (submission.selftext or "")[:config.GPT_POST_BODY_MAX],
                    "top_comments": top_comments,
                    "subreddit": name,
                    "created_utc": int(submission.created_utc),
                    "bullish": None,  # 감성 분석 후 채워짐
                })

            # rate limit 방지
            time.sleep(1.0)

        except Exception as e:
            logger.warning(f"r/{name} 수집 실패: {e} — 빈 리스트 반환")

        return posts

    def _extract_tickers(self, posts: list[dict]) -> dict[str, list[dict]]:
        """
        게시글에서 $TICKER 패턴 + config.COMPANY_NAMES 매칭으로 종목 추출.
        결과: {"NVDA": [post1, post2, ...], ...}
        """
        ticker_posts: dict[str, list[dict]] = {}
        company_map = {
            name.upper(): symbol
            for symbol, name in config.COMPANY_NAMES.items()
            if symbol and name
        }

        for post in posts:
            text = f"{post['title']} {post['body_excerpt']}"
            found: set[str] = set()

            # $TICKER 패턴
            for match in _TICKER_PATTERN.finditer(text):
                ticker = match.group(1)
                if ticker not in _COMMON_WORDS:
                    found.add(ticker)

            # 회사명 키워드 매칭
            for name_upper, symbol in company_map.items():
                if name_upper in text.upper():
                    found.add(symbol)

            # 일반 대문자 단어 패턴 (보조)
            for match in _WORD_TICKER_PATTERN.finditer(text):
                word = match.group(1)
                if word not in _COMMON_WORDS and len(word) >= 3:
                    found.add(word)

            for ticker in found:
                ticker_posts.setdefault(ticker, []).append(post)

        return ticker_posts

    def _validate_polygon(self, symbols: list[str]) -> list[str]:
        """
        Polygon.io OHLCV 조회 성공 = 유효 종목.
        Market Cap/Short Interest 필터 없음 (FR-19).
        """
        # collector 모듈 임포트 (Polygon API 재사용)
        try:
            import collector
        except ImportError:
            logger.warning("collector 모듈 없음 — 유효성 검사 건너뜀, 전체 허용")
            return symbols

        valid = []
        for symbol in symbols:
            try:
                df = collector.get_ohlcv(symbol, lookback_days=5)
                if df is not None and not df.empty:
                    valid.append(symbol)
                else:
                    logger.debug(f"[{symbol}] Polygon OHLCV 없음 — 제외")
            except Exception as e:
                logger.debug(f"[{symbol}] Polygon 조회 실패: {e} — 제외")
            time.sleep(0.1)  # Polygon rate limit 방지

        return valid

    def _save_posts(self, date_str: str, posts_by_symbol: dict) -> None:
        """
        data/reddit/{date_str}/wsb_posts.json 저장.
        기존 파일이 있으면 덮어씀.
        """
        dir_path = os.path.join(config.REDDIT_DATA_DIR, date_str)
        os.makedirs(dir_path, exist_ok=True)
        file_path = os.path.join(dir_path, "wsb_posts.json")

        payload = {"date": date_str, **posts_by_symbol}
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        logger.info(
            f"wsb_posts.json 저장 완료: {file_path}"
            f" ({len(posts_by_symbol)}개 종목)"
        )

    @staticmethod
    def load_posts(date_str: str) -> dict[str, list[dict]]:
        """
        data/reddit/{date_str}/wsb_posts.json 로드.
        파일 없으면 빈 dict 반환.
        """
        file_path = os.path.join(config.REDDIT_DATA_DIR, date_str, "wsb_posts.json")
        if not os.path.exists(file_path):
            logger.warning(f"wsb_posts.json 없음: {file_path}")
            return {}
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # "date" 키 제거 후 반환
        return {k: v for k, v in data.items() if k != "date"}

    @staticmethod
    def discover_dates(from_date: str, to_date: str) -> list[str]:
        """
        data/reddit/ 하위 YYYY-MM-DD 폴더 중 from_date ≤ date ≤ to_date 반환.
        날짜 오름차순 정렬.
        """
        root = config.REDDIT_DATA_DIR
        if not os.path.isdir(root):
            return []

        date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        dates = []
        for entry in os.listdir(root):
            if date_pattern.match(entry) and from_date <= entry <= to_date:
                day_dir = os.path.join(root, entry)
                posts_file = os.path.join(day_dir, "wsb_posts.json")
                if os.path.isfile(posts_file):
                    dates.append(entry)

        return sorted(dates)

# Design Ref: §2.4 -RedditCollector (PRAW 3 subreddits + ticker extract + daily storage)
# Plan SC FR-04: PRAW 3개 서브레딧 (wsb/investing/stocks) 수집
# Plan SC FR-05: Flair 필터 (DD/Discussion/Fundamentals/Daily Discussion/Earnings)
# Plan SC FR-06: 티커 추출 + Polygon OHLCV 유효성 검사
# Plan SC FR-07: 날짜별 저장 data/reddit/YYYY-MM-DD/wsb_posts.json
# Plan SC FR-19: Market Cap/Short Interest 필터 없음 -Polygon 조회 성공 = 유효
# Plan SC FR-20: GPT-5.4 Mini 텍스트 최적화 (title 200자 + body 300자 + top comments 3개×100자)
import json
import logging
import os
import re
import time
from datetime import date, datetime, timezone

import config

logger = logging.getLogger(__name__)

# 비-티커 단어 (대문자이지만 종목이 아닌 것들)
_COMMON_WORDS = frozenset({
    # 기본 영어
    "I", "A", "AN", "THE", "AND", "BUT", "FOR", "OR", "NOR", "SO", "YET",
    "IF", "IN", "ON", "AT", "TO", "BY", "OF", "UP", "AS", "IS", "IT",
    "BE", "DO", "GO", "HE", "HI", "ME", "MY", "NO", "OK", "WE",
    # 일반 대문자 약어
    "AM", "PM", "US", "UK", "EU", "UN", "NY", "LA", "DC",
    # 금융/투자 일반 용어
    "AI", "EV", "OP", "DD", "PE", "PB", "PG", "VC",
    "IPO", "IMO", "TBH", "IRA", "ETF", "FED", "GDP", "CPI",
    "ATH", "ATL", "EPS", "ROE", "ROI", "YTD", "QOQ", "YOY",
    "SEC", "NYSE", "FOMC", "FDIC", "REIT", "SPAC", "MACD", "VWAP",
    "RSI", "ATR", "EMA", "SMA", "DJIA", "SPX", "SPY", "QQQ", "VIX",
    # 매매 행위
    "BUY", "SELL", "HOLD", "LONG", "SHORT", "PUTS", "CALL", "CALLS",
    "DIP", "DIPS", "BULL", "BEAR", "MOON", "PUMP", "DUMP", "HODL",
    "YOLO", "FOMO", "REKT",
    # 수식어/일반어
    "NOW", "NEW", "OLD", "TOP", "BIG", "BAD", "LOW", "HIGH",
    "BEST", "JUST", "EVEN", "ALSO", "NEXT", "LAST", "LATE",
    "MORE", "LESS", "VERY", "GOOD", "REAL", "FAKE",
    "HUGE", "DROP", "GAIN", "LOSS", "RISK", "HELP", "DEAL",
    # Reddit 용어
    "WSB", "CEO", "CFO", "CTO", "COO", "CMO",
    "EDIT", "TLDR", "INFO", "POST", "NEWS", "MEME",
    "BRRR", "HODL", "IMHO",
    # 옵션 용어
    "ATM", "ITM", "OTM", "DTE", "IV", "HV", "PNL",
    "THETA", "DELTA", "GAMMA", "VEGA", "RHO",
    # 암호화폐 (주식 아님)
    "BTC", "ETH", "SOL", "XRP", "DOGE", "SHIB",
    # 기타 일반 ETF/인덱스 (이미 SPY/QQQ 있음)
    "VTI", "VOO", "VT", "VTV", "GLD", "SLV",
    "IWM", "TLT", "HYG", "LQD", "XLF", "XLE",
    # 원자재/지표 (주식 아님)
    "WTI", "NDX", "USD", "EUR", "JPY", "DXY",
    # 옵션 전략 용어
    "PUT", "CSP", "PMC", "LEAPS", "PMCC",
    # 국가/지역 (주식 아님)
    "IRAN", "IRAQ", "CHINA", "KOREA", "INDIA",
    # 분명한 비티커
    "DCA", "LOL", "SAY", "MAY", "OPEN",
    "CASH", "SABER", "AXIOS", "ADAS",
    "CTA", "BTO", "XSP",
    # 시제/연결
    "THIS", "THAT", "THEN", "THEM", "THEY", "WHEN", "WILL",
    "BEEN", "HAVE", "FROM", "WITH", "WHAT", "WELL", "WERE",
    "INTO", "OVER", "SOME", "SUCH", "BOTH", "EACH",
})

# $TICKER 명시 패턴 ($NVDA, $AAPL 등)
_TICKER_PATTERN = re.compile(r'\$([A-Z]{1,5})\b')
# 대문자 단어 패턴 (2-post 등장 조건으로만 사용)
_WORD_TICKER_PATTERN = re.compile(r'\b([A-Z]{2,5})\b')


# ---------------------------------------------------------------------------
# community-opinion-agent §3.6 — source quality + ticker ambiguity 헬퍼
# 모두 config flag 게이팅 — OFF면 기존 동작과 동일(회귀 0). forward 수집 전용.
# ---------------------------------------------------------------------------

# flair 텍스트 → weight 상수 매핑
_FLAIR_WEIGHT_MAP_KEYS = (
    ("DD", "COMMUNITY_FLAIR_WEIGHT_DD"),
    ("Discussion", "COMMUNITY_FLAIR_WEIGHT_DISCUSSION"),
    ("Daily Discussion", "COMMUNITY_FLAIR_WEIGHT_DISCUSSION"),
    ("Stocks", "COMMUNITY_FLAIR_WEIGHT_DISCUSSION"),
    ("News", "COMMUNITY_FLAIR_WEIGHT_NEWS"),
    ("Earnings", "COMMUNITY_FLAIR_WEIGHT_NEWS"),
    ("Options", "COMMUNITY_FLAIR_WEIGHT_OPTIONS"),
    ("Technical Analysis", "COMMUNITY_FLAIR_WEIGHT_TECHNICAL"),
    ("Technicals", "COMMUNITY_FLAIR_WEIGHT_TECHNICAL"),
    ("Fundamentals", "COMMUNITY_FLAIR_WEIGHT_FUNDAMENTALS"),
)


def source_quality_weight(flair: str | None, source: str = "post") -> float:
    """flair·source로 게시글 품질 weight 계산 (Design Ref: §3.6 / Plan FR-1.1).
    필터 OFF 또는 flair 없음 → DEFAULT(1.0) fallback. low quality flair → 0.0.
    daily_thread 댓글 → DAILY_THREAD weight(0.5)."""
    if not config.COMMUNITY_ENABLE_SOURCE_QUALITY_FILTER:
        return config.COMMUNITY_FLAIR_WEIGHT_DEFAULT
    if source == "daily_thread":
        return config.COMMUNITY_FLAIR_WEIGHT_DAILY_THREAD
    f = (flair or "").strip()
    if not f:
        return config.COMMUNITY_FLAIR_WEIGHT_DEFAULT
    if f in config.COMMUNITY_LOW_QUALITY_FLAIRS:
        return config.COMMUNITY_FLAIR_WEIGHT_LOW_QUALITY   # 0.0
    for key, const_name in _FLAIR_WEIGHT_MAP_KEYS:
        if f == key:
            return getattr(config, const_name)
    return config.COMMUNITY_FLAIR_WEIGHT_DEFAULT


def is_ambiguous_ticker(ticker: str, *, has_dollar: bool) -> bool:
    """티커 오탐 위험 판정 (Design Ref: §3.6 / Plan FR-1.2).
    필터 OFF면 항상 False. ambiguity blacklist 종목·단일문자 티커는
    $ prefix가 없으면 ambiguous(=제외 대상)로 본다."""
    if not config.COMMUNITY_ENABLE_TICKER_AMBIGUITY_FILTER:
        return False
    t = (ticker or "").upper()
    if len(t) == 1 and config.COMMUNITY_SINGLE_LETTER_TICKER_REQUIRE_DOLLAR and not has_dollar:
        return True
    if t in config.COMMUNITY_TICKER_AMBIGUITY_BLACKLIST and not has_dollar:
        return True
    return False


def _is_dd_flair(flair: str | None) -> bool:
    """flair가 DD형(심층분석)인지 판별 (Design Ref: §7.1 / Plan FR-02).
    DD형이면 댓글을 COMMENT_COLLECT_DD개까지 대량 수집한다."""
    f = (flair or "").strip().lower()
    if not f:
        return False
    return f in {d.lower() for d in config.DD_FLAIRS}


def _is_quality_comment(body: str | None, author: str | None) -> bool:
    """댓글 품질 필터 (Design Ref: §7.1 / Plan FR-08).
    삭제/제거/봇/초단문 댓글 제외 → bull/bear 오카운팅·노이즈 방지."""
    b = (body or "").strip()
    if not b or b in ("[deleted]", "[removed]"):
        return False
    if len(b) < config.COMMENT_MIN_LEN:
        return False
    if author and author in config.COMMENT_BOT_AUTHORS:
        return False
    return True


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
            logger.error("PRAW 미초기화 -빈 결과 반환")
            return {}

        all_posts = []
        for sub_name in config.REDDIT_SUBREDDITS:
            posts = self._fetch_subreddit(sub_name)
            daily = self._fetch_daily_thread(sub_name)
            all_posts.extend(posts)
            all_posts.extend(daily)
            logger.info(
                f"r/{sub_name}: 게시글 {len(posts)}개 + Daily Thread {len(daily)}개"
            )

        posts_by_symbol = self._extract_tickers(all_posts)
        if not posts_by_symbol:
            logger.warning("유효 종목 없음 -저장 건너뜀")
            return {}

        valid_symbols = self._validate_polygon(list(posts_by_symbol.keys()))
        filtered = {s: posts_by_symbol[s] for s in valid_symbols}

        logger.info(
            f"유효 종목: {len(filtered)}/{len(posts_by_symbol)}개"
            f" -{list(filtered.keys())}"
        )
        self._save_posts(date_str, filtered)
        return filtered

    def _collect_comments(self, submission, is_dd: bool) -> list[str]:
        """글의 댓글을 품질 필터 적용해 상위 N개 수집 (Design Ref: §7.1 / Plan FR-01·08).
        DD형이면 COMMENT_COLLECT_DD, 아니면 COMMENT_COLLECT_NORMAL까지.
        비용 가드: replace_more 한도 + wall-clock 타임아웃."""
        limit = config.COMMENT_COLLECT_DD if is_dd else config.COMMENT_COLLECT_NORMAL
        out: list[str] = []
        start = time.time()
        try:
            submission.comments.replace_more(limit=config.COMMENT_REPLACE_MORE_LIMIT)
        except Exception as e:  # noqa: BLE001 — 일부 확장 실패해도 로드분으로 진행
            logger.debug(f"replace_more 실패: {e}")
        try:
            for comment in submission.comments.list():
                if len(out) >= limit:
                    break
                if time.time() - start > config.COMMENT_COLLECT_TIMEOUT_SEC:
                    logger.debug(
                        f"[{getattr(submission, 'id', '?')}] 댓글 수집 타임아웃 "
                        f"— {len(out)}개에서 중단"
                    )
                    break
                body = getattr(comment, "body", None)
                author = getattr(comment, "author", None)
                author_name = getattr(author, "name", None) if author else None
                if not _is_quality_comment(body, author_name):
                    continue
                out.append(body.strip()[:config.COMMENT_TEXT_MAX])
        except Exception as e:  # noqa: BLE001 — 댓글 수집 실패는 빈 리스트로 폴백
            logger.debug(f"댓글 수집 중 예외: {e}")
        return out

    def _fetch_subreddit(self, name: str) -> list[dict]:
        """
        단일 서브레딧 new + hot 피드 병행 수집 (중복 제거).
        Flair 필터: denylist 방식 - Gain/Loss/Meme/YOLO/Screenshot만 제외.
        24시간 이내 게시글만.
        """
        excluded_flairs = {
            "Gain", "Loss", "Meme", "YOLO",
            "Daily Discussion - Meme", "Screenshot",
        }
        cutoff_utc = datetime.now(timezone.utc).timestamp() - (
            config.REDDIT_LOOKBACK_HOURS * 3600
        )

        seen_ids: set[str] = set()
        posts = []
        dd_used = [0]  # 서브레딧당 DD형 대량수집 글 수 (비용 가드 상한)

        def _process_feed(feed):
            for submission in feed:
                if submission.created_utc < cutoff_utc:
                    break  # new/hot 모두 시간 역순 — 이후는 전부 오래된 것
                if submission.id in seen_ids:
                    continue
                seen_ids.add(submission.id)

                flair_clean = (submission.link_flair_text or "").strip()
                if flair_clean in excluded_flairs:
                    continue

                # comment-aware-sentiment: flair로 댓글 수집 규모 결정 (DD형 대량)
                # Design Ref: §7.1 — DD 상한 초과 시 일반 한도로 강등(비용 가드)
                is_dd = _is_dd_flair(flair_clean)
                if is_dd and dd_used[0] >= config.COMMENT_MAX_DD_POSTS_PER_SUB:
                    is_dd = False
                top_comments = self._collect_comments(submission, is_dd)
                if is_dd:
                    dd_used[0] += 1

                posts.append({
                    "title": submission.title[:config.GPT_POST_TITLE_MAX],
                    "body_excerpt": (submission.selftext or "")[:config.GPT_POST_BODY_MAX],
                    "top_comments": top_comments,
                    "subreddit": name,
                    "created_utc": int(submission.created_utc),
                    "bullish": None,
                    # community-opinion-agent §3.6 — 품질 가중 메타데이터 (additive)
                    "flair": flair_clean,
                    "source": "post",
                    "source_quality_weight": source_quality_weight(flair_clean, "post"),
                })

        try:
            subreddit = self._reddit.subreddit(name)
            _process_feed(subreddit.new(limit=1000))
            time.sleep(1.0)
            _process_feed(subreddit.hot(limit=100))  # hot은 100개면 충분
            time.sleep(1.0)
        except Exception as e:
            logger.warning(f"r/{name} 수집 실패: {e} -빈 리스트 반환")

        return posts

    def _fetch_daily_thread(self, name: str) -> list[dict]:
        """
        Daily Discussion Thread 댓글 수집.
        sticky → hot 순서로 탐색. 댓글 상위 N개(config.REDDIT_DAILY_THREAD_COMMENTS).
        댓글 1개 = post 1개로 취급하여 ticker 추출에 활용.
        """
        patterns = config.REDDIT_DAILY_PATTERNS.get(name, [])
        if not patterns:
            return []

        thread = None
        subreddit = self._reddit.subreddit(name)

        # 1. sticky 탐색
        for n in [1, 2]:
            try:
                s = subreddit.sticky(number=n)
                if any(p in s.title.lower() for p in patterns):
                    thread = s
                    break
            except Exception:
                pass

        # 2. hot fallback (sticky 없거나 패턴 미일치) — limit 50으로 확장
        if thread is None:
            try:
                for s in subreddit.hot(limit=50):
                    if any(p in s.title.lower() for p in patterns):
                        thread = s
                        break
            except Exception:
                pass

        # 3. new 피드 fallback (hot에도 없을 경우 — WSB처럼 빠른 서브레딧 대응)
        if thread is None:
            try:
                for s in subreddit.new(limit=20):
                    if any(p in s.title.lower() for p in patterns):
                        thread = s
                        break
            except Exception:
                pass

        if thread is None:
            logger.debug(f"r/{name}: Daily Discussion Thread 없음")
            return []

        logger.info(
            f"r/{name}: Daily Thread '{thread.title[:50]}' "
            f"(전체 {thread.num_comments}개 댓글) → top {config.REDDIT_DAILY_THREAD_COMMENTS}개 수집"
        )

        posts = []
        try:
            thread.comments.replace_more(limit=config.REDDIT_DAILY_THREAD_REPLACE_MORE)
            # 품질 필터(삭제/봇/초단문 FR-08) 먼저 적용 → score 상위 N개가 전부 유효 댓글이 되도록
            quality = [
                c for c in thread.comments
                if hasattr(c, "body") and _is_quality_comment(
                    getattr(c, "body", None),
                    str(c.author) if getattr(c, "author", None) else None,
                )
            ]
            top_comments = sorted(
                quality, key=lambda c: getattr(c, "score", 0), reverse=True,
            )[:config.REDDIT_DAILY_THREAD_COMMENTS]

            for comment in top_comments:
                body = comment.body.strip()
                posts.append({
                    "title": "",  # 댓글은 제목 없음
                    "body_excerpt": body[:config.GPT_POST_BODY_MAX],
                    "top_comments": [],
                    "subreddit": name,
                    "created_utc": int(comment.created_utc),
                    "bullish": None,
                    "source": "daily_thread",
                    # community-opinion-agent §3.6 — Daily Thread 댓글은 낮은 weight
                    "flair": "",
                    "source_quality_weight": source_quality_weight("", "daily_thread"),
                })
        except Exception as e:
            logger.warning(f"r/{name} Daily Thread 댓글 수집 실패: {e}")

        logger.info(f"r/{name}: Daily Thread 댓글 {len(posts)}개 추출 (source=daily_thread)")
        time.sleep(1.0)
        return posts

    def _extract_tickers(self, posts: list[dict]) -> dict[str, list[dict]]:
        """
        게시글에서 3단계 전략으로 종목 추출.
        결과: {"NVDA": [post1, post2, ...], ...}

        신호 품질 전략:
          - Stage 1: $TICKER 명시 패턴 (1건 이상)
          - Stage 2: 회사명 키워드 config.COMPANY_NAMES (1건 이상)
          - Stage 3: 대문자 단어 패턴 (2건 이상 등장 시에만 수집)
        """
        high_conf_posts: dict[str, list[dict]] = {}  # Stage 1+2
        word_posts: dict[str, list[dict]] = {}        # Stage 3 후보

        company_map = {
            name.upper(): symbol
            for symbol, name in config.COMPANY_NAMES.items()
            if symbol and name
        }

        blacklist = _COMMON_WORDS | config.REDDIT_TICKER_BLACKLIST
        # community-opinion-agent §3.6 — ambiguity 제외 통계 (forward 수집 로깅/통계용)
        self._ambiguity_skips: dict[str, int] = {}

        for post in posts:
            text = f"{post['title']} {post['body_excerpt']}"

            # Stage 1: $TICKER 명시 패턴 -가장 신뢰도 높음 (has_dollar=True → ambiguity 통과)
            for match in _TICKER_PATTERN.finditer(text):
                ticker = match.group(1)
                if ticker not in blacklist:
                    high_conf_posts.setdefault(ticker, []).append(post)

            # Stage 2: 회사명 키워드 매칭
            for name_upper, symbol in company_map.items():
                if name_upper in text.upper():
                    high_conf_posts.setdefault(symbol, []).append(post)

            # Stage 3: 대문자 단어 (3자 이상, 후보 수집) — $ 없는 bare word
            for match in _WORD_TICKER_PATTERN.finditer(text):
                word = match.group(1)
                if word in blacklist or len(word) < 3:
                    continue
                # ticker ambiguity filter (gated): blacklist 단어는 $ 없으면 제외
                if is_ambiguous_ticker(word, has_dollar=False):
                    self._ambiguity_skips[word] = self._ambiguity_skips.get(word, 0) + 1
                    continue
                word_posts.setdefault(word, []).append(post)

        # Stage 3: 2개 이상 게시글에 등장 + Stage 1/2 미수집 종목만 추가
        MIN_WORD_POSTS = 2
        for ticker, post_list in word_posts.items():
            if ticker not in high_conf_posts and len(post_list) >= MIN_WORD_POSTS:
                high_conf_posts[ticker] = post_list

        if self._ambiguity_skips:
            logger.info(
                f"[ticker ambiguity] $없는 모호어 제외: "
                f"{dict(sorted(self._ambiguity_skips.items()))}"
            )

        return high_conf_posts

    _TICKER_CACHE_FILE = os.path.join(config.REDDIT_DATA_DIR, "ticker_cache.json")
    _TICKER_CACHE_TTL_DAYS = 7  # 7일간 캐시 유지

    def _load_ticker_cache(self) -> dict[str, bool]:
        """파일에서 티커 캐시 로드. TTL 만료된 항목 제거."""
        if not os.path.exists(self._TICKER_CACHE_FILE):
            return {}
        try:
            with open(self._TICKER_CACHE_FILE, "r", encoding="utf-8") as f:
                raw: dict = json.load(f)
            cutoff = (
                datetime.now(timezone.utc)
                .replace(tzinfo=None)
                .date()
            )
            from datetime import timedelta
            min_date_str = (
                datetime.now(timezone.utc).date()
                - timedelta(days=self._TICKER_CACHE_TTL_DAYS)
            ).isoformat()
            # 형식: {"NVDA": {"valid": true, "checked": "2026-04-18"}}
            return {
                ticker: entry["valid"]
                for ticker, entry in raw.items()
                if isinstance(entry, dict) and entry.get("checked", "") >= min_date_str
            }
        except Exception:
            return {}

    def _save_ticker_cache(self, cache: dict[str, bool]) -> None:
        """티커 캐시를 파일에 저장."""
        today = date.today().isoformat()
        # 기존 파일 로드 후 병합 (기존 항목 보존)
        raw: dict = {}
        if os.path.exists(self._TICKER_CACHE_FILE):
            try:
                with open(self._TICKER_CACHE_FILE, "r", encoding="utf-8") as f:
                    raw = json.load(f)
            except Exception:
                raw = {}
        for ticker, valid in cache.items():
            raw[ticker] = {"valid": valid, "checked": today}
        os.makedirs(os.path.dirname(self._TICKER_CACHE_FILE), exist_ok=True)
        with open(self._TICKER_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)

    def _validate_polygon(self, symbols: list[str]) -> list[str]:
        """
        Polygon.io OHLCV 조회 성공 = 유효 종목.
        파일 캐시(7일 TTL) 사용 - 검증된 티커는 재호출 없이 재사용.
        Market Cap/Short Interest 필터 없음 (FR-19).
        """
        cache = self._load_ticker_cache()

        cached_valid = [s for s in symbols if cache.get(s) is True]
        cached_invalid = {s for s in symbols if cache.get(s) is False}
        unknown = [s for s in symbols if s not in cache]

        if unknown:
            try:
                import collector
            except ImportError:
                logger.warning("collector 모듈 없음 -유효성 검사 건너뜀, 전체 허용")
                return symbols

            logger.info(
                f"Polygon 검증: 신규 {len(unknown)}개 "
                f"(캐시 유효 {len(cached_valid)}개 / 캐시 제외 {len(cached_invalid)}개)"
            )
            new_results: dict[str, bool] = {}
            for symbol in unknown:
                try:
                    df = collector.get_ohlcv(symbol, days=5)
                    ok = df is not None and not df.empty
                    new_results[symbol] = ok
                    if ok:
                        cached_valid.append(symbol)
                    else:
                        logger.debug(f"[{symbol}] Polygon OHLCV 없음 -제외")
                except Exception as e:
                    new_results[symbol] = False
                    logger.debug(f"[{symbol}] Polygon 조회 실패: {e} -제외")
                time.sleep(12.0)  # Polygon 무료 플랜 5 req/min = 12초 간격
            self._save_ticker_cache(new_results)
        else:
            logger.info(
                f"Polygon 검증: 전체 캐시 히트 "
                f"(유효 {len(cached_valid)}개 / 제외 {len(cached_invalid)}개)"
            )

        return cached_valid

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

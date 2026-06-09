# Design Ref: §3.1 — WSBPreprocessor: WSB Reddit 텍스트를 FinBERT 친화적 금융 언어로 정규화
# Plan SC SC-06: wsb_preprocessor.py 파일 존재
import re


class WSBPreprocessor:
    """
    WSB Reddit 텍스트를 FinBERT 친화적 금융 언어로 정규화.

    처리 순서 (순서 중요):
    1. 이모지 → 감성 단어 (복합 이모지 먼저, 길이 내림차순)
    2. 소문자 변환
    3. 반어법 패턴 탐지 → replacement 텍스트로 교체
    4. WSB 슬랭 → 표준 금융 용어 (단어 경계 \b 매칭)
    5. 빈 공백 정리
    """

    # Design Ref: §3.1.1 — EMOJI_MAP (복합 이모지 먼저 등록)
    EMOJI_MAP: dict[str, str] = {
        # 복합 이모지 (먼저 처리)
        "🌈🐻": " bearish market outlook ",
        "💎🙌": " strong hold position ",
        # Bullish
        "🚀": " significant upward movement ",
        "💎": " strong hold position ",
        "🙌": " confident hold ",
        "📈": " upward price trend ",
        "💰": " profitable investment ",
        "🔥": " strong bullish momentum ",
        "💪": " strong conviction ",
        "🤑": " profit making ",
        "✅": " confirmed positive ",
        "🟢": " bullish signal ",
        # Bearish
        "🐻": " bearish market ",
        "📉": " downward price trend ",
        "💀": " significant loss ",
        "😭": " disappointed loss ",
        "💩": " poor investment quality ",
        "🔴": " bearish signal ",
        "❌": " negative signal ",
        "😱": " market fear ",
        # Neutral/Context
        "🤔": " uncertain about investment ",
        "👀": " watching closely ",
        "⚠️": " warning risk ",
        "🎯": " target price ",
        "📊": " market analysis ",
    }

    # Design Ref: §3.1.2 — WSB_SLANG (bullish / bearish / 제거 / 시장 / 옵션)
    WSB_SLANG: dict[str, str] = {
        # --- Bullish ---
        "to the moon": "significant upward price movement",
        "moon shot": "significant upward price movement",
        "moonshot": "significant upward price movement",
        "mooning": "experiencing sharp upward movement",
        "moon": "significant upward movement",
        "tendies": "profits",
        "tendie": "profit",
        "diamond hands": "holding long position firmly",
        "diamond hand": "holding long position",
        "apes together strong": "retail investors holding bullish",
        "yolo": "high conviction trade",
        "going to print": "will generate profits",
        "printing money": "generating significant profits",
        "calls printing": "call options generating profits",
        "short squeeze": "forced short covering upward movement",
        "gamma squeeze": "options driven upward price movement",
        "squeeze": "short squeeze upward price pressure",
        "stonks only go up": "market expected to rise",
        "stonks": "stocks performing well",
        "buy the dip": "purchasing at lower price opportunity",
        "dip buy": "buying price decline",
        "btfd": "buy the price decline",
        "let it rip": "expected sharp upward movement",
        "this is the way": "correct investment strategy",
        "we're all gonna make it": "bullish market sentiment",
        "wagmi": "bullish market sentiment",
        "ngmi": "bearish outcome expected",
        "rip": "sharp upward price movement",
        # --- Bearish ---
        "paper hands": "selling position prematurely",
        "paper hand": "selling under pressure",
        "bagholder": "investor holding significant losing position",
        "bag holder": "investor with losing position",
        "holding the bag": "stuck with losing position",
        "exit liquidity": "selling into retail buyers at peak",
        "rekt": "significant financial loss suffered",
        "get rekt": "experienced significant financial loss",
        "puts printing": "put options generating profits on decline",
        "going to zero": "expected complete loss",
        "to zero": "expected complete loss",
        "crashing": "experiencing sharp decline",
        "crash": "sharp market decline",
        "bleeding out": "continuous price decline",
        "bleeding": "sustained price decline",
        "dumping": "experiencing significant price decrease",
        "dump": "significant price decrease",
        "fud": "fear uncertainty doubt negative sentiment",
        "capitulation": "panic selling extreme fear",
        "circuit breaker": "trading halt extreme market volatility",
        "nuked": "experienced sharp sudden drop",
        "nuke": "sharp sudden price drop",
        # --- 시장 용어 슬랭 ---
        "fed pivot": "Federal Reserve policy change",
        "jpow": "Federal Reserve chairman Powell",
        "powell": "Federal Reserve chairman",
        "cpi print": "consumer price index data release",
        "fomc": "Federal Open Market Committee meeting",
        "rate hike": "interest rate increase",
        "rate cut": "interest rate decrease",
        "qe": "quantitative easing monetary stimulus",
        "qt": "quantitative tightening monetary restriction",
        "risk on": "increased risk appetite investment environment",
        "risk off": "reduced risk appetite defensive environment",
        "vix spike": "sharp increase in market volatility",
        "vix": "market volatility index",
        # --- 옵션 슬랭 ---
        "0dte": "zero days to expiration options",
        "0 dte": "zero days to expiration options",
        "yolo calls": "high risk call options position",
        "yolo puts": "high risk put options position",
        "theta gang": "options premium selling strategy",
        "wheel strategy": "options wheel income strategy",
        "iron condor": "neutral options income strategy",
        "bull spread": "bullish options spread strategy",
        "bear spread": "bearish options spread strategy",
        "leaps": "long term equity anticipation securities",
        "covered calls": "covered call options strategy",
        "cash secured puts": "cash secured put options strategy",
        # --- 제거 대상 (감성 무관 비속어/WSB 표현) ---
        "retarded": "",
        "retard": "",
        "autistic": "",
        "autist": "",
        "smooth brain": "",
        "smoothbrain": "",
        "regarded": "",
        "wrinkle brain": "",
        "apes": "bullish retail investors",
        "ape": "retail investor",
        # --- 추가 슬랭 (G2: 100개+ 목표) ---
        "to the tendies": "to profits",
        "money printer go brrr": "Federal Reserve quantitative easing",
        "brrr": "Federal Reserve money printing",
        "drill": "sharp downward price movement",
        "drilling": "experiencing sharp downward movement",
        "bananas": "extremely volatile",
        "going bananas": "experiencing extreme volatility",
        "eat the loss": "accept financial loss",
        "avg down": "averaging down position",
        "average down": "buying more at lower price",
        "limit down": "maximum daily price decline reached",
        "limit up": "maximum daily price increase reached",
        "dead cat bounce": "temporary price recovery before further decline",
        "dcb": "temporary price recovery before further decline",
        "bear trap": "false bearish signal before price increase",
        "bull trap": "false bullish signal before price decrease",
    }

    # Design Ref: §3.1.3 — SARCASM_PATTERNS (패턴, replacement)
    SARCASM_PATTERNS: list[tuple[str, str]] = [
        ("this aged well", "this investment thesis failed completely"),
        ("aged well", "did not perform as expected"),
        ("great call bro", "that was a poor investment decision"),
        ("great call", "that turned out to be a poor call"),
        ("great dd", "that research was incorrect"),
        ("totally fine", "there are significant concerns"),
        ("what could go wrong", "there is significant downside risk"),
        ("can't go tits up", "this investment has significant risk"),
        ("this is fine", "there are serious problems"),
        ("nothing to see here", "there is concerning activity"),
        ("trust me bro", "unverified speculation no fundamental basis"),
        ("trust me", "this is unverified speculation"),
        ("definitely not a pump", "this appears to be price manipulation"),
    ]

    # 이모지 매핑 순서 캐시 (복합 이모지 우선 — 길이 내림차순)
    _sorted_emojis: list[tuple[str, str]] | None = None

    @classmethod
    def _get_sorted_emojis(cls) -> list[tuple[str, str]]:
        if cls._sorted_emojis is None:
            cls._sorted_emojis = sorted(
                cls.EMOJI_MAP.items(),
                key=lambda x: len(x[0]),
                reverse=True,
            )
        return cls._sorted_emojis

    def preprocess(self, text: str) -> str:
        """
        단일 텍스트 정규화.
        Plan SC SC-03: 이모지 변환 / SC-04: 반어법 탐지
        """
        if not text:
            return text

        # 1. 이모지 → 감성 단어 (복합 이모지 우선)
        for emoji, replacement in self._get_sorted_emojis():
            text = text.replace(emoji, replacement)

        # 2. 소문자 변환
        text = text.lower()

        # 3. 반어법 패턴 탐지 → replacement
        for pattern, replacement in self.SARCASM_PATTERNS:
            if pattern in text:
                text = text.replace(pattern, replacement)

        # 4. WSB 슬랭 → 표준 금융 용어 (단어 경계 매칭, 긴 구문 먼저)
        sorted_slang = sorted(
            self.WSB_SLANG.items(),
            key=lambda x: len(x[0]),
            reverse=True,
        )
        for slang, replacement in sorted_slang:
            # 단어 경계 매칭 (\b). 이모지/특수문자 포함 구문은 직접 replace로 처리됨
            pattern = r'\b' + re.escape(slang) + r'\b'
            text = re.sub(pattern, replacement, text)

        # 5. 다중 공백 정리
        text = re.sub(r'\s+', ' ', text).strip()

        return text

    def preprocess_post(self, post: dict) -> dict:
        """
        Reddit post dict 정규화.
        title + body_excerpt 각각 처리.
        원본은 title_original, body_original 키로 보존.
        """
        return {
            **post,
            "title_original": post.get("title", ""),
            "body_original": post.get("body_excerpt", ""),
            "title": self.preprocess(post.get("title", "")),
            "body_excerpt": self.preprocess(post.get("body_excerpt", "")),
        }

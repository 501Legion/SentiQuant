# Design: WSB FinBERT 전처리기 — 슬랭/반어법/이모지 정규화

**Feature**: wsb-finbert-preprocessor
**Date**: 2026-04-18
**Status**: Design
**Architecture**: Option C — Pragmatic Balance

---

## Context Anchor

| 항목 | 내용 |
|------|------|
| **WHY** | FinBERT WSB 미스매치 → neutral 필터로 대부분 폴백 실행 중. 전처리로 빠르게 개선 |
| **WHO** | reddit backtest 실행 시 --model finbert-wsb 선택 |
| **RISK** | 슬랭 딕셔너리 과도 필터 / 신조어 미커버 / 반어법 오탐 |
| **SUCCESS** | finbert-wsb 실행 성공 + Before/After 로그 확인 + 기존 finbert 하위호환 유지 |
| **SCOPE** | 신규: wsb_preprocessor.py / 수정: sentiment_provider.py, reddit_backtester.py, main.py |

---

## 1. Overview

WSB Reddit 텍스트를 FinBERT 친화적 금융 언어로 변환하는 전처리 계층 추가.
기존 `finbert` 모델은 그대로 유지하고, `finbert-wsb`라는 새로운 모델 옵션으로 활성화.

```
Reddit Post (raw)
    ↓
WSBPreprocessor.preprocess_post()
    ├─ EMOJI_MAP: 🚀 → "significant upward movement"
    ├─ SARCASM_PATTERNS: "aged well" → "this investment thesis failed"
    └─ WSB_SLANG: "tendies" → "profits"
    ↓
Normalized Post
    ↓
FinBERT 파이프라인 (기존 동일)
    ↓
positive/negative/neutral 확률
```

---

## 2. 아키텍처 선택 이유 (Option C)

| 기준 | A: 인라인 | C: Pragmatic (선택) | B: Clean |
|------|----------|---------------------|---------|
| 파일 수 | 0개 신규 | 1개 신규 | 3개 신규 |
| 테스트 용이성 | 어려움 | 쉬움 (독립 모듈) | 쉬움 |
| 오버엔지니어링 | 없음 | 없음 | 있음 |
| FinBERT 결합도 | 높음 | 낮음 | 낮음 |
| 미래 확장 | 어려움 | 충분 | 과도 |

→ **wsb_preprocessor.py 신규 1개 파일**로 분리. ABC/Registry는 현재 불필요.

---

## 3. 모듈 설계

### 3.1 wsb_preprocessor.py (신규)

```python
# Design Ref: §3.1 — WSBPreprocessor: 독립 모듈, FinBERT 전처리 전용
class WSBPreprocessor:
    """
    WSB Reddit 텍스트를 FinBERT 친화적 금융 언어로 정규화.
    
    처리 순서 (순서 중요):
    1. 이모지 → 감성 단어 (텍스트 앞쪽 이모지 순서 보장)
    2. 소문자 변환
    3. 반어법 패턴 탐지 → bearish prefix 주입
    4. WSB 슬랭 → 표준 금융 용어 (단어 경계 매칭)
    5. 빈 공백 정리
    """
    
    EMOJI_MAP: dict[str, str] = { ... }      # §3.1.1
    WSB_SLANG: dict[str, str] = { ... }      # §3.1.2
    SARCASM_PATTERNS: list[tuple] = [ ... ]  # §3.1.3
    
    def preprocess(self, text: str) -> str:
        """단일 텍스트 정규화"""
    
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
```

#### 3.1.1 EMOJI_MAP (완전 목록)

```python
EMOJI_MAP = {
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
    "🌈🐻": " bearish market outlook ",   # 순서 중요: 복합 이모지 먼저
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
    "💬": " discussion ",
}
```

#### 3.1.2 WSB_SLANG (100개+ 목록)

**Bullish 카테고리:**
```python
{
    "to the moon": "significant upward price movement",
    "moon": "significant upward movement",
    "🚀🚀🚀": "strong bullish momentum",   # 반복 이모지
    "tendies": "profits",
    "tendie": "profit",
    "diamond hands": "holding long position firmly",
    "diamond hand": "holding long position",
    "apes together strong": "retail investors holding bullish",
    "apes": "bullish retail investors",
    "ape": "retail investor",
    "to the tendies": "to profits",
    "yolo": "high conviction trade",
    "going to print": "will generate profits",
    "printing money": "generating significant profits",
    "calls printing": "call options generating profits",
    "squeeze": "short squeeze upward price pressure",
    "short squeeze": "forced short covering upward movement",
    "gamma squeeze": "options driven upward price movement",
    "stonks only go up": "market expected to rise",
    "stonks": "stocks performing well",
    "buy the dip": "purchasing at lower price opportunity",
    "dip buy": "buying price decline",
    "btfd": "buy the price decline",
    "buy the f***ing dip": "strong buy recommendation at lower price",
    "let it rip": "expected sharp upward movement",
    "rip": "sharp upward price movement",
    "this is the way": "correct investment strategy",
    "we're all gonna make it": "bullish market sentiment",
    "wagmi": "bullish market sentiment",
    "gme": "GameStop",
    "amc": "AMC Entertainment",
    "mooning": "experiencing sharp upward movement",
}
```

**Bearish 카테고리:**
```python
{
    "paper hands": "selling position prematurely",
    "paper hand": "selling under pressure",
    "bagholder": "investor holding significant losing position",
    "bag holder": "investor with losing position",
    "holding the bag": "stuck with losing position",
    "rekt": "significant financial loss suffered",
    "get rekt": "experience significant financial loss",
    "puts printing": "put options generating profits on decline",
    "going to zero": "expected complete loss",
    "to zero": "expected complete loss",
    "crash": "sharp market decline",
    "crashing": "experiencing sharp decline",
    "bleeding": "sustained price decline",
    "bleeding out": "continuous price decline",
    "dump": "significant price decrease",
    "dumping": "experiencing significant price decrease",
    "exit liquidity": "selling into retail buyers at peak",
    "fud": "fear uncertainty doubt negative sentiment",
    "capitulation": "panic selling extreme fear",
    "circuit breaker": "trading halt extreme market volatility",
    "nuke": "sharp sudden price drop",
    "nuked": "experienced sharp sudden drop",
}
```

**중립화 (제거) 카테고리 — 감성 무관 WSB 표현:**
```python
{
    "retard": "",
    "retarded": "",
    "autist": "",
    "autistic": "",
    "smooth brain": "",
    "smoothbrain": "",
    "ape shit": "",
    "regarded": "",    # WSB의 retarded 대체어
    "wrinkle brain": "",  # 반대로 smart 의미지만 감성 무관
}
```

**시장 용어 슬랭:**
```python
{
    "fed pivot": "Federal Reserve policy change",
    "powell": "Federal Reserve chairman",
    "jpow": "Federal Reserve chairman Powell",
    "cpi print": "consumer price index data release",
    "fomc": "Federal Open Market Committee meeting",
    "rate hike": "interest rate increase",
    "rate cut": "interest rate decrease",
    "qe": "quantitative easing monetary stimulus",
    "qt": "quantitative tightening monetary restriction",
    "macro": "macroeconomic conditions",
    "risk on": "increased risk appetite investment environment",
    "risk off": "reduced risk appetite defensive environment",
    "vix": "market volatility index",
    "vix spike": "sharp increase in market volatility",
}
```

**옵션 슬랭:**
```python
{
    "0dte": "zero days to expiration options",
    "0 dte": "zero days to expiration options",
    "yolo calls": "high risk call options position",
    "yolo puts": "high risk put options position",
    "fd": "short term speculative options",
    "fds": "short term speculative options",
    "leaps": "long term equity anticipation securities",
    "covered calls": "covered call options strategy",
    "cash secured puts": "cash secured put options strategy",
    "theta gang": "options premium selling strategy",
    "wheel strategy": "options wheel income strategy",
    "iron condor": "neutral options income strategy",
    "bull spread": "bullish options spread strategy",
    "bear spread": "bearish options spread strategy",
}
```

#### 3.1.3 SARCASM_PATTERNS

```python
SARCASM_PATTERNS = [
    # (탐지 패턴, 대체 prefix)
    # 패턴은 소문자 변환 후 검사
    ("this aged well", "this investment thesis failed completely"),
    ("aged well", "did not perform as expected"),
    ("great call bro", "that was a poor investment decision"),
    ("great call", "that turned out to be a poor call"),  
    ("great dd", "that research was incorrect"),
    ("totally fine", "there are significant concerns"),
    ("what could go wrong", "there is significant downside risk"),
    ("can't go tits up", "this investment has significant risk"),   # WSB 고전
    ("this is fine", "there are serious problems"),  # 불 개 밈
    ("nothing to see here", "there is concerning activity"),
    ("trust me bro", "unverified speculation no fundamental basis"),
    ("trust me", "this is speculation"),
    ("definitely not a pump", "this appears to be price manipulation"),
]
```

**반어법 처리 방식:**
```python
def _apply_sarcasm(self, text: str) -> str:
    for pattern, replacement in self.SARCASM_PATTERNS:
        if pattern in text:
            # 해당 패턴을 replacement로 교체 (제거가 아님)
            text = text.replace(pattern, replacement)
    return text
```

---

### 3.2 sentiment_provider.py 수정

#### 3.2.1 FinBERTProvider 변경

```python
# Design Ref: §3.2 — FinBERTProvider: use_wsb_preprocessor 파라미터 추가
class FinBERTProvider(SentimentProvider):
    
    def __init__(self, use_wsb_preprocessor: bool = False):
        # Plan SC: SC-05 — 기존 finbert 동작 변경 없음 (default=False)
        self._use_wsb = use_wsb_preprocessor
        self._preprocessor = None  # lazy init
    
    @property
    def preprocessor(self):
        if self._use_wsb and self._preprocessor is None:
            from wsb_preprocessor import WSBPreprocessor
            self._preprocessor = WSBPreprocessor()
        return self._preprocessor
    
    def score(self, articles: list[dict]) -> tuple[float, list[dict]]:
        # Plan SC: SC-01 — finbert-wsb 실행 시 전처리 활성화
        if self.preprocessor:
            original_articles = articles
            articles = [self.preprocessor.preprocess_post(a) for a in articles]
            # Plan SC: SC-02 — Before/After 로깅
            self._log_preprocessing_samples(original_articles, articles)
        
        # ... 기존 FinBERT 로직 그대로 ...
    
    def _log_preprocessing_samples(
        self, 
        original: list[dict], 
        processed: list[dict],
        n: int = 3,
    ) -> None:
        """전처리 전/후 샘플 로깅 (첫 n개)."""
        for orig, proc in zip(original[:n], processed[:n]):
            orig_title = orig.get("title", "")[:60]
            proc_title = proc.get("title", "")[:60]
            if orig_title != proc_title:
                logger.debug(
                    f"[WSB-Preprocess] "
                    f"Before: '{orig_title}' "
                    f"→ After: '{proc_title}'"
                )
        
        # 집계 로깅
        changed = sum(
            1 for o, p in zip(original, processed)
            if o.get("title") != p.get("title") 
            or o.get("body_excerpt") != p.get("body_excerpt")
        )
        if changed > 0:
            logger.info(
                f"[FinBERT-WSB] 전처리 완료: "
                f"{len(original)}건 중 {changed}건 변환"
            )
```

#### 3.2.2 get_provider() 변경

```python
def get_provider(name: str) -> SentimentProvider:
    if name == "textblob":
        return TextBlobProvider()
    if name == "finbert":
        return FinBERTProvider(use_wsb_preprocessor=False)  # 기존 동작 유지
    if name == "finbert-wsb":
        # Plan SC: SC-01 — finbert-wsb 모델 옵션
        return FinBERTProvider(use_wsb_preprocessor=True)
    if name == "gpt4":
        return GPTProvider()
    raise ValueError(
        f"알 수 없는 SentimentProvider: '{name}'. "
        f"사용 가능: textblob, finbert, finbert-wsb, gpt4"
    )
```

---

### 3.3 reddit_backtester.py 수정

```python
# Plan SC: SC-01 — finbert-wsb 모델 지원
VALID_MODELS = ("finbert", "finbert-wsb", "gpt4")

def __init__(self, model: str, ...):
    if model not in VALID_MODELS:
        raise ValueError(f"Reddit 모델은 {VALID_MODELS} 지원: {model}")
    ...
```

`run_all_reddit_strategies()`는 수정하지 않음 (12전략 유지).
`finbert-wsb`는 단일 실행에서만 사용.

---

### 3.4 main.py 수정

```python
# --model 도움말 업데이트
parser.add_argument(
    "--model",
    choices=["textblob", "finbert", "finbert-wsb", "gpt4"],
    default="textblob",
    help="감성 분석 모델 (reddit backtest: finbert|finbert-wsb|gpt4)",
)
```

---

## 4. 데이터 플로우

```
[입력] Reddit Post
  title: "NVDA to the moon 🚀🚀 apes are ready"
  body_excerpt: "This aged well 😭 paper hands sold"

[WSBPreprocessor.preprocess_post()]
  Step 1 (emoji):
    "NVDA to the moon  significant upward movement   significant upward movement  apes are ready"
    " significant loss  paper hands sold"
  
  Step 2 (lowercase):
    "nvda to the moon  significant upward movement   significant upward movement  apes are ready"
    "this aged well  significant loss  paper hands sold"
  
  Step 3 (sarcasm):
    title: 변화 없음
    body: "this did not perform as expected  significant loss  paper hands sold"
  
  Step 4 (slang):
    title: "nvda  significant upward movement   significant upward movement   significant upward movement  bullish retail investors are ready"
    body: "this did not perform as expected  significant loss  selling position prematurely sold"

[출력] Normalized Post
  title: "nvda significant upward movement bullish retail investors are ready"
  body_excerpt: "this investment thesis failed significant loss selling position prematurely sold"

[FinBERT] → positive=0.82, negative=0.12, neutral=0.06 → included=True → bullish
```

---

## 5. 처리 순서 근거 (중요)

**이모지를 먼저 처리하는 이유:**
- 복합 이모지 `🌈🐻`를 단일 이모지 `🐻`보다 먼저 매칭해야 오분류 방지
- `regex.sub` 시 dict 순서 유지 필요 (Python 3.7+ dict는 삽입 순서 보장)

**소문자 변환을 이모지 후에 하는 이유:**
- 이모지는 대소문자 무관하지만, 슬랭 매칭 전에 정규화 필요

**반어법을 슬랭 전에 처리하는 이유:**
- "aged well" → "did not perform as expected" 변환 후 슬랭 dict 적용
- 반어법 내 슬랭이 있을 경우 의미 보존 가능

---

## 6. Before/After 로깅 상세

### INFO 레벨 (항상 출력):
```
[FinBERT-WSB] 전처리 완료: 104건 중 73건 변환
[FinBERT] 감성 점수: pos=48/61건 valid → score=78.69 (neutral 필터 제외: 10건)
```

### DEBUG 레벨 (`--debug` 시):
```
[WSB-Preprocess] Before: 'NVDA to the moon 🚀🚀 apes'
                → After: 'nvda significant upward movement bullish retail investors'
[WSB-Preprocess] Before: 'This aged well 😭 sold everything'
                → After: 'this investment thesis failed significant loss sold everything'
[WSB-Preprocess] Before: 'Great DD bro, not going to zero'
                → After: 'that was a poor investment decision, not going to zero'
```

---

## 7. 구현 주의사항

### 7.1 단어 경계 매칭
슬랭 치환 시 부분 단어 매칭 방지:
```python
import re

def _apply_slang(self, text: str) -> str:
    for slang, replacement in self.WSB_SLANG.items():
        # 단어 경계 \b 사용 (단, 이모지/특수문자 포함 슬랭은 직접 replace)
        pattern = r'\b' + re.escape(slang) + r'\b'
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text
```

**예외**: `"moon"` → `\bmoon\b`로 "moon"만 매칭 (moonshot, honeymoon 제외)

### 7.2 멀티 이모지 처리
```python
def _apply_emoji(self, text: str) -> str:
    # 복합 이모지 우선 처리 (길이 긴 것 먼저)
    sorted_emojis = sorted(
        self.EMOJI_MAP.items(), 
        key=lambda x: len(x[0]), 
        reverse=True
    )
    for emoji, replacement in sorted_emojis:
        text = text.replace(emoji, replacement)
    return text
```

### 7.3 빈 문자열 처리
`replacement = ""`인 슬랭(비속어 제거)의 경우 이중 공백 발생 → 마지막에 `re.sub(r'\s+', ' ', text).strip()`

### 7.4 FinBERT 512 토큰 제한
전처리 후 텍스트가 길어질 수 있음. 기존 `text[:512]` 유지 (FinBERT 파이프라인에서 `truncation=True` 처리).

---

## 8. 성공 기준 검증 방법

| SC | 검증 방법 |
|----|----------|
| SC-01 | `python main.py --backtest --source reddit --model finbert-wsb --ranking mentions --sizing equal --from DATE --to DATE` |
| SC-02 | 로그에서 `[WSB-Preprocess] Before:` → `After:` 출력 확인 |
| SC-03 | DEBUG 로그에서 🚀 → "significant upward movement" 변환 확인 |
| SC-04 | DEBUG 로그에서 "aged well" → replacement 텍스트 확인 |
| SC-05 | `--model finbert` 기존 동작 결과 변화 없음 확인 |
| SC-06 | `wsb_preprocessor.py` 파일 존재 및 `WSBPreprocessor` 클래스 확인 |

---

## 9. 파일 변경 요약

| 파일 | 유형 | 변경 내용 |
|------|------|----------|
| `wsb_preprocessor.py` | **신규** | WSBPreprocessor 클래스, EMOJI_MAP, WSB_SLANG(100개+), SARCASM_PATTERNS, preprocess()/preprocess_post() |
| `sentiment_provider.py` | 수정 | FinBERTProvider.__init__(use_wsb_preprocessor), preprocessor property (lazy init), _log_preprocessing_samples(), get_provider("finbert-wsb") |
| `reddit_backtester.py` | 수정 | VALID_MODELS에 "finbert-wsb" 추가 |
| `main.py` | 수정 | --model choices에 "finbert-wsb" 추가 |

---

## 10. 의존성

신규 패키지 없음. 기존 `re` 모듈 (stdlib) 사용.

---

## 11. Implementation Guide

### 11.1 구현 순서

```
1. wsb_preprocessor.py 생성
   ├─ EMOJI_MAP dict 정의 (복합 이모지 먼저)
   ├─ WSB_SLANG dict 정의 (bullish/bearish/제거/시장/옵션 카테고리)
   ├─ SARCASM_PATTERNS list 정의
   ├─ preprocess(text: str) → str 구현
   │   ├─ _apply_emoji()
   │   ├─ lowercase
   │   ├─ _apply_sarcasm()
   │   ├─ _apply_slang()
   │   └─ whitespace cleanup
   └─ preprocess_post(post: dict) → dict 구현

2. sentiment_provider.py 수정
   ├─ FinBERTProvider.__init__(use_wsb_preprocessor=False) 추가
   ├─ preprocessor property (lazy init) 추가
   ├─ score() 내 전처리 호출 + 로깅 추가
   └─ get_provider("finbert-wsb") 분기 추가

3. reddit_backtester.py 수정
   └─ VALID_MODELS 튜플에 "finbert-wsb" 추가

4. main.py 수정
   └─ --model choices에 "finbert-wsb" 추가
```

### 11.2 구현 팁

- `WSBPreprocessor`는 stateless → `FinBERTProvider`에서 인스턴스 1번만 생성 (lazy init)
- `preprocess_post()`는 원본 보존 (`title_original` 키) → 로깅/디버깅 용이
- 슬랭 dict는 길이 내림차순 정렬 불필요 (단어 경계 `\b` 매칭으로 해결)
- 이모지 매핑은 길이 내림차순 정렬 필요 (복합 이모지 우선)

### 11.3 Session Guide

**Module Map:**

| 모듈 | 파일 | 예상 라인 | 범위 키 |
|------|------|----------|---------|
| M1: WSBPreprocessor | wsb_preprocessor.py (신규) | ~150줄 | module-1 |
| M2: FinBERTProvider 수정 | sentiment_provider.py | ~30줄 추가 | module-2 |
| M3: CLI 옵션 추가 | reddit_backtester.py + main.py | ~10줄 | module-3 |

**권장 세션 계획:**
- 단일 세션 가능 (전체 변경량 ~190줄)
- `/pdca do wsb-finbert-preprocessor` → 전체 구현

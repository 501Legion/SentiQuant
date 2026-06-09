# Plan: WSB FinBERT 전처리기 — 슬랭/반어법/이모지 정규화

**Feature**: wsb-finbert-preprocessor
**Date**: 2026-04-17
**Status**: Plan

---

## Executive Summary

| 관점 | 내용 |
|------|------|
| **Problem** | FinBERT는 Bloomberg/Reuters 형식적 금융 뉴스로 학습됨. WSB 슬랭("tendies", "apes", "moon"), 이모지(🚀🌈🐻), 반어법("aged well"), 비속어("retard" = bullish 용법)를 오분류하여 신호 품질 저하 |
| **Solution** | `wsb_preprocessor.py` 신규 생성 — 슬랭 딕셔너리(100개+) + 이모지 매핑 + 반어법 패턴 탐지로 WSB 텍스트를 FinBERT 친화적 금융 언어로 변환. `finbert-wsb` 모델 옵션으로 기존 `finbert`와 독립 비교 가능 |
| **Function UX Effect** | `--model finbert-wsb` 옵션 추가. 전처리 전/후 FinBERT 점수 변화 로그 출력. 기존 12전략 백워드 호환 유지 |
| **Core Value** | WSB 특화 감성 분석 정확도 향상 → Reddit Forward Testing 신호 신뢰도 개선. FinBERT vs FinBERT-WSB vs GPT-4 실증 비교 가능 |

---

## Context Anchor

| 항목 | 내용 |
|------|------|
| **WHY** | FinBERT의 WSB 텍스트 오분류가 reddit-gpt4-quant 12전략 신호 품질의 근본 약점. 전처리로 빠르게 개선 가능 |
| **WHO** | news-rsi-trading 운영자 (reddit backtest 실행 시 --model finbert-wsb 선택) |
| **RISK** | 슬랭 딕셔너리 과도 필터 시 실제 감성 신호 손실 가능 / 신조어 미커버 |
| **SUCCESS** | finbert-wsb 실행 성공 / 전처리 전/후 점수 변화 로그 확인 / 🚀→"bullish" 변환 확인 |
| **SCOPE** | 신규: wsb_preprocessor.py / 수정: sentiment_provider.py, reddit_backtester.py, main.py |

---

## 1. 문제 분석

### 1.1 FinBERT WSB 미스매치 사례

| WSB 텍스트 | FinBERT 예상 분류 | 실제 의미 |
|-----------|----------------|----------|
| "NVDA to the moon 🚀🚀" | neutral/negative | 강한 bullish |
| "Apes together strong 💎🙌" | neutral | bullish hold |
| "This aged well 💀" | positive (sarcasm 오해) | bearish/loss |
| "Great DD bro" (반어) | positive | 실제 부정적 |
| "I'm retarded and going YOLO" | negative | bullish (WSB 문화) |
| "Paper hands 😭" | neutral | selling/bearish |
| "🌈🐻 confirmed" | neutral | 강한 bearish |

### 1.2 FinBERT neutral 필터 문제

현재 `NEUTRAL_FILTER_THRESHOLD = 0.80` 설정에서 WSB 슬랭 텍스트는 FinBERT가 의미를 파악 못해 neutral 확률이 높아짐 → 대부분 필터링 → `NEUTRAL_FILTER_MIN_ARTICLES` 미달 → 폴백 방식으로 자동 전환.

즉, **WSB 데이터는 현재 거의 FinBERT 감성 분석이 작동하지 않는 상태.**

---

## 2. 구현 상세

### 2.1 wsb_preprocessor.py — WSBPreprocessor 클래스 (신규)

```python
class WSBPreprocessor:
    """
    WSB Reddit 텍스트를 FinBERT 친화적 금융 언어로 정규화.
    
    처리 순서:
    1. 이모지 → 감성 단어 매핑
    2. 반어법 패턴 탐지 → bearish prefix 주입
    3. WSB 슬랭 → 표준 금융 용어 치환
    4. 비속어/노이즈 제거 (감성 신호 없는 단어)
    """
```

#### 2.1.1 이모지 매핑 (EMOJI_MAP)

| 이모지 | 변환 | 감성 |
|--------|------|------|
| 🚀 | "significant upward movement" | bullish |
| 💎🙌 | "strong hold position" | bullish |
| 📈 | "upward price trend" | bullish |
| 💰 | "profitable" | bullish |
| 🔥 | "strong momentum" | bullish |
| 🌈🐻 | "bearish market" | bearish |
| 📉 | "downward price trend" | bearish |
| 💀 | "significant loss" | bearish |
| 💩 | "poor investment" | bearish |
| 😭 | "disappointed loss" | bearish |

#### 2.1.2 WSB 슬랭 딕셔너리 (WSB_SLANG, 100개+)

**Bullish 슬랭**:
| 슬랭 | 변환 |
|------|------|
| moon / to the moon | significant upward movement |
| tendies | profits |
| diamond hands | holding long position |
| apes / ape | bullish retail investors |
| yolo | high conviction trade |
| stonks | stocks performing well |
| squeeze | short squeeze upward |
| rip | sharp upward movement |
| dip buy / buy the dip | purchasing at lower price |
| calls / call options | bullish options position |
| printing | generating profits |
| gang / squad | holding together |

**Bearish 슬랭**:
| 슬랭 | 변환 |
|------|------|
| paper hands | selling position |
| bagholder | investor holding losing position |
| puts / put options | bearish options position |
| dump / dumping | significant price decrease |
| crash | sharp market decline |
| rekt | significant financial loss |
| bleeding | sustained price decline |
| FUD | fear uncertainty doubt bearish sentiment |
| capitulation | panic selling |

**중립/제거 대상** (감성 신호 없는 WSB 비속어):
| 제거 | 이유 |
|------|------|
| retard / retarded | WSB 친밀 표현, 감성 무관 |
| autist / autistic | WSB 문화 표현, 감성 무관 |
| smooth brain | 자조적 표현, 감성 무관 |
| ape (standalone) | 문맥에 따라 제거 또는 bullish |
| YOLO (standalone) | 이미 위에서 처리 |

**시장 상황 슬랭**:
| 슬랭 | 변환 |
|------|------|
| circuit breaker | trading halt market volatility |
| fed / powell | Federal Reserve monetary policy |
| CPI | consumer price index inflation data |
| fomc | Federal Open Market Committee meeting |
| tariff | import tax trade policy |

#### 2.1.3 반어법 패턴 (SARCASM_PATTERNS)

```python
SARCASM_PATTERNS = [
    # (패턴, bearish prefix)
    ("aged well", "this investment thesis failed"),
    ("great call bro", "poor investment decision"),
    ("great dd", "poor research"),
    ("definitely not", ""),  # 제거
    ("totally fine", "not performing well"),
    ("trust me bro", "unverified speculation"),
    ("can't go tits up", "significant downside risk"),  # WSB 고전
    ("what could go wrong", "high risk investment"),
]
```

### 2.2 sentiment_provider.py — FinBERTProvider 수정

```python
class FinBERTProvider(SentimentProvider):
    def __init__(self, use_wsb_preprocessor: bool = False):
        self.preprocessor = WSBPreprocessor() if use_wsb_preprocessor else None
    
    def score(self, articles: list[dict]) -> tuple[float, list[dict]]:
        if self.preprocessor:
            articles_processed = [
                self.preprocessor.preprocess_post(a) for a in articles
            ]
            # Before/After 로깅 (첫 3개만)
            for orig, proc in zip(articles[:3], articles_processed[:3]):
                logger.debug(
                    f"[WSB-Preprocess] Before: {orig.get('title','')[:60]}"
                    f" → After: {proc.get('title','')[:60]}"
                )
        else:
            articles_processed = articles
        
        # 기존 FinBERT 로직 (articles_processed 사용)
        ...
```

### 2.3 get_provider() 수정

```python
def get_provider(name: str) -> SentimentProvider:
    if name == "finbert":
        return FinBERTProvider(use_wsb_preprocessor=False)
    if name == "finbert-wsb":
        return FinBERTProvider(use_wsb_preprocessor=True)
    if name == "gpt4":
        return GPTProvider()
    ...
```

### 2.4 reddit_backtester.py 수정

```python
if model not in ("finbert", "finbert-wsb", "gpt4"):
    raise ValueError(...)
```

### 2.5 main.py 수정

```
--model 옵션: finbert | finbert-wsb | gpt4
```

---

## 3. Before/After 점수 로깅 설계

실행 시 DEBUG 로그:
```
[WSB-Preprocess] Before: "NVDA to the moon 🚀🚀 apes"
                → After: "NVDA significant upward movement bullish bullish bullish retail investors"
[WSB-Preprocess] FinBERT before_score=N/A → post_score=XX.X
```

INFO 로그 (집계):
```
[FinBERT-WSB] 전처리 완료: 총 104건, 슬랭 변환 37건, 이모지 변환 52건, 반어법 탐지 3건
[FinBERT-WSB] neutral 필터 통과: 61/104건 (기존 finbert 대비 +XX건 예상)
```

---

## 4. 리스크 및 대응

| 리스크 | 대응 |
|--------|------|
| 슬랭 딕셔너리 과도 필터 | 제거 대상은 감성 무관 단어만. 의심스러운 것은 변환 안 함 |
| 신조어 미커버 | `WSB_SLANG` dict은 확장 가능 구조. Stage 1로 $TICKER 명시 게시글 우선 |
| 반어법 오탐 | 패턴은 보수적으로 (고전적 WSB 표현만). 로그로 검증 가능 |
| 기존 finbert 동작 변경 | `use_wsb_preprocessor=False` 기본값 → 하위호환 완전 유지 |

---

## 5. 성공 기준 (SC)

| # | 기준 | 검증 방법 |
|---|------|----------|
| SC-01 | `--model finbert-wsb` 실행 성공 | `python main.py --backtest --source reddit --model finbert-wsb ...` |
| SC-02 | 전처리 전/후 변환 로그 확인 | DEBUG 로그에서 Before/After 텍스트 변화 확인 |
| SC-03 | 🚀 emoji → "significant upward movement" 변환 | 로그 확인 |
| SC-04 | "aged well" → bearish prefix 주입 확인 | 로그 확인 |
| SC-05 | 기존 `--model finbert` 동작 유지 | 기존 실행 결과 동일 |
| SC-06 | WSBPreprocessor 단위 구조 확인 | wsb_preprocessor.py 파일 존재 및 클래스 구조 |

---

## 6. 파일 변경 목록

| 파일 | 변경 유형 | 내용 |
|------|----------|------|
| `wsb_preprocessor.py` | **신규** | WSBPreprocessor 클래스, EMOJI_MAP, WSB_SLANG, SARCASM_PATTERNS |
| `sentiment_provider.py` | 수정 | FinBERTProvider에 `use_wsb_preprocessor` 파라미터 추가, before/after 로깅 |
| `reddit_backtester.py` | 수정 | `finbert-wsb` 모델 옵션 추가 |
| `main.py` | 수정 | `--model` 옵션에 `finbert-wsb` 추가 |

---

## 7. User Intent Discovery (Plan Plus)

| 항목 | 내용 |
|------|------|
| **핵심 문제** | FinBERT WSB 정확도 — 슬랭/반어법/이모지 오분류 |
| **목표 사용자** | news-rsi-trading 운영자 |
| **성공 기준** | finbert-wsb 실행 + 로그로 개선 확인 |
| **제외 항목** | WSB 파인튜닝(C안), GPT-4 라우팅(B안) — 향후 검토 |

---

## 8. Alternatives Explored

| 접근법 | 설명 | 결정 |
|--------|------|------|
| A: 전처리 딕셔너리 | WSB 슬랭 → 표준 금융 용어 변환 | **선택** |
| B: GPT-4 라우팅 | Daily Thread → GPT-4, 일반 → FinBERT | 나중에 검토 |
| C: FinBERT 파인튜닝 | WSB labeled dataset으로 fine-tune | 장기 과제 |

---

## 9. YAGNI Review

| 항목 | 포함 여부 | 이유 |
|------|----------|------|
| WSB 슬랭 사전 100개+ | ✅ | 핵심 기능 |
| 반어법 패턴 탐지 | ✅ | 고전적 WSB 표현 오분류 방지 |
| 이모지 감성 매핑 | ✅ | WSB 이모지 = 핵심 감성 신호 |
| Before/After 점수 로깅 | ✅ | 개선 효과 측정 필수 |
| 단위 테스트 파일 | ❌ | 첫 버전 제외, 로그로 검증 |
| finbert-wsb 18전략 report | ❌ | 기존 12전략 유지, 단일 실행만 추가 |

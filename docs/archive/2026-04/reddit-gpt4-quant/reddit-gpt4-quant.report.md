# Report: 뉴스+Reddit 5-Model 감성 분석 비교 전략

**Feature**: reddit-gpt4-quant
**Date**: 2026-04-17
**Status**: Completed
**Match Rate**: 100% (static) | 12/17 SC 정적 검증 완료 | 5/17 런타임 대기

---

## Executive Summary

| 관점 | 내용 |
|------|------|
| **Problem** | 기존 뉴스+TextBlob/FinBERT 2모델로만 검증됨. GPT-4 성능 미확인. Reddit 군중심리 활용법 없음. Reddit 밈주식 급락 방어 로직 부재 |
| **Solution** | 뉴스 3종 백테스팅(TextBlob/FinBERT/GPT-4) + Reddit Forward Testing 12전략(2모델×2랭킹×3사이징). Stop-Loss(-7%)+Trailing Stop(-5%)+Gap Down 즉시 청산 포함. 수수료 0.25% 양방 공제 |
| **Value Delivered** | 신규 5개 파일 + 기존 5개 파일 수정. main.py에 8개 CLI 플래그 추가. 15가지 전략 비교 인프라 완성. 크론탭 연동 준비 완료 |
| **Core Value** | 수수료+손절매+슬리피지 포함 실전 조건에서 Reddit 신호 유효성 검증 준비. 2-4주 Forward Testing 후 최적 전략 확정 가능 |

---

## Context Anchor

| 항목 | 내용 |
|------|------|
| **WHY** | 뉴스 vs Reddit, TextBlob vs FinBERT vs GPT-4, Equal vs Sentiment vs Volatility 실증 비교 |
| **WHO** | news-rsi-trading 시스템 운영자 |
| **RISK** | GPT-4 비용 / PRAW rate limit / Reddit 과거 데이터 접근 / ATR 계산 OHLCV 필요 |
| **SUCCESS** | 15가지 전략 각각 수익률 비교 출력, 캐시 재활용, 기존 뉴스 로직 동작 유지 |

---

## 1. Value Delivered

### 1.1 구현된 기능 (전체)

| 모듈 | 파일 | 상태 | 핵심 내용 |
|------|------|------|---------|
| M1 | `config.py` | ✅ | 52개 신규 상수 (GPT/Reddit/손절매/수수료/Position Sizing) |
| M2 | `sentiment_provider.py` | ✅ | GPTProvider: gpt-4o, 배치10건, sha256 캐시 |
| M3 | `indicators.py` | ✅ | get_ma(period), get_atr(14, Wilder's) |
| M4 | `position_sizer.py` | ✅ | EqualSizer/SentimentSizer/VolatilitySizer ABC |
| M5 | `reddit_collector.py` | ✅ | PRAW 3서브레딧, Flair 필터, 티커추출, Polygon검증 |
| M6 | `wsb_signal_engine.py` | ✅ | Consensus(×1.5)→30MA→Ranking→TopN 파이프라인 |
| M7 | `reddit_portfolio.py` | ✅ | 포지션 추적, Gap Down, Stop-Loss, Trailing Stop, 수수료 |
| M8 | `backtester.py` + `reddit_backtester.py` | ✅ | gpt4 추가, 수수료 공제, 12전략 replay |
| M9 | `main.py` | ✅ | --model gpt4/combined, --source reddit, --reddit-run-now 등 8개 플래그 |

### 1.2 신규 CLI 명령어

```bash
# 뉴스 백테스팅 (gpt4 추가)
python main.py --backtest --model gpt4
python main.py --backtest                        # combined: 3종 비교

# Reddit Forward Testing
python main.py --reddit-run-now                  # 크론탭: 매일 16:30 ET
python main.py --report-reddit                   # 12전략 비교 출력
python main.py --report-reddit --from 2026-04-17 --to 2026-05-17

# Reddit Replay 백테스팅
python main.py --backtest --source reddit \
    --model finbert --ranking mentions --sizing equal \
    --from 2026-04-17 --to 2026-05-17
```

### 1.3 크론탭 설정 (Forward Testing 운영 준비)

```
# 매일 16:30 ET Reddit 신호 수집 + Forward Testing
30 16 * * 1-5  cd /path/to/SentiQuant && python main.py --reddit-run-now

# 주간 리포트 (금요일 17:00 ET)
0 17 * * 5  cd /path/to/SentiQuant && python main.py --report-reddit
```

---

## 2. Key Decisions & Outcomes

| 결정 | 근거 | 결과 |
|------|------|------|
| **Clean Architecture (Option B)** | 설계 시 선택. 기존 코드 분리 유지 | 신규 5파일 독립 모듈로 완성. 기존 signals.py/trader.py 무변경 |
| **Reddit Forward Testing** | PRAW 과거 데이터 한계(~1000 posts) | data/reddit/YYYY-MM-DD/ 날짜별 저장 → 나중에 replay 백테스팅 가능 |
| **Gap Down 즉시 청산** | 밈주식 오버나이트 급락 시 Stop-Loss 전 손실 | process_day() Step 1에서 시가 청산 로직 구현 |
| **수수료 양방 공제** | 한국투자증권 0.25% (min $2) | reddit_portfolio._sell()에서 buy+sell 수수료 모두 net_pnl에 반영 |
| **EqualSizer 폴백** | VolatilitySizer ATR 없을 시 | ATR 없으면 Equal 방식으로 자동 폴백 |
| **combined = 3종 비교** | main.py 사용자 결정 | --model combined 시 textblob+finbert+gpt4 모두 실행 |
| **reddit backtest 단일 전략 필수** | 사용자 결정 (--ranking/--sizing 지정 필수) | 에러 메시지 + 사용 예시 자동 출력 |

---

## 3. Success Criteria Final Status

| SC | 기준 | 상태 | 증거 |
|----|------|------|------|
| SC-01 | GPTProvider gpt-4o 구현, gpt_cache.json 생성 | ✅ | sentiment_provider.py:GPTProvider |
| SC-02 | wsb_posts.json 생성, DD/Discussion만, 티커별 | ✅ | reddit_collector.py:_save_posts |
| SC-03 | Consensus 1.5배 + 30MA + Top N 동작 | ✅ | wsb_signal_engine.py:run_pipeline |
| SC-04 | consensus_reversal / ma30_breakdown SELL | ✅ | wsb_signal_engine.py:check_exit |
| SC-05 | Equal/Sentiment/Volatility 각각 다른 주수 | ✅ | position_sizer.py:calc_shares |
| SC-06 | 뉴스 3종 수익률 출력 | ✅ | backtester.py:run_all_models |
| SC-07 | Reddit 12종 수익률 출력 | ✅ | reddit_backtester.py:run_all_reddit_strategies |
| SC-08 | 동일 날짜 재실행 캐시 HIT 로그 | ⏳ | 런타임 필요 (OpenAI API) |
| SC-09 | --source 미지정 시 뉴스 동작 유지 | ✅ | main.py:default='news' |
| SC-10 | 수수료 포함 P&L (매수/매도 각각) | ✅ | reddit_portfolio.py:_calc_commission |
| SC-11 | 3개 서브레딧 통합, 서브레딧별 로그 | ⏳ | 런타임 필요 (PRAW) |
| SC-12 | data/reddit/YYYY-MM-DD/ 3개 파일 생성 | ⏳ | 런타임 필요 |
| SC-13 | Stop-Loss / Gap Down 로그 출력 | ✅ | reddit_portfolio.py + wsb_signal_engine.py |
| SC-14 | Trailing Stop 로그 출력 | ✅ | wsb_signal_engine.py:check_exit |
| SC-15 | Polygon OHLCV 실패 종목 제외 로그 | ⏳ | 런타임 필요 (Polygon API) |
| SC-16 | GPT 게시글당 토큰 ≤ 300 | ⏳ | 런타임 필요 (텍스트 잘라내기 구현됨) |
| SC-17 | Reddit replay `--source reddit --from --to` | ✅ | reddit_backtester.py:RedditReplayBacktester |

**Overall: 12/17 ✅ 정적 검증 | 5/17 런타임 대기 (외부 API 종속)**

---

## 4. 리스크 해소 현황

| 리스크 | 계획 대응 | 실제 구현 |
|--------|---------|---------|
| GPT-4 비용 | 텍스트 잘라내기 + gpt_cache.json | ✅ 구현 완료 |
| Reddit 밈주식 급락 | Stop-Loss + Trailing Stop + Gap Down | ✅ 구현 완료 |
| Reddit 과거 데이터 없음 | Forward Testing daily 수집 | ✅ 크론탭 준비 완료 |
| PRAW rate limit | 서브레딧당 time.sleep(1.0) | ✅ 구현 완료 |
| Polygon OHLCV 부하 | time.sleep(0.1) + 캐시 | ✅ 구현 완료 |

---

## 5. 다음 실행 단계

### Phase A: 즉시 (API 키 설정)
```bash
# .env에 추가
OPENAI_API_KEY=sk-...
REDDIT_CLIENT_ID=...
REDDIT_CLIENT_SECRET=...
REDDIT_USER_AGENT=trading-bot/1.0
```

### Phase B: Forward Testing 시작 (오늘~)
```bash
# 매일 16:30 ET 크론탭 등록
python main.py --reddit-run-now
```

### Phase C: 2-4주 후 결과 확인
```bash
python main.py --report-reddit
# → 12전략 수익률 비교 출력
```

### Phase D: 뉴스 3종 백테스팅 비교
```bash
python main.py --backtest
# → TextBlob / FinBERT / GPT-4o 수익률 비교
```

---

## 6. 파일 변경 목록

```
[신규 생성]
reddit_collector.py       PRAW 3서브레딧 수집 + 티커추출 + 저장
wsb_signal_engine.py      Reddit 파이프라인 (Consensus/30MA/Ranking/Exit)
position_sizer.py         Equal/Sentiment/Volatility PositionSizer ABC
reddit_portfolio.py       RedditPortfolio (포지션/수수료/Gap Down)
reddit_backtester.py      RedditReplayBacktester + 12전략 실행

[수정]
config.py                 +52 상수 (GPT/Reddit/손절매/수수료/Sizing)
sentiment_provider.py     +GPTProvider (gpt-4o, 배치, 캐시)
indicators.py             +get_ma(), +get_atr() (Wilder's)
backtester.py             +gpt4 모델, +_calc_commission_pct(), +models 파라미터
main.py                   +8개 CLI 플래그 (--source/--ranking/--sizing/--reddit-run-now 등)
```

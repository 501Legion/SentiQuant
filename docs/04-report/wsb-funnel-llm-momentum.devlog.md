# 개발 저널 — WSB 깔때기 정상화 → LLM 라우터 → 모멘텀 모순

**대상 라인**: community-opinion-agent-live (라이브 매수 0건 정상화 → LLM 라우터 실효화 → 전략 모순 발견)
**기간**: 2026-06-05 ~ 2026-06-13
**상태**: 진행 중 — 멀티데이 손익 백테스트로 핵심 가설 검증 단계
**관련 문서**: [no-trade 진단](community-opinion-agent-live.no-trade-diagnosis.md) · [라이브 설계](../02-design/features/community-opinion-agent-live.design.md) · [운영](../ops/live-scheduler.md)

> 이 문서는 "제작 과정에서 어떤 트러블이 있었고, 어떻게 해결했고, 어디로 가는지"를 시간순으로 남긴 개발 저널이다. 각 항목은 **증상 → 원인 → 해결 → 교훈** 구조.

---

## 0. 한 줄 요약

라이브 에이전트가 **매수 0건**으로 시작 → 깔때기가 과보수임을 진단(funnel-fix) → 주문 지연 17h 해소(timing-fix) → LLM 라우터가 "의사판단만 하고 실효 0"인 이유 규명(브레이크 전용 + BUY 후보 부재) → 깔때기를 풀자 LLM이 즉시 brake 작동 → 그 과정에서 **전략의 구조적 모순**(WSB 모멘텀 전략인데 필터가 모멘텀 승자 지문을 역차별) 발견 → 펌프위험을 *진입 veto*가 아니라 *사이징+타이트 손절*로 다루자는 가설 도출 → **멀티데이 손익 백테스트로 검증 중**(OHLCV throttle 병목을 사전캐싱으로 우회).

---

## 1. 트러블 #1 — 라이브 매수 0건 (과보수 깔때기)

- **증상**: `community_live.run_live` 가동 후 매수 0건. 4회 연속 주문 0건(06-04~06-05). 19~58종목 스코어링 → 전원 탈락.
- **원인**: 보수 필터 4겹이 직렬로 작동 — ① FinBERT 중립 분류 과다(WSB 글이 잡담·질문 위주), ② 중립비율 ≤70% 킬스위치, ③ 합의비율 상승≥하락×1.5, ④ 종목당 글 수 부족(1~7건)으로 폴백 경로. 자세한 종목별 실측은 [no-trade 진단 리포트](community-opinion-agent-live.no-trade-diagnosis.md) §3.
- **해결 (funnel-fix, 2026-06-13)** — 진단 리포트의 "옵션 B(임계값 완화, 백테스트 재검증 후)"를 실행:
  1. score 표본 수축 `score* = 50+(raw-50)·n/(n+K)`, `WSB_SCORE_SHRINKAGE_K=8` (`wsb_signal_engine._score_posts`).
  2. 중립 킬스위치(neutral/total>0.75) 폐지 → 방향성 멘션 최소치 `WSB_MIN_DIRECTIONAL_MENTIONS=3` + 극단 컷 `WSB_NEUTRAL_RATIO_MAX=0.95`.
  3. RSI 30~50 역추세 매수창 폐지 → `WSB_RSI_BUY_MAX=70` 과매수만 회피 (`_determine_signal_v3`).
  4. 중립 게이트 0.70~0.75 → 0.90, sizer `_neutral_factor`를 연속 damper(0.6/0.8/0.9)로 전환.
  5. `COMMUNITY_MIN_EDGE_TO_COST_MULTIPLIER` 2.0 → 1.5.
- **교훈**: FinBERT는 소셜 텍스트에서 **중립 편향** — 토론 많은 종목일수록 neutral_ratio가 구조적으로 높다. 중립비율로 차단하면 *정보 많은 종목을 역차별*한다. (이 통찰이 뒤의 모멘텀 모순으로 이어진다.)

---

## 2. 트러블 #2 — 주문 17시간 지연

- **증상**: 신호 계산 16:30 ET → 주문은 익일 09:35 ET. 신호와 체결 사이 17시간 지연 → 모멘텀 전략에 치명적.
- **원인**: 수집/신호 잡 시각이 장 마감 후(16:30 ET)로 설정.
- **해결 (timing-fix, 2026-06-13)**: 수집/신호 잡 16:30 ET → **08:45 ET**(`config.SIGNAL_JOB_HOUR/MINUTE`). 당일 08:45 수집 → 09:35 주문, 지연 17h → 50분. 수집 잡 실측 6~9분.
- **잔여 리스크**: 새 08:45 ET signal 잡은 안정 프로세스에서 첫 발화 미경험(첫 실증 2026-06-13 21:45 KST). 미발화 시 워치독이 마감 후 재시작 → 06-11과 동형 사고 위험. → 운영 메모리에 추적 중.

---

## 3. 트러블 #3 — 백테스터 전역 상태 오염

- **증상**: 백테스트가 `mention_history`/`position_scores`/daily_snapshot 등 **라이브 운영 파일을 직접 write** → 백테스트가 비결정적이고 라이브 상태를 더럽힘.
- **해결 (2464a43)**: `run()`이 해당 상태를 run-local 임시 파일로 격리. 이제 백테스트는 결정적·무침습. 단 OHLCV 스냅샷 캐시는 공유(읽기 전용이라 무해).
- **교훈**: 이후 모든 A/B 백테스트 스크립트(`bt_*`)는 temp 메모리/스냅샷/이력으로 arm을 격리하는 패턴을 표준으로 따른다.

---

## 4. 트러블 #4 — LLM 라우터 strict 파싱 → 폴백 폭증

- **증상**: LLM 라우터 도입 직후 22/22건이 rule-based로 폴백 → LLM이 사실상 무력.
- **원인**: LLM이 숫자 필드(`stop_loss_pct` 등)에 `"tighten"` 같은 **문자열**을 반환하는 일이 잦은데, strict JSON 파싱이 이를 전부 invalid 처리.
- **해결**: 필드별 안전 변환(`_num`, `_clamp_stop`) 도입 — 파싱 실패 대신 안전 기본값/클램프로 흡수.
- **교훈**: LLM 출력은 "스키마 위반"을 정상 분포로 가정하고 **방어적으로 파싱**해야 한다. strict는 폴백 폭증을 부른다.

---

## 5. 트러블 #5 — LLM 마스터 스위치 함정

- **증상**: rule-only 재현을 위해 `DecisionRouter(llm_router=False)`를 줘도 LLM이 계속 켜짐.
- **원인**: `DecisionRouter.__init__`(decision_router.py:158) `self.llm_router = bool(llm_router) or bool(config.COMMUNITY_LLM_ROUTER_ENABLED)` — **인자와 config 플래그의 OR**. 인자 False로는 못 끈다.
- **해결**: 진짜 OFF는 `config.COMMUNITY_LLM_ROUTER_ENABLED=False`. 모든 rule-only 백테스트 arm은 config 플래그를 끈다.
- **교훈**: "OR로 합친 enable 플래그"는 끄는 경로가 비대칭이 되어 재현성을 깬다. 백테스트 스크립트 주석에 명시.

---

## 6. 트러블 #6 — "LLM이 쓸모없다"는 착시

- **증상**: rule / LLM집계 / LLM+원문 3버전 백테스트가 **모두 동일 거래·수익(+0.77%)**. LLM 무용론.
- **원인 규명**: LLM 라우터는 **보수적 브레이크 전용**이다(`_apply_llm`, decision_router.py:370-409). 권한은 ① rule SKIP을 단독 BUY로 **못 뒤집음**(line 385), ② confidence<MIN이면 무시, ③ invalid면 fallback, ④ 가능한 건 size 축소·손절/트레일링 타이트닝(클램프 -10~-3%)·rule BUY를 HOLD로 강등. **비대칭: 더 보수적으로만 가능, 공격적으론 불가.** 그런데 funnel이 거의 다 SKIP을 내던 상태라 **LLM이 브레이크 걸 대상 자체가 없었다.**
- **검증 (`scripts/bt_loose_funnel_llm.py`, `bt_loose_day_llm.py`)**: 깔때기를 풀어(score52/cons1.3/neut0.95/min2) 룰이 BUY를 내게 하자, LLM OFF=매수 발생 vs LLM ON=SNDK veto로 0건. → **LLM은 무용이 아니라 일감이 없었을 뿐. 깔때기 풀면 즉시 brake 작동.**
- **교훈**: 보조 모델의 "효과 0"은 모델이 약한 게 아니라 **업스트림이 입력을 안 주는 것**일 수 있다. 효과 측정 전에 "개입 대상이 존재하는가"부터 확인.

---

## 7. 트러블 #7 — 일일 리포트에 게이트 후보 근거가 비었음

- **증상**: 게이트에서 탈락한 후보의 `reason_codes`가 리포트에 `-`로 빈칸.
- **원인**: `community_live.py`가 `decision_records`를 ReportContext에 전달하지 않음(누락).
- **해결 (6b2e271)**: `decision_report.py`에 "후보 상세 판단(근거)" 섹션 추가 + 요약에 수집출처일·LLM호출수. `community_live.py`가 `decision_records`를 전달하도록 수정. test_decision_report 9/9 통과.

---

## 8. 트러블 #8 (현재) — 멀티데이 손익 백테스트가 OHLCV throttle로 타임아웃

- **증상**: 5/13~6/11 손익 백테스트 3회 모두 타임아웃. 손익 기반 LLM/전략 가치 측정 불가.
- **원인**: `reddit_backtester._prefetch_ohlcv`의 OHLCV 캐시 키가 `(ohlcv_start, to)` **문자열**이라, 윈도가 바뀌면 기존 822개 캐시가 전부 미스. Polygon 무료플랜 5req/min → 종목당 12s throttle. 5/13~6/11 윈도 기준 **202종목 미스 × 12s ≈ 40분** → 명령 타임아웃.
- **해결 (이번 세션, `scripts/prefetch_ohlcv_window.py`)**: 목표 윈도 키(`2026-02-02_2026-06-11`)로 미스 종목만 백그라운드 사전 적재. 진행상황은 `data/prefetch_progress.txt`에 즉시 기록. 한 번 채우면 이후 rule/llm·tight/loose 손익 비교는 전부 오프라인.
- **교훈**: rate-limit 병목은 "백테스트를 빠르게"가 아니라 "느린 부분을 한 번만, 캐시 키를 윈도에 고정해 미리" 돌려 분리한다.

---

## 9. 발견 — 전략의 구조적 모순 (SNDK 케이스)

데이터상 SNDK는 캐시 4개월만 봐도 $626→$1881(+200%, 저점→고점 +257%)인데, 시스템은 **두 번 거부**했다(점수컷 + LLM "펌프/RSI과열" veto).

핵심 모순: **이건 WSB 모멘텀 전략인데, 필터가 모멘텀 승자의 지문을 정확히 거른다.**
- "RSI 과열 회피"는 반(反)모멘텀이다. 강한 상승추세는 늘 과매수 상태다.
- "펌프/밈/hype" veto는 사전 구별 불가다. 진짜 돌파와 펌프는 진행 중엔 똑같이 hype로 보인다. hype를 막으면 오른쪽 꼬리(대박)를 통째로 포기한다 — WSB 알파가 바로 그 꼬리다.
- score 수축(K=8)+min_mentions는 초기 모멘텀을 역차별한다. SNDK는 방향성 멘션 ~4개라 score가 50쪽으로 끌려 컷 미달.

**비대칭의 함정**: 모멘텀 전략은 승률이 아니라 소수의 초대박으로 번다. veto 90%가 옳아도 SNDK 하나 놓치면 기대값이 무너진다.

**두 겹 게이트** (A/B 백테스트로 확인, `scripts/bt_sndk_score_cut.py`):
| 게이트 | 컷 57 | 컷 56(loose) |
|---|---|---|
| 룰(score 수축) | SKIP | BUY |
| LLM | (룰 SKIP이라 무관) | **HOLD veto** (펌프위험+RSI과열, size 0.5) |

→ 컷 57에선 점수컷이, 컷 56에선 LLM이 죽인다. 둘 다 손봐야 한다.

**가장 중요한 발견**: veto는 **아키텍처 제약이 아니라 프롬프트 정책**이다. `_apply_llm`은 이미 `action=BUY` + `size_factor_modifier<1` + 타이트 스톱 반환을 지원한다(당신이 원하는 "작게 사고 빡빡한 스톱"). SNDK가 막힌 건 LLM이 스스로 `HOLD`를 골랐기 때문이고, 프롬프트(decision_router.py:454,460)가 *"downsize **or hold**"* 로 HOLD를 공짜 선택지로 줘서 모델이 안전한 HOLD로 수렴한다.

---

## 10. 방향 — 다음 단계

**원칙**: 시스템이 한 덩어리로 뭉친 두 질문을 분리한다.
- **Q1 "강세 신호가 진짜인가 가짜인가"** (반어법·실체 없는 유료펌프·사기/상폐·보유기간 내 바이너리 이벤트) → *유효성* → HOLD/SKIP 정당.
- **Q2 "위험한가"** (RSI 과열·포물선·hype지만 진짜·낮은 지속성) → *사이징/리스크* → 작게 + 타이트 스톱으로만 표현, **veto 금지**.

**Stage 1 — 반증 가능하게 (캐시 완료 후, 진행 예정)**: 손익 매트릭스 백테스트. arm = rule컷{57, loose} × LLM{OFF, veto모드, downsize모드}. arm별로 총수익·거래수·승률 **+ 최대 단일승자 기여도 + 최대 낙폭**을 본다. 짧은 윈도엔 SNDK급 사건이 없을 수 있어 평균만 보면 오른쪽 꼬리를 놓친다 → **분포로 판단**.

**Stage 2 — 코드 변경 (Stage 1이 지지할 때만)**:
1. 프롬프트 분리: "RSI 과열·hype는 HOLD 사유가 아니다. 그 우려는 `size_factor_modifier`↓ + `trailing_stop_pct` 타이트로만. HOLD/SKIP은 강세가 *가짜*라는 증거에만."
2. 가드레일(`_apply_llm`): LLM이 rule BUY→HOLD 강등인데 reason_codes가 **위험형뿐**(rsi/overbought/hype/pump)이면 BUY로 되돌리되 강제 작은 사이즈 + 타이트 스톱. → 기존 `llm_buy_overridden_by_rule_skip`(line 385)의 **대칭쌍**.
3. 룰 모멘텀 레인: 수축 score 컷 미달이어도 강한 velocity(NEW_SPIKE)+가격 돌파면 **작은 base 사이즈**로 BUY 후보 편입. 점수컷 통째 인하(노이즈 복귀) 대신 타깃 수정.

**Stage 3 — 리스크 관리가 진짜 안전망**: veto를 사이즈+스톱으로 대체하면 트레일링 스톱이 load-bearing. hype/NEW_SPIKE는 트레일링 -3~-5%·사이즈 NEW_SPIKE_FACTOR 강제, -50% 덤프를 정말 막는지 백테스트 검증.

**불변(유지)**: `_enforce_safety`의 하드 게이트(neutral/consensus/universe/cost/cash)는 절대 유지. 제거하는 건 *위험을 진입거부로 다루는 오캘리브레이션 브레이크 하나*뿐, *유효성 veto와 하드 안전장치*는 남긴다.

---

## 부록 — A/B 백테스트 스크립트 색인

| 스크립트 | 목적 |
|---|---|
| `scripts/prefetch_ohlcv_window.py` | 목표 윈도 키로 OHLCV 사전캐싱(throttle 병목 우회) |
| `scripts/bt_loose_funnel_llm.py` | 깔때기 완화 후 RULE-only vs LLM-assisted 손익 A/B(5/13~6/11) |
| `scripts/bt_loose_day_llm.py` | 완전캐시된 단일일 run_live로 LLM ON/OFF 비교 |
| `scripts/bt_sndk_score_cut.py` | SNDK 점수컷 57→56 A/B(LLM ON/OFF 각각) |
| `scripts/llm_divergence_report.py` | 라이브 rule↔llm 분기율 추적 |
| `scripts/regression_check_reddit.py` | 5/13~6/6 baseline 회귀 검사(model=finbert) |

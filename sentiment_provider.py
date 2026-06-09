# Design Ref: §2 — SentimentProvider ABC + TextBlobProvider + FinBERTProvider + GPTProvider
# Plan SC: WSB/GPT 확장 시 이 파일에 신규 Provider 추가만으로 완성 (signals.py 수정 불필요)
import hashlib
import json
import logging
import os
from abc import ABC, abstractmethod

from textblob import TextBlob

import config

logger = logging.getLogger(__name__)


class SentimentProvider(ABC):
    """
    감성 점수 계산 추상 베이스 클래스.

    score() 구현체는 signals.py(실시간)와 backtester.py(백테스팅)에서 동일하게 사용된다.
    """

    @abstractmethod
    def score(self, articles: list[dict]) -> tuple[float, list[dict]]:
        """
        기사 목록으로부터 감성 점수와 기사별 분석 결과를 반환한다.

        Args:
            articles: list of {title, description, publishedAt}

        Returns:
            (score [0-100], article_details)
            - score: 감성 점수. 기사 없거나 실패 시 50.0 (중립).
            - article_details: 기사별 분석 결과 목록
        """


class TextBlobProvider(SentimentProvider):
    """
    TextBlob 기반 감성 분석.

    알고리즘:
    1. title + description 결합
    2. TextBlob polarity 계산 (-1 ~ 1)
    3. 평균 polarity → (avg + 1) * 50 → [0, 100]

    article_details: [{title, included: True}] — 모든 기사 포함, 별도 레이블 없음
    """

    def score(self, articles: list[dict]) -> tuple[float, list[dict]]:
        if not articles:
            logger.warning("TextBlob: 뉴스 기사 없음 — 기본값 50.0 반환")
            return 50.0, []

        polarities = []
        article_details = []
        for article in articles:
            text = f"{article.get('title', '')} {article.get('description', '')}".strip()
            detail = {"title": article.get("title", ""), "included": True}
            article_details.append(detail)
            if not text:
                continue
            try:
                polarity = TextBlob(text).sentiment.polarity
                polarities.append(polarity)
            except Exception as e:
                logger.warning(f"TextBlob 개별 기사 분석 실패: {e}")

        if not polarities:
            logger.warning("TextBlob: 유효한 기사 없음 — 기본값 50.0 반환")
            return 50.0, article_details

        avg_polarity = sum(polarities) / len(polarities)
        scaled = (avg_polarity + 1) * 50
        scaled = max(0.0, min(100.0, scaled))

        logger.info(
            f"TextBlob 감성 점수: avg_polarity={avg_polarity:.4f} → scaled={scaled:.2f}"
            f" (기사 {len(polarities)}건)"
        )
        return round(scaled, 2), article_details


class FinBERTProvider(SentimentProvider):
    """
    FinBERT 기반 감성 분석. neutral 필터링 포함.

    알고리즘:
    1. FinBERT로 각 기사의 {positive, negative, neutral} 확률 계산
    2. neutral ≥ NEUTRAL_FILTER_THRESHOLD → included=False (제외)
    3. 유효 기사 < NEUTRAL_FILTER_MIN_ARTICLES → 폴백 (avg(p-n) 방식) + 경고 로그
    4. 신규 공식: pos_count / (pos_count + neg_count) * 100

    indicators._get_finbert_pipeline() 재사용 → ONNX 로컬 캐시 그대로 유지

    use_wsb_preprocessor=True 시 WSBPreprocessor로 슬랭/이모지/반어법 전처리 후 FinBERT 적용.
    # Design Ref: §3.2 — FinBERTProvider: use_wsb_preprocessor 파라미터
    """

    def __init__(self, use_wsb_preprocessor: bool = False):
        # Plan SC SC-05: 기존 finbert 동작 변경 없음 (default=False)
        self._use_wsb = use_wsb_preprocessor
        self._preprocessor = None  # lazy init

    @property
    def preprocessor(self):
        if self._use_wsb and self._preprocessor is None:
            from wsb_preprocessor import WSBPreprocessor
            self._preprocessor = WSBPreprocessor()
        return self._preprocessor

    def _log_preprocessing_samples(
        self,
        original: list[dict],
        processed: list[dict],
        n: int = 3,
    ) -> None:
        """Plan SC SC-02: 전처리 전/후 샘플 로깅 (첫 n개) + 집계."""
        for orig, proc in zip(original[:n], processed[:n]):
            orig_title = orig.get("title", "")[:60]
            proc_title = proc.get("title", "")[:60]
            if orig_title != proc_title:
                logger.debug(
                    f"[WSB-Preprocess] Before: '{orig_title}'"
                    f" → After: '{proc_title}'"
                )
        changed = sum(
            1 for o, p in zip(original, processed)
            if o.get("title") != p.get("title")
            or o.get("body_excerpt") != p.get("body_excerpt")
        )
        if changed > 0:
            logger.info(
                f"[FinBERT-WSB] 전처리 완료: {len(original)}건 중 {changed}건 변환"
            )

    @staticmethod
    def _expand_articles(articles: list[dict]) -> list[dict]:
        """본문 + 댓글을 개별 감성 데이터포인트(article-like dict)로 확장.
        # Design Ref: §7.2 / D1 — score() 단일 확장 지점 (백테스트·라이브 공용)
        # Plan SC: SC-03 본문+댓글 개별 분류

        각 post → [본문 unit(location="body")] + [top_comments별 unit(location="comment")].
        댓글은 부모 글의 source_quality_weight를 상속. 뉴스 기사는 top_comments가 없어
        본문 1건만 생성 → 기존 동작과 동일(무영향, D6).
        location/source_quality_weight를 unit에 부착해 detail까지 전파한다.
        """
        expanded: list[dict] = []
        for a in articles:
            sqw = float(a.get("source_quality_weight", 1.0))
            body = dict(a)
            body["location"] = "body"
            body["source_quality_weight"] = sqw
            expanded.append(body)
            for c in a.get("top_comments", []) or []:
                if not c:
                    continue
                expanded.append({
                    "title": "",
                    "body_excerpt": c,
                    "location": "comment",
                    "source_quality_weight": sqw,
                })
        return expanded

    def score(self, articles: list[dict]) -> tuple[float, list[dict]]:
        if not articles:
            logger.warning("FinBERT: 뉴스 기사 없음 — 기본값 50.0 반환")
            return 50.0, []

        # 본문+댓글 개별 확장 (location/source_quality_weight 부착). 뉴스는 본문만.
        # Design Ref: §7.2 — 루프 진입 전 확장. n_valid(≥10 게이트)가 댓글 포함으로 동작(D3).
        units = self._expand_articles(articles)
        if not units:
            return 50.0, []

        # Plan SC SC-01: finbert-wsb 실행 시 전처리 활성화
        # 전처리는 텍스트만 가공하므로 location/sqw는 원본 units에서 읽는다(전처리가 키를 떨궈도 안전).
        proc_units = units
        if self.preprocessor:
            proc_units = [self.preprocessor.preprocess_post(u) for u in units]
            self._log_preprocessing_samples(units, proc_units)

        # indicators 모듈에서 공유 파이프라인 가져옴 (ONNX 캐시 재사용)
        import indicators
        try:
            pipe = indicators._get_finbert_pipeline()
        except Exception as e:
            logger.error(f"FinBERT 초기화 실패: {e} — 기본값 50.0 반환")
            return 50.0, []

        article_details = []
        fallback_scores = []  # 폴백용: avg(p - n)

        for src, article in zip(units, proc_units):
            location = src.get("location", "body")
            sqw = float(src.get("source_quality_weight", 1.0))
            # Reddit 게시글은 body_excerpt, 뉴스 기사는 description, 댓글은 body_excerpt 사용
            # Design Ref: §3.2 — WSB Daily Thread 댓글(title="")은 body_excerpt가 핵심 텍스트
            body = article.get("description", "") or article.get("body_excerpt", "")
            text = f"{article.get('title', '')} {body}".strip()
            title = article.get("title", "")

            if not text:
                article_details.append({
                    "title": title,
                    "finbert_label": "neutral",
                    "scores": {"positive": 0.0, "negative": 0.0, "neutral": 1.0},
                    "included": False,
                    "location": location,
                    "source_quality_weight": sqw,
                })
                continue

            try:
                result = pipe(text[:512], truncation=True)
                # result 형태: [[{label: "positive", score: 0.8}, ...]]
                label_map = {r["label"]: r["score"] for r in result[0]}
                positive = label_map.get("positive", 0.0)
                negative = label_map.get("negative", 0.0)
                neutral = label_map.get("neutral", 0.0)

                finbert_label = max(label_map, key=label_map.get)
                included = neutral < config.NEUTRAL_FILTER_THRESHOLD

                fallback_scores.append(positive - negative)
                article_details.append({
                    "title": title,
                    "finbert_label": finbert_label,
                    "scores": {
                        "positive": round(positive, 4),
                        "negative": round(negative, 4),
                        "neutral": round(neutral, 4),
                    },
                    "included": included,
                    "location": location,
                    "source_quality_weight": sqw,
                })
            except Exception as e:
                logger.warning(f"FinBERT 개별 기사 분석 실패: {e}")
                article_details.append({
                    "title": title,
                    "finbert_label": "neutral",
                    "scores": {"positive": 0.0, "negative": 0.0, "neutral": 1.0},
                    "included": False,
                    "location": location,
                    "source_quality_weight": sqw,
                })

        valid = [a for a in article_details if a["included"]]
        n_valid = len(valid)

        # 유효 기사 부족 → 폴백
        if n_valid < config.NEUTRAL_FILTER_MIN_ARTICLES:
            logger.warning(
                f"FinBERT: 유효 기사 부족 ({n_valid}건 < {config.NEUTRAL_FILTER_MIN_ARTICLES}건)"
                f" — 폴백 방식(avg p-n) 사용"
            )
            if not fallback_scores:
                return 50.0, article_details
            avg_raw = sum(fallback_scores) / len(fallback_scores)
            scaled = (avg_raw + 1) * 50
            scaled = max(0.0, min(100.0, scaled))
            logger.info(
                f"FinBERT(폴백) 감성 점수: avg_raw={avg_raw:.4f} → scaled={scaled:.2f}"
            )
            return round(scaled, 2), article_details

        # 신규 공식: pos / (pos + neg) * 100
        pos_count = sum(1 for a in valid if a["finbert_label"] == "positive")
        neg_count = sum(1 for a in valid if a["finbert_label"] == "negative")

        if pos_count + neg_count == 0:
            logger.warning("FinBERT: 유효 기사 중 positive/negative 없음 — 기본값 50.0 반환")
            return 50.0, article_details

        score = pos_count / (pos_count + neg_count) * 100
        logger.info(
            f"FinBERT 감성 점수: pos={pos_count}/{n_valid}건 valid → score={score:.2f}"
            f" (neutral 필터 제외: {len(article_details) - n_valid}건)"
        )
        return round(score, 2), article_details


class GPTProvider(SentimentProvider):
    """
    OpenAI GPT-5.4 Mini 기반 감성 분석.
    # Design Ref: §2.1 — GPTProvider (batch 10, gpt_cache.json)
    # Plan SC FR-01: GPTProvider OpenAI gpt-5.4-mini 구현
    # Plan SC FR-02: 배치 처리(10건/호출) + gpt_cache.json 캐시

    알고리즘:
    1. 각 텍스트를 sha256[:16]으로 캐시 키 생성
    2. 캐시 미스 항목만 GPT-5.4 Mini 배치 호출 (10건/호출)
    3. bullish/bearish/neutral 분류 → pos/(pos+neg)*100 공식
    4. neutral은 score에 포함하지 않음 (FinBERT와 동일 방식)

    입력 article 형태:
      뉴스:   {"title": str, "description": str}
      Reddit: {"title": str, "body_excerpt": str, "top_comments": list[str]}
    """

    _SYSTEM_PROMPT = (
        "You are a financial sentiment classifier. "
        "For each numbered item, classify the sentiment as exactly one of: "
        "bullish, bearish, or neutral. "
        "Return a JSON array of labels in the same order, e.g. [\"bullish\",\"neutral\",\"bearish\"]. "
        "Do not include any other text."
    )

    def score(self, articles: list[dict]) -> tuple[float, list[dict]]:
        if not articles:
            logger.warning("GPT: 기사 없음 — 기본값 50.0 반환")
            return 50.0, []

        cache = self._load_cache()
        texts = [self._build_text(a) for a in articles]
        keys = [self._text_key(t) for t in texts]

        # 캐시 미스 항목 수집
        miss_indices = [i for i, k in enumerate(keys) if k not in cache]
        if miss_indices:
            miss_texts = [texts[i] for i in miss_indices]
            labels = self._batch_call_all(miss_texts)
            for idx, label in zip(miss_indices, labels):
                cache[keys[idx]] = {"label": label, "cached_at": _today()}
            self._save_cache(cache)

        article_details = []
        pos_count = neg_count = 0
        for i, article in enumerate(articles):
            label = cache[keys[i]]["label"]
            included = label != "neutral"
            if label == "bullish":
                pos_count += 1
            elif label == "bearish":
                neg_count += 1
            article_details.append({
                "title": article.get("title", ""),
                "label": label,
                "included": included,
                "cached": keys[i] in cache,
            })

        if pos_count + neg_count == 0:
            logger.warning("GPT: bullish/bearish 없음 — 기본값 50.0 반환")
            return 50.0, article_details

        score = pos_count / (pos_count + neg_count) * 100
        logger.info(
            f"GPT 감성 점수: pos={pos_count}, neg={neg_count} → score={score:.2f}"
            f" (neutral 제외, cached={len(keys) - len(miss_indices)}/{len(keys)})"
        )
        return round(score, 2), article_details

    def _build_text(self, article: dict) -> str:
        """뉴스/Reddit 공통 텍스트 구성. 길이 제한 적용."""
        title = article.get("title", "")[:config.GPT_POST_TITLE_MAX]
        # Reddit
        if "body_excerpt" in article:
            body = article.get("body_excerpt", "")[:config.GPT_POST_BODY_MAX]
            comments = article.get("top_comments", [])[:config.GPT_TOP_COMMENTS]
            comment_str = " | ".join(c[:config.GPT_COMMENT_MAX] for c in comments)
            return f"{title} {body} {comment_str}".strip()
        # 뉴스
        desc = article.get("description", "")
        return f"{title} {desc}".strip()

    def _batch_call_all(self, texts: list[str]) -> list[str]:
        """texts를 GPT_BATCH_SIZE 단위로 나눠 GPT-5.4 Mini 호출."""
        results = []
        for i in range(0, len(texts), config.GPT_BATCH_SIZE):
            batch = texts[i: i + config.GPT_BATCH_SIZE]
            results.extend(self._batch_call(batch))
        return results

    def _batch_call(self, texts: list[str]) -> list[str]:
        """GPT-5.4 Mini에 배치 호출. 응답 파싱 실패 시 "neutral" 폴백."""
        try:
            from openai import OpenAI
            client = OpenAI(api_key=config.OPENAI_API_KEY)
            numbered = "\n".join(f"[{i+1}] {t}" for i, t in enumerate(texts))
            response = client.chat.completions.create(
                model=config.GPT_MODEL,
                messages=[
                    {"role": "system", "content": self._SYSTEM_PROMPT},
                    {"role": "user", "content": numbered},
                ],
                temperature=0,
            )
            raw = response.choices[0].message.content.strip()
            labels = json.loads(raw)
            if isinstance(labels, list) and len(labels) == len(texts):
                return [str(l).lower() for l in labels]
            logger.warning(f"GPT 응답 길이 불일치 ({len(labels)} vs {len(texts)}) — neutral 폴백")
        except Exception as e:
            logger.error(f"GPT 배치 호출 실패: {e} — neutral 폴백")
        return ["neutral"] * len(texts)

    def _text_key(self, text: str) -> str:
        """sha256(text)[:16] → 캐시 키"""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    def _load_cache(self) -> dict:
        if os.path.exists(config.GPT_CACHE_FILE):
            try:
                with open(config.GPT_CACHE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_cache(self, cache: dict) -> None:
        os.makedirs(os.path.dirname(config.GPT_CACHE_FILE), exist_ok=True)
        with open(config.GPT_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)


def _today() -> str:
    from datetime import date
    return date.today().isoformat()


def get_provider(name: str) -> SentimentProvider:
    """
    이름으로 Provider 인스턴스를 반환한다.
    # Plan SC FR-03: get_provider("gpt5") 분기 추가

    Args:
        name: "textblob" | "finbert" | "gpt5"

    Returns:
        SentimentProvider 인스턴스

    Raises:
        ValueError: 알 수 없는 provider 이름
    """
    if name == "textblob":
        return TextBlobProvider()
    if name == "finbert":
        return FinBERTProvider(use_wsb_preprocessor=False)
    if name == "finbert-wsb":
        # Plan SC SC-01: finbert-wsb 모델 옵션 — WSBPreprocessor 활성화
        return FinBERTProvider(use_wsb_preprocessor=True)
    if name == config.GPT_MODEL_ALIAS:
        return GPTProvider()
    raise ValueError(
        f"알 수 없는 SentimentProvider: '{name}'. "
        f"사용 가능: textblob, finbert, finbert-wsb, {config.GPT_MODEL_ALIAS}"
    )

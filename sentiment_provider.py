# Design Ref: §2 — SentimentProvider ABC + TextBlobProvider + FinBERTProvider
# Plan SC: WSB/GPT 확장 시 이 파일에 신규 Provider 추가만으로 완성 (signals.py 수정 불필요)
import logging
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
    """

    def score(self, articles: list[dict]) -> tuple[float, list[dict]]:
        if not articles:
            logger.warning("FinBERT: 뉴스 기사 없음 — 기본값 50.0 반환")
            return 50.0, []

        # indicators 모듈에서 공유 파이프라인 가져옴 (ONNX 캐시 재사용)
        import indicators
        try:
            pipe = indicators._get_finbert_pipeline()
        except Exception as e:
            logger.error(f"FinBERT 초기화 실패: {e} — 기본값 50.0 반환")
            return 50.0, []

        article_details = []
        fallback_scores = []  # 폴백용: avg(p - n)

        for article in articles:
            text = f"{article.get('title', '')} {article.get('description', '')}".strip()
            title = article.get("title", "")

            if not text:
                article_details.append({
                    "title": title,
                    "finbert_label": "neutral",
                    "scores": {"positive": 0.0, "negative": 0.0, "neutral": 1.0},
                    "included": False,
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
                })
            except Exception as e:
                logger.warning(f"FinBERT 개별 기사 분석 실패: {e}")
                article_details.append({
                    "title": title,
                    "finbert_label": "neutral",
                    "scores": {"positive": 0.0, "negative": 0.0, "neutral": 1.0},
                    "included": False,
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


def get_provider(name: str) -> SentimentProvider:
    """
    이름으로 Provider 인스턴스를 반환한다.

    Args:
        name: "textblob" | "finbert"

    Returns:
        SentimentProvider 인스턴스

    Raises:
        ValueError: 알 수 없는 provider 이름
    """
    if name == "textblob":
        return TextBlobProvider()
    if name == "finbert":
        return FinBERTProvider()
    raise ValueError(f"알 수 없는 SentimentProvider: '{name}'. 사용 가능: textblob, finbert")

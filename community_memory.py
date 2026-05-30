# Design Ref: community-opinion-agent §3.3 — CommunityMemoryStore (jsonl backend)
# Plan FR-2.1~2: 과거 opinion snapshot / reflection 저장 + 유사 사례 검색.
# 향후 Chroma/Faiss 교체를 위해 MemoryBackend 추상 인터페이스 분리.
# 회귀 보호: COMMUNITY_MEMORY_ENABLED=False → add/retrieve no-op.
import dataclasses
import json
import logging
import os
from abc import ABC, abstractmethod

import config

logger = logging.getLogger(__name__)

KIND_OPINION = "opinion_snapshots"
KIND_LOW = "low_level_reflections"
KIND_HIGH = "high_level_reflections"


def _to_record(obj) -> dict:
    """dataclass/객체/dict → 직렬화 dict."""
    if isinstance(obj, dict):
        return obj
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    # 일반 객체: __dict__ 폴백
    return {k: v for k, v in vars(obj).items() if not k.startswith("_")}


# ---------------------------------------------------------------------------
# Backend 추상화 (향후 vector DB 교체 지점)
# ---------------------------------------------------------------------------
class MemoryBackend(ABC):
    @abstractmethod
    def append(self, kind: str, record: dict) -> None: ...

    @abstractmethod
    def read_all(self, kind: str) -> list[dict]: ...


class JsonlMemoryStore(MemoryBackend):
    """data/community/memory/{kind}.jsonl append-only 백엔드."""

    def __init__(self, base_dir: str = None):
        self.base_dir = base_dir or config.COMMUNITY_MEMORY_DIR

    def _path(self, kind: str) -> str:
        return os.path.join(self.base_dir, f"{kind}.jsonl")

    def append(self, kind: str, record: dict) -> None:
        os.makedirs(self.base_dir, exist_ok=True)
        with open(self._path(kind), "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def read_all(self, kind: str) -> list[dict]:
        path = self._path(kind)
        if not os.path.exists(path):
            return []
        out: list[dict] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning(f"{kind}.jsonl 손상 라인 건너뜀")
        return out


class InMemoryBackend(MemoryBackend):
    """프로세스 메모리 보관 백엔드 (백테스트 결정성용 — 전역 파일 미오염).
    단일 run 내에서만 누적되어 검색이 결정적이다 (Design Ref §3.3 / G1)."""

    def __init__(self):
        self._data: dict[str, list[dict]] = {}

    def append(self, kind: str, record: dict) -> None:
        self._data.setdefault(kind, []).append(record)

    def read_all(self, kind: str) -> list[dict]:
        return list(self._data.get(kind, []))


# ---------------------------------------------------------------------------
# 검색 점수 (초기 휴리스틱 — Design Ref §3.3)
# ---------------------------------------------------------------------------
def _tokens(text) -> set:
    if isinstance(text, (list, set, tuple)):
        return {str(t).upper() for t in text}
    return {w.upper() for w in str(text or "").replace(",", " ").split() if w}


def _query_tokens(query) -> set:
    """query dict의 텍스트성 필드를 토큰 집합으로."""
    if isinstance(query, dict):
        toks: set = set()
        for k, v in query.items():
            if isinstance(v, str):
                toks |= _tokens(v)
            elif k in ("keywords", "top_keywords"):
                toks |= _tokens(v)
        return toks
    return _tokens(query)


def _num(rec: dict, key: str):
    v = rec.get(key)
    return v if isinstance(v, (int, float)) else None


def _similarity(record: dict, symbol: str, query) -> float:
    """record와 (symbol, query) 유사도 0~1+ 휴리스틱."""
    score = 0.0
    if record.get("symbol") == symbol:
        score += 0.4
    q = query if isinstance(query, dict) else {}

    # universe_tier 동일
    if q.get("universe_tier") and record.get("universe_tier") == q.get("universe_tier"):
        score += 0.1
    # velocity_state 동일
    if q.get("velocity_state") and record.get("velocity_state") == q.get("velocity_state"):
        score += 0.05
    # opinion_trend 동일
    if q.get("opinion_trend") and record.get("opinion_trend") == q.get("opinion_trend"):
        score += 0.05

    # 수치 유사도
    qs = q.get("opinion_score"); rs = _num(record, "opinion_score")
    if isinstance(qs, (int, float)) and rs is not None:
        score += 0.15 * max(0.0, 1 - abs(qs - rs) / 100.0)
    qc = q.get("consensus_ratio"); rc = _num(record, "consensus_ratio")
    if isinstance(qc, (int, float)) and rc is not None:
        score += 0.1 * max(0.0, 1 - min(abs(qc - rc) / 3.0, 1.0))
    qn = q.get("neutral_ratio"); rn = _num(record, "neutral_ratio")
    if isinstance(qn, (int, float)) and rn is not None:
        score += 0.1 * max(0.0, 1 - min(abs(qn - rn), 1.0))
    qp = q.get("persistence_days"); rp = _num(record, "persistence_days")
    if isinstance(qp, (int, float)) and rp is not None:
        score += 0.05 * max(0.0, 1 - min(abs(qp - rp) / 5.0, 1.0))

    # query keyword overlap (jaccard)
    qtok = _query_tokens(query)
    rtok = _tokens(record.get("top_keywords")) | _tokens(record.get("query"))
    if qtok and rtok:
        inter = len(qtok & rtok)
        union = len(qtok | rtok)
        if union:
            score += 0.1 * (inter / union)

    # 과거 결과 가중
    label = str(record.get("result_label") or record.get("decision_quality") or "")
    if label.startswith("success") or label.startswith("good") or "success" in label:
        score += 0.1
    elif label.startswith("failed") or label.startswith("bad") or "failure" in label:
        score -= 0.1

    return score


# ---------------------------------------------------------------------------
# CommunityMemoryStore
# ---------------------------------------------------------------------------
class CommunityMemoryStore:
    def __init__(self, backend: MemoryBackend = None, top_k: int = None):
        self.backend = backend or JsonlMemoryStore()
        self.top_k = top_k if top_k is not None else config.COMMUNITY_MEMORY_TOP_K

    @property
    def enabled(self) -> bool:
        return bool(config.COMMUNITY_MEMORY_ENABLED)

    # --- 저장 ---
    def add_opinion_snapshot(self, snapshot) -> None:
        if self.enabled:
            self.backend.append(KIND_OPINION, _to_record(snapshot))

    def add_low_level_reflection(self, reflection) -> None:
        if self.enabled:
            self.backend.append(KIND_LOW, _to_record(reflection))

    def add_high_level_reflection(self, reflection) -> None:
        if self.enabled:
            self.backend.append(KIND_HIGH, _to_record(reflection))

    # --- 검색 ---
    def _retrieve(self, kind, symbol, query, top_k):
        if not self.enabled:
            return []
        k = top_k if top_k is not None else self.top_k
        records = self.backend.read_all(kind)
        scored = sorted(records, key=lambda r: _similarity(r, symbol, query), reverse=True)
        return scored[:k]

    def retrieve_similar_opinions(self, symbol, query, top_k=None) -> list[dict]:
        return self._retrieve(KIND_OPINION, symbol, query, top_k)

    def retrieve_low_level_reflections(self, symbol, query, top_k=None) -> list[dict]:
        return self._retrieve(KIND_LOW, symbol, query, top_k)

    def retrieve_high_level_reflections(self, symbol, query, top_k=None) -> list[dict]:
        return self._retrieve(KIND_HIGH, symbol, query, top_k)

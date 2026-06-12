# 운영 config는 LLM 라우터 ON(2026-06-13)이지만, 테스트는 네트워크 없이 결정적으로
# 돌아야 한다. 기본값을 OFF로 고정하고, LLM 경로 테스트는 각자 플래그를 명시 토글한다.
import pytest

import config


@pytest.fixture(autouse=True)
def _llm_router_off_by_default(monkeypatch):
    monkeypatch.setattr(config, "COMMUNITY_LLM_ROUTER_ENABLED", False)

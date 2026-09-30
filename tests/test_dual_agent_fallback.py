"""
Unit and integration tests for LLM Gateway, 429 quota resilience, and OpenAI/Groq dual-agent fallback.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from app.config import settings
from app.main import app
from app.llm_gateway import _is_quota_exhausted, call_llm_json, call_llm_text


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_is_quota_exhausted_detection():
    """Verifies reliable detection of 429, ResourceExhausted, and quota errors."""
    assert _is_quota_exhausted(Exception("429 Client Error: Too Many Requests"))
    assert _is_quota_exhausted(Exception("ResourceExhausted: 429 Resource has been exhausted (e.g. check quota)."))
    assert _is_quota_exhausted(Exception("GoogleAPICallError: Quota exceeded for quota metric"))
    assert _is_quota_exhausted(Exception("Rate limit reached for default-gemini-2.5-flash"))
    assert not _is_quota_exhausted(Exception("404 Not Found"))
    assert not _is_quota_exhausted(Exception("SyntaxError: invalid syntax"))


def test_openai_settings_config():
    """Verifies that OPENAI environment configuration is properly loaded into Settings."""
    assert hasattr(settings, "OPENAI_API_KEY")
    assert hasattr(settings, "OPENAI_BASE_URL")
    assert hasattr(settings, "OPENAI_MODEL")
    assert hasattr(settings, "has_openai_key")
    assert isinstance(settings.has_openai_key, bool)


def test_dual_agent_fallback_on_429_exhaustion(client: TestClient):
    """
    Verifies that when Gemini raises 429 Quota Exhausted, the endpoints:
    - /api/ingest
    - /api/intel/synthesize
    - /api/articles
    seamlessly fall back to local rule-based intelligence without crashing or throwing 500.
    """
    quota_err = Exception("429 Resource has been exhausted (quota exceeded)")

    # 1. Ingestion endpoint under simulated 429 quota exhaustion
    import uuid
    uid = uuid.uuid4().hex[:6]
    with patch("app.processor.get_gemini_client") as mock_client:
        mock_instance = MagicMock()
        mock_instance.models.generate_content.side_effect = quota_err
        mock_client.return_value = mock_instance

        res = client.post("/api/ingest", json={
            "title": f"Quantum Encrypted Communications Field Mesh {uid}",
            "content": f"Defense communication units have completed field integration of tactical quantum mesh radios across forward air command headquarters {uid}.",
            "source": "Field OSINT"
        })
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()
        assert data["category"] in ["Cybersecurity", "Defence Technology", "Space"]
        assert len(data["summary"]) > 10

    # 2. Intel synthesis endpoint under simulated 429 quota exhaustion
    with patch("app.llm_gateway._get_gemini_client") as mock_gw_client:
        mock_instance = MagicMock()
        mock_instance.models.generate_content.side_effect = quota_err
        mock_gw_client.return_value = mock_instance

        res_synth = client.post("/api/intel/synthesize", json={
            "query": "Where did the quantum encrypted communications test occur?",
            "lang": "EN"
        })
        assert res_synth.status_code == 200, f"Expected 200, got {res_synth.status_code}: {res_synth.text}"
        synth_data = res_synth.json()
        assert "executive_assessment" in synth_data
        assert len(synth_data["executive_assessment"]) > 20

    # 3. Articles feed endpoint with Indic translation under simulated 429 quota exhaustion
    with patch("app.translator.get_genai_client") as mock_tr_client:
        mock_instance = MagicMock()
        mock_instance.models.generate_content.side_effect = quota_err
        mock_tr_client.return_value = mock_instance

        res_arts = client.get("/api/articles?limit=5&lang=KN")
        assert res_arts.status_code == 200
        arts = res_arts.json()
        assert len(arts) > 0

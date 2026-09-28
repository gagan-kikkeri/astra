"""
Comprehensive automated test suite for ASTRA Sentinel.
Tests ingestion, SHA-256 deduplication, validation, FTS5 search, SitRep synthesis,
and the unified autonomous agent execution pipeline with network error verification.
"""

import os
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from app.config import settings
from app.database import init_db
from app.main import app


@pytest.fixture(scope="session", autouse=True)
def test_environment(tmp_path_factory):
    """Configures an isolated test database for pytest runs."""
    temp_dir = tmp_path_factory.mktemp("astra_test_data")
    test_db_path = temp_dir / "test_sentinel.db"
    settings.DB_PATH = str(test_db_path)
    init_db()
    yield
    if test_db_path.exists():
        try:
            os.remove(test_db_path)
        except OSError:
            pass


@pytest.fixture(scope="module")
def client():
    """Initializes FastAPI TestClient with lifespan execution."""
    with TestClient(app) as test_client:
        yield test_client


def test_ingest_unique_article(client: TestClient):
    """
    1. test_ingest_unique_article:
    Verifies successful ingestion, category extraction, SHA-256 calculation, and 200 response.
    """
    payload = {
        "title": "Quantum Magnetometer Submarine Sensor Validated in Deep Sea Chokepoint",
        "content": "Naval operational units deployed a distributed quantum magnetometer array across deep sea maritime chokepoints to detect wake turbulence and magnetic anomalies from submerged nuclear submarines at ultra-quiet cavitation speeds.",
        "source": "Naval OSINT Bureau",
        "date": "2026-09-28"
    }
    response = client.post("/api/ingest", json=payload)
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

    data = response.json()
    assert "id" in data
    assert data["id"].startswith("AST-")
    assert "content_hash" in data
    assert len(data["content_hash"]) == 64  # SHA-256 hex length
    assert data["category"] in [
        "Aerospace", "Naval", "Land Systems", "Cybersecurity",
        "Space", "AI/Robotics", "Defence Technology"
    ]
    assert data["category"] == "Naval"
    assert data["threat_impact"] in ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    assert isinstance(data["keywords"], list)
    assert len(data["keywords"]) >= 3
    assert isinstance(data["entities"], list)
    assert len(data["entities"]) >= 1


def test_deduplication_collision(client: TestClient):
    """
    2. test_deduplication_collision:
    Attempts to ingest identical content twice; asserts HTTP 409 Conflict with hash alert.
    """
    payload = {
        "title": "Hypersonic Boost-Glide Vehicle Flight Telemetry Intercepted",
        "content": "Radar telemetry recorded a boost-glide flight vehicle maneuvering in the upper atmosphere at Mach 8.2 with trajectory shifts avoiding standard missile interception arcs.",
        "source": "Aerospace OSINT",
        "date": "2026-09-28"
    }

    # First ingestion should succeed
    res1 = client.post("/api/ingest", json=payload)
    assert res1.status_code == 200
    first_record = res1.json()

    # Second ingestion with identical content must trigger Duplicate Gate
    res2 = client.post("/api/ingest", json=payload)
    assert res2.status_code == 409, f"Expected 409 Conflict, got {res2.status_code}: {res2.text}"

    detail = res2.json().get("detail", "")
    assert "DUPLICATE DETECTED" in detail
    assert first_record["content_hash"][:8] in detail
    assert first_record["id"] in detail


def test_malformed_input_rejection(client: TestClient):
    """
    3. test_malformed_input_rejection:
    Sends empty title/body or blank whitespace; asserts 422 Unprocessable Entity.
    """
    # Empty title and content
    res1 = client.post("/api/ingest", json={"title": "", "content": ""})
    assert res1.status_code == 422

    # Whitespace-only title
    res2 = client.post("/api/ingest", json={"title": "   ", "content": "Valid body text here."})
    assert res2.status_code == 422

    # Missing required field
    res3 = client.post("/api/ingest", json={"title": "Only Title Provided"})
    assert res3.status_code == 422

    # Whitespace-only content
    res4 = client.post("/api/ingest", json={"title": "Valid Headline", "content": "    \n\t   "})
    assert res4.status_code == 422


def test_fts5_acronym_search(client: TestClient):
    """
    4. test_fts5_acronym_search:
    Queries exact military acronyms (e.g., 'UAV') and verifies FTS5 matching.
    """
    # Ensure article with acronym UAV is present
    uav_payload = {
        "title": "Autonomous UAV Reconnaissance Swarm Deployed Over Border Sectors",
        "content": "Special military units commissioned an autonomous UAV surveillance flight carrying high-resolution synthetic aperture radar for border observation and threat detection.",
        "source": "Border Recon Wire",
        "date": "2026-09-28"
    }
    client.post("/api/ingest", json=uav_payload)

    # Search for acronym "UAV"
    res = client.get("/api/search?q=UAV")
    assert res.status_code == 200
    data = res.json()

    assert data["total_hits"] >= 1
    assert "latency_ms" in data
    assert data["engine"] in ["FTS5_BM25", "SQL_RECENCY", "SQL_LIKE_FALLBACK"]
    
    # Check that returned results mention UAV
    match_found = False
    for art in data["articles"]:
        text_corpus = f"{art['title']} {art['content']} {' '.join(art['keywords'])} {' '.join(art['entities'])}".upper()
        if "UAV" in text_corpus:
            match_found = True
            break
    assert match_found, "Expected at least one article matching military acronym 'UAV'"


def test_sitrep_generation(client: TestClient):
    """
    5. test_sitrep_generation:
    Executes SitRep briefing endpoint and asserts valid schema and source citation IDs.
    """
    sitrep_request = {
        "topic": "Submarine Detection and Acoustic Surveillance",
        "category": "Naval",
        "max_articles": 3
    }
    response = client.post("/api/sitrep", json=sitrep_request)
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

    data = response.json()
    assert "topic" in data
    assert data["topic"] == sitrep_request["topic"]
    assert "classification" in data
    assert isinstance(data["classification"], str)
    assert len(data["classification"]) > 0
    assert "executive_assessment" in data
    assert len(data["executive_assessment"]) > 20
    assert "key_actors" in data
    assert isinstance(data["key_actors"], list)
    assert "timeline" in data
    assert isinstance(data["timeline"], list)
    assert "cited_article_ids" in data
    assert isinstance(data["cited_article_ids"], list)
    assert len(data["cited_article_ids"]) >= 1

    # Verify that cited IDs start with AST-
    for cid in data["cited_article_ids"]:
        assert cid.startswith("AST-")


def test_health_and_telemetry(client: TestClient):
    """
    Verifies /api/health and /api/stats return operational telemetry,
    verifying WAL mode, FTS5 status, category breakdowns, and network timeouts.
    """
    health_res = client.get("/api/health")
    assert health_res.status_code == 200
    health = health_res.json()
    assert health["status"] == "operational"
    assert health["wal_mode"] is True
    assert health["fts5_active"] is True
    assert health["document_count"] > 0
    assert "timeout_seconds" in health

    stats_res = client.get("/api/stats")
    assert stats_res.status_code == 200
    stats = stats_res.json()
    assert stats["total_articles"] > 0
    assert stats["active_categories"] > 0
    assert isinstance(stats["categories_breakdown"], dict)
    assert isinstance(stats["threat_breakdown"], dict)


def test_analyze_agent_execution_flow(client: TestClient):
    """
    Verifies the unified single-flow agent execution endpoint (/api/analyze).
    Asserts 4 sequential steps in agent_trace, domain classification, entity extraction,
    and executive summary synthesis.
    """
    text_payload = {
        "text_or_url": "Next-Generation Fighter Jet Stealth Coating Validated in Radar Chamber.\n\nAir force material scientists tested a radar-absorbent nanostructured polymer skin applied to 5th-gen fighter aircraft wings. Electromagnetic chamber trials confirmed a 40% reduction in X-band AESA radar reflection cross-section under supersonic flight simulation."
    }
    res = client.post("/api/analyze", json=text_payload)
    assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"

    data = res.json()
    assert data["id"].startswith("AST-")
    assert data["category"] in [
        "Aerospace", "Naval", "Land Systems", "Cybersecurity",
        "Space", "AI/Robotics", "Defence Technology"
    ]
    assert data["category"] == "Aerospace"
    assert "executive_summary" in data
    assert len(data["executive_summary"]) > 20
    assert isinstance(data["entities"], list)
    assert len(data["entities"]) >= 1

    # Verify 4 sequential agent steps
    trace = data.get("agent_trace", [])
    assert len(trace) == 4
    assert trace[0]["step_num"] == 1
    assert "hash integrity" in trace[0]["name"].lower()
    assert trace[1]["step_num"] == 2
    assert "classifying" in trace[1]["name"].lower()
    assert trace[2]["step_num"] == 3
    assert "extracting" in trace[2]["name"].lower()
    assert trace[3]["step_num"] == 4
    assert "synthesizing" in trace[3]["name"].lower()


def test_analyze_duplicate_collision(client: TestClient):
    """
    Verifies duplicate prevention gate on /api/analyze:
    Submitting the exact same content returns HTTP 409 Conflict.
    """
    payload = {
        "text_or_url": "Air-Gapped Telemetry Sensor Breach Simulated in Red Team Drill.\n\nCyber combat teams demonstrated acoustic exfiltration vectors against air-gapped SCADA systems in hardened command shelters."
    }
    # First submission
    r1 = client.post("/api/analyze", json=payload)
    assert r1.status_code == 200

    # Duplicate submission
    r2 = client.post("/api/analyze", json=payload)
    assert r2.status_code == 409
    assert "DUPLICATE DETECTED" in r2.json()["detail"]


def test_analyze_network_resolution_error(client: TestClient):
    """
    Verifies graceful network error handling on /api/analyze:
    When an unreachable or unresolvable domain is submitted, the backend raises
    HTTP 502 with a clear network resolution diagnostic message rather than
    silently falling back without user feedback.
    """
    invalid_url_payload = {
        "text_or_url": "https://nonexistent-military-domain-xyz-404.mil/report"
    }
    res = client.post("/api/analyze", json=invalid_url_payload)
    assert res.status_code == 502
    detail = res.json().get("detail", "")
    assert "Network" in detail or "resolution" in detail or "reach" in detail

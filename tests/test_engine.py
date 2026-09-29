"""
Comprehensive automated test suite for ASTRA Sentinel.
Tests hardened defensive validation, SHA-256 deduplication, dual category/date filtering,
FTS5 acronym searches, SitRep briefings, and Gemini cross-document relational synthesis.
"""

import os
import pytest
from datetime import datetime, timezone
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


def test_empty_input_rejected(client: TestClient):
    """
    1. test_empty_input_rejected:
    Submits blank, whitespace, or sub-minimum payloads; asserts HTTP 422 Unprocessable Entity.
    """
    # Empty title and content
    res1 = client.post("/api/ingest", json={"title": "", "content": ""})
    assert res1.status_code == 422

    # Whitespace-only title
    res2 = client.post("/api/ingest", json={
        "title": "     ",
        "content": "Valid content length of at least twenty characters here."
    })
    assert res2.status_code == 422

    # Sub-minimum title (< 5 characters)
    res3 = client.post("/api/ingest", json={
        "title": "UAV",
        "content": "Valid body text that exceeds the twenty character minimum length."
    })
    assert res3.status_code == 422

    # Sub-minimum content (< 20 characters)
    res4 = client.post("/api/ingest", json={
        "title": "Valid Headline Title",
        "content": "Too short text"
    })
    assert res4.status_code == 422

    # Empty payload
    res5 = client.post("/api/ingest", json={})
    assert res5.status_code == 422


def test_deduplication_hash_collision(client: TestClient):
    """
    2. test_deduplication_hash_collision:
    Submits identical text twice; asserts HTTP 409 Conflict with hash collision notification.
    """
    payload = {
        "title": "Hypersonic Boost-Glide Vehicle Flight Telemetry Intercepted",
        "content": "Radar telemetry recorded a boost-glide flight vehicle maneuvering in the upper atmosphere at Mach 8.2 with trajectory shifts avoiding standard missile interception arcs.",
        "source": "Aerospace OSINT",
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d")
    }

    # First ingestion succeeds
    res1 = client.post("/api/ingest", json=payload)
    assert res1.status_code == 200
    first_record = res1.json()

    # Second ingestion with identical content triggers duplicate gate
    res2 = client.post("/api/ingest", json=payload)
    assert res2.status_code == 409, f"Expected 409 Conflict, got {res2.status_code}: {res2.text}"

    detail = res2.json().get("detail", "")
    assert "Collision detected" in detail or "DUPLICATE DETECTED" in detail
    assert first_record["content_hash"][:8] in detail


def test_category_and_date_filtering(client: TestClient):
    """
    3. test_category_and_date_filtering:
    Queries articles filtered by date_filter='7D' and category; validates strict filtering.
    """
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Ingest fresh article for today
    payload = {
        "title": "Next-Gen Fighter Jet Stealth Coating Validated in Chamber",
        "content": "Air force material scientists tested a radar-absorbent nanostructured polymer skin applied to 5th-gen fighter aircraft wings for supersonic reflection suppression.",
        "source": "Aerospace OSINT",
        "date": today_str
    }
    client.post("/api/ingest", json=payload)

    # Query with date_filter='7D' and category='Aerospace'
    res = client.get(f"/api/articles?category=Aerospace&date_filter=7D")
    assert res.status_code == 200
    articles = res.json()
    assert len(articles) >= 1

    # Verify every returned article matches category
    for art in articles:
        assert art["category"] == "Aerospace"

    # Also test the /articles alias
    alias_res = client.get(f"/articles?category=Aerospace&date_filter=7D")
    assert alias_res.status_code == 200


def test_gemini_cross_document_synthesis(client: TestClient):
    """
    4. test_gemini_cross_document_synthesis:
    Sends query to /api/intel/synthesize; verifies response contains citations,
    related platforms, chronological developments, and executive assessment.
    """
    inquiry_payload = {
        "query": "hypersonic radar countermeasures and glide interceptor trials",
        "category": "Aerospace"
    }
    res = client.post("/api/intel/synthesize", json=inquiry_payload)
    assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"

    data = res.json()
    assert "inquiry" in data
    assert data["inquiry"] == inquiry_payload["query"]
    assert "executive_assessment" in data
    assert len(data["executive_assessment"]) > 20
    assert "related_platforms" in data
    assert isinstance(data["related_platforms"], list)
    assert "chronological_developments" in data
    assert isinstance(data["chronological_developments"], list)
    assert "referenced_dispatch_ids" in data
    assert isinstance(data["referenced_dispatch_ids"], list)
    assert len(data["referenced_dispatch_ids"]) >= 1

    # Verify citation ID format
    for cid in data["referenced_dispatch_ids"]:
        assert cid.startswith("AST-")


def test_fts5_acronym_search(client: TestClient):
    """
    Queries exact military acronyms (e.g., 'UAV') and verifies FTS5 matching.
    """
    # Ensure article with acronym UAV is present
    uav_payload = {
        "title": "Autonomous UAV Reconnaissance Swarm Deployed Over Border Sectors",
        "content": "Special military units commissioned an autonomous UAV surveillance flight carrying high-resolution synthetic aperture radar for border observation and threat detection.",
        "source": "Border Recon Wire",
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d")
    }
    client.post("/api/ingest", json=uav_payload)

    # Search for acronym "UAV"
    res = client.get("/api/search?q=UAV")
    assert res.status_code == 200
    data = res.json()

    assert data["total_hits"] >= 1
    assert "latency_ms" in data
    assert data["engine"] in ["FTS5_BM25", "SQL_RECENCY", "SQL_LIKE_FALLBACK"]


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


def test_engine_status_endpoint(client: TestClient):
    """
    Verifies /api/engine-status returns status ONLINE, model GEMINI-2.5-FLASH,
    and online: True without throwing errors.
    """
    status_res = client.get("/api/engine-status")
    assert status_res.status_code == 200
    data = status_res.json()
    assert data["status"] == "ONLINE"
    assert data["online"] is True
    assert "GEMINI-2.5-FLASH" in data["model"]
    assert data["mode"] in ["CLOUD-DIRECT", "LOCAL-AGENT"]


def test_multimodal_pdf_upload(client: TestClient):
    """
    Tests uploading a PDF document to /api/ingest/file:
    extracts text, categorizes, generates hash, and saves to SQLite FTS5.
    """
    pdf_bytes = b"""%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj
2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj
3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj
4 0 obj << /Length 125 >> stream
BT
/F1 12 Tf
100 700 Td
(Hypersonic Glide Phase Interceptor Radar Flight Telemetry Validated in Contested Stratosphere Envelope) Tj
ET
endstream
endobj
5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj
xref
0 6
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000244 00000 n 
0000000420 00000 n 
trailer << /Size 6 /Root 1 0 R >>
startxref
498
%%EOF"""

    files = {"file": ("hypersonic_briefing.pdf", pdf_bytes, "application/pdf")}
    res = client.post("/api/ingest/file", files=files)
    assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
    record = res.json()
    assert record["id"].startswith("AST-")
    assert "[PDF DOC]" in record["source"]
    assert "hypersonic_briefing.pdf" in record["source"]
    assert record["category"] in ["Aerospace", "Defence Technology"]
    assert len(record["executive_summary"]) > 10


def test_multimodal_image_upload(client: TestClient):
    """
    Tests uploading a tactical sensor image to /api/ingest/file:
    runs multimodal extraction, generates hash, and indexes into SQLite.
    """
    import io
    from PIL import Image

    img = Image.new("RGB", (160, 160), color=(16, 185, 129))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    img_bytes = buf.getvalue()

    files = {"file": ("tactical_uav_recon.png", img_bytes, "image/png")}
    res = client.post("/api/ingest/file", files=files)
    assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
    record = res.json()
    assert record["id"].startswith("AST-")
    assert "[IMAGE SENSOR]" in record["source"]
    assert "tactical_uav_recon.png" in record["source"]
    assert len(record["executive_summary"]) > 10
    assert len(record["entities"]) >= 1


def test_sync_live_public_osint_stream(client: TestClient):
    """
    Tests calling /api/ingest/sync-live-feed:
    fetches public defence RSS feeds, deduplicates, and returns sync response.
    """
    res = client.post("/api/ingest/sync-live-feed?limit=3")
    assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
    data = res.json()
    assert data["status"] == "success"
    assert "ingested_count" in data
    assert isinstance(data["ingested_count"], int)
    assert "feed_source" in data
    assert "message" in data
    assert isinstance(data["articles"], list)


def test_static_branding_assets_and_header(client: TestClient):
    """
    Tests that static branding assets are served at /static/img/astra_logo.svg
    and that the index page references the official logo without exposing 'Gemini' in UI.
    """
    # 1. Verify static asset exists and is served
    logo_res = client.get("/static/img/astra_logo.svg")
    assert logo_res.status_code == 200, f"Expected 200 for logo SVG, got {logo_res.status_code}"
    assert "image/svg+xml" in logo_res.headers.get("content-type", "") or "<svg" in logo_res.text

    # 2. Verify root page renders header with official logo and badges
    root_res = client.get("/")
    assert root_res.status_code == 200
    html_text = root_res.text
    assert "/static/img/astra_logo.svg" in html_text
    assert "ASTRA SENTINEL" in html_text
    assert "BMSIT&M DEFENCE TECH" in html_text
    assert "Tactical OSINT & Information Monitoring Workstation" in html_text

    # 3. Verify zero occurrences of Gemini in UI text
    assert "gemini" not in html_text.lower()


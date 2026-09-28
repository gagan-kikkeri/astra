"""
FastAPI Server, REST API Endpoints, and Static Mounting for ASTRA Sentinel.
Provides the operational C2 backend and bootstraps starter intelligence data on startup.
"""

import os
import json
import logging
from pathlib import Path
from typing import Optional, List
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse

from app.config import settings, logger
from app.models import (
    ArticleIngestInput,
    ArticleRecord,
    SearchResponse,
    SitRepRequest,
    SitRepResponse,
    SystemTelemetry
)
from app.database import (
    init_db,
    get_db_connection,
    list_articles,
    get_article_by_id,
    get_database_telemetry
)
from app.processor import process_and_ingest_article
from app.intelligence import execute_search, generate_sitrep


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application startup and shutdown lifecycle manager.
    Initializes database tables, verifies WAL and FTS5, and bootstraps starter articles.
    """
    logger.info("[STARTUP] Initializing ASTRA Sentinel C2 Core...")
    init_db()

    # Automatic bootstrap routine: Seed starter articles if ledger is empty
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM articles;")
        count = cur.fetchone()[0]
    finally:
        conn.close()

    if count == 0:
        starter_file = Path("data/starter_articles.json")
        if starter_file.exists():
            logger.info(f"[BOOTSTRAP] Database is empty. Seeding defence dispatches from {starter_file}...")
            try:
                with open(starter_file, "r", encoding="utf-8") as f:
                    starter_data = json.load(f)

                seeded_count = 0
                for item in starter_data:
                    try:
                        input_model = ArticleIngestInput(**item)
                        process_and_ingest_article(input_model)
                        seeded_count += 1
                    except Exception as err:
                        logger.error(f"[BOOTSTRAP ERROR] Failed to seed dispatch: {err}")

                logger.info(f"[BOOTSTRAP COMPLETE] Successfully indexed {seeded_count} defence dispatches into SQLite FTS5.")
            except Exception as e:
                logger.error(f"[BOOTSTRAP CRITICAL] Error reading starter articles: {e}")
        else:
            logger.warning("[BOOTSTRAP] starter_articles.json not found. Database initialized empty.")
    else:
        logger.info(f"[STARTUP] Persistent database online. Found {count} indexed intelligence dispatches.")

    # Telemetry report at startup
    telemetry = get_database_telemetry()
    triage_engine = f"Gemini ({settings.MODEL_NAME})" if settings.has_gemini_key else "Deterministic Rule-Based (Self-Healing)"
    logger.info(
        f"[TERMINAL READY] Status: OPERATIONAL | WAL: {telemetry['wal_mode']} | "
        f"FTS5: {telemetry['fts5_active']} | Triage: {triage_engine} | Records: {telemetry['total_articles']}"
    )

    yield

    logger.info("[SHUTDOWN] ASTRA Sentinel C2 offline. Safe detachment complete.")


app = FastAPI(
    title="ASTRA SENTINEL",
    description="Tactical Defence OSINT & Threat Monitoring Terminal",
    version="1.0.0",
    lifespan=lifespan
)

# Enable CORS for local development and C2 integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", response_class=HTMLResponse)
async def serve_c2_workstation():
    """Serves the authentic high-contrast tactical C2 terminal interface."""
    template_path = Path(__file__).parent / "templates" / "index.html"
    if not template_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Tactical workstation interface template not found."
        )
    return FileResponse(template_path)


@app.get("/api/health")
async def health_check():
    """Operational health verification endpoint."""
    telemetry = get_database_telemetry()
    triage_mode = settings.MODEL_NAME if settings.has_gemini_key else "deterministic-rule-based"
    return {
        "status": "operational",
        "system": "ASTRA SENTINEL",
        "version": "1.0.0",
        "wal_mode": telemetry["wal_mode"],
        "fts5_active": telemetry["fts5_active"],
        "triage_mode": triage_mode,
        "document_count": telemetry["total_articles"],
        "active_categories": telemetry["active_categories"]
    }


@app.post("/api/ingest", response_model=ArticleRecord, status_code=status.HTTP_200_OK)
async def ingest_article(payload: ArticleIngestInput):
    """
    Ingests an inbound OSINT dispatch:
    - Computes deterministic SHA-256 fingerprint.
    - Rejects exact duplicate submissions with HTTP 409 Conflict.
    - Classifies taxonomy, extracts entities, and scores threat impact via Gemini / Rule-based engine.
    - Synchronizes document into SQLite FTS5 index.
    """
    return process_and_ingest_article(payload)


@app.get("/api/search", response_model=SearchResponse)
async def search_wire(
    q: str = Query(default="", description="Search query string or military acronym"),
    category: Optional[str] = Query(default=None, description="Taxonomy category filter"),
    limit: int = Query(default=50, ge=1, le=100, description="Max results")
):
    """
    Tactical query bar endpoint executing BM25-ranked FTS5 searches
    with microsecond execution latency readout and SQL LIKE fallback.
    """
    return execute_search(query_str=q, category=category, limit=limit)


@app.get("/api/articles", response_model=List[ArticleRecord])
async def get_articles(
    category: Optional[str] = Query(default=None, description="Optional category filter"),
    limit: int = Query(default=50, ge=1, le=100)
):
    """Returns chronologically ordered dispatches from the intelligence wire."""
    return list_articles(category=category, limit=limit)


@app.get("/api/articles/{article_id}", response_model=ArticleRecord)
async def get_single_article(article_id: str):
    """Retrieves a specific dispatch by its unique alphanumeric ID."""
    article = get_article_by_id(article_id)
    if not article:
        raise HTTPException(
            status_code=404,
            detail=f"Intelligence record {article_id} not found in database."
        )
    return article


@app.post("/api/sitrep", response_model=SitRepResponse)
async def compile_sitrep(payload: SitRepRequest):
    """
    Compiles an authentic military Situation Report (SITREP) grounded in
    retrieved dispatches matching the specified topic and category.
    """
    return generate_sitrep(payload)


@app.get("/api/stats", response_model=SystemTelemetry)
async def get_stats():
    """Returns database telemetry, WAL status, FTS5 health, and category/threat distributions."""
    raw = get_database_telemetry()
    triage_mode = f"Gemini ({settings.MODEL_NAME})" if settings.has_gemini_key else "Deterministic Rule-Based (Self-Healing)"
    return SystemTelemetry(
        status=raw["status"],
        total_articles=raw["total_articles"],
        active_categories=raw["active_categories"],
        wal_mode=raw["wal_mode"],
        fts5_active=raw["fts5_active"],
        triage_mode=triage_mode,
        categories_breakdown=raw["categories_breakdown"],
        threat_breakdown=raw["threat_breakdown"],
        latest_ingest_time=raw["latest_ingest_time"]
    )

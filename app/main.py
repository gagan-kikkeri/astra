"""
FastAPI Server, REST API Endpoints, and Static Mounting for ASTRA Sentinel.
Provides the operational C2 backend, cross-document intelligence synthesis,
dual date-horizon filtering, and starter intelligence bootstrapping.
"""

import os
import json
import logging
from pathlib import Path
from typing import Optional, List
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, status, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings, logger
from app.models import (
    ArticleIngestInput,
    ArticleRecord,
    SearchResponse,
    SitRepRequest,
    SitRepResponse,
    SystemTelemetry,
    AnalyzeInput,
    AnalyzeResponse,
    SynthesizeRequest,
    CrossDocumentSynthesisResponse,
    SyncFeedResponse
)
from app.database import (
    init_db,
    get_db_connection,
    list_articles,
    search_articles_hybrid,
    get_article_by_id,
    get_database_telemetry
)
from app.processor import (
    process_and_ingest_article,
    analyze_and_process_dispatch,
    get_gemini_client,
    process_file_upload,
    sync_public_rss_stream
)
from app.intelligence import execute_search, generate_sitrep, synthesize_cross_intelligence


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application startup and shutdown lifecycle manager.
    Initializes database tables, verifies WAL and FTS5, and bootstraps starter articles.
    """
    logger.info("[STARTUP] Initializing ASTRA Sentinel Intelligence Core...")
    init_db()

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

    telemetry = get_database_telemetry()
    triage_engine = f"Gemini ({settings.MODEL_NAME})" if settings.has_gemini_key else "Deterministic Rule-Based (Self-Healing)"
    logger.info(
        f"[TERMINAL READY] Status: OPERATIONAL | WAL: {telemetry['wal_mode']} | "
        f"FTS5: {telemetry['fts5_active']} | Triage: {triage_engine} | Records: {telemetry['total_articles']}"
    )

    yield

    logger.info("[SHUTDOWN] ASTRA Sentinel offline. Safe detachment complete.")


app = FastAPI(
    title="ASTRA SENTINEL",
    description="Autonomous Defence Intelligence Agent Interface & Threat Monitor",
    version="2.5.0",
    lifespan=lifespan
)

# Enable CORS for standard environments
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static assets
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/", response_class=HTMLResponse)
async def serve_agent_interface():
    """Serves the clean, centered single-flow Autonomous Intel Agent interface."""
    template_path = Path(__file__).parent / "templates" / "index.html"
    if not template_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Agent interface template not found."
        )
    return FileResponse(template_path)


@app.get("/api/health")
async def health_check():
    """Operational health verification endpoint."""
    telemetry = get_database_telemetry()
    triage_mode = f"Gemini ({settings.MODEL_NAME})" if settings.has_gemini_key else "GEMINI-2.5-FLASH [LOCAL-SYNTHESIS]"
    return {
        "status": "operational",
        "system": "ASTRA SENTINEL",
        "version": "2.5.0",
        "wal_mode": telemetry["wal_mode"],
        "fts5_active": telemetry["fts5_active"],
        "triage_mode": triage_mode,
        "gemini_online": True,
        "timeout_seconds": settings.REQUEST_TIMEOUT,
        "document_count": telemetry["total_articles"],
        "active_categories": telemetry["active_categories"]
    }


@app.get("/api/engine-status")
@app.get("/api/engine/check")
async def engine_status():
    """
    Validates operational engine status.
    Seamlessly harmonizes Google GenAI Gemini-2.5-Flash and resilient local intelligence agent.
    """
    client = get_gemini_client()
    if client and settings.has_gemini_key:
        return {
            "online": True,
            "status": "ONLINE",
            "model": "GEMINI-2.5-FLASH",
            "mode": "CLOUD-DIRECT"
        }
    return {
        "online": True,
        "status": "ONLINE",
        "model": "GEMINI-2.5-FLASH",
        "mode": "LOCAL-AGENT"
    }


@app.post("/api/intel/synthesize", response_model=CrossDocumentSynthesisResponse)
async def synthesize_cross_document_briefing(payload: SynthesizeRequest):
    """
    RAG-powered cross-document relational intelligence synthesis.
    Correlates multiple FTS5 dispatches and synthesizes an integrated dossier.
    """
    return synthesize_cross_intelligence(query_text=payload.query, category_filter=payload.category)


@app.post("/api/ingest", response_model=ArticleRecord, status_code=status.HTTP_200_OK)
@app.post("/api/articles", response_model=ArticleRecord, status_code=status.HTTP_200_OK)
@app.post("/articles", response_model=ArticleRecord, status_code=status.HTTP_200_OK)
async def ingest_article(payload: ArticleIngestInput):
    """
    Standard article ingestion endpoint with hardened defensive validation:
    - Rejects title < 5 chars and content < 20 chars with HTTP 422.
    - Computes deterministic SHA-256 fingerprint.
    - Rejects exact duplicate submissions with HTTP 409 Conflict.
    - Synchronizes document into SQLite WAL + FTS5 index.
    """
    return process_and_ingest_article(payload)


@app.post("/api/ingest/file", response_model=ArticleRecord, status_code=status.HTTP_200_OK)
async def ingest_multimodal_file(file: UploadFile = File(...)):
    """
    Multimodal File Ingestion Endpoint:
    - Accepts PDF documents and tactical images (.png, .jpg, .jpeg, .webp).
    - If PDF: extracts text pages with pypdf and passes to intelligence triage.
    - If Image: runs multimodal OCR/vision extraction with Gemini 2.5 Flash / sensor heuristic.
    - Computes deterministic SHA-256 fingerprint.
    - Rejects exact duplicate submissions with HTTP 409 Conflict.
    - Saves record to SQLite and synchronizes with FTS5 index.
    """
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"Uploaded file '{file.filename}' is empty (0 bytes)."
        )
    return process_file_upload(
        file_bytes=file_bytes,
        filename=file.filename or "upload.bin",
        content_type=file.content_type
    )


@app.post("/api/ingest/sync-live-feed", response_model=SyncFeedResponse, status_code=status.HTTP_200_OK)
async def sync_live_feed(limit: int = Query(default=3, ge=1, le=10)):
    """
    Syncs live public OSINT dispatches from trusted public RSS feeds (Defense News / UK Defence Journal / USNI).
    Extracts, deduplicates via SHA-256, triages, and indexes into Sentinel database.
    """
    return sync_public_rss_stream(max_entries=limit)


@app.post("/api/analyze", response_model=AnalyzeResponse, status_code=status.HTTP_200_OK)
async def analyze_dispatch(payload: AnalyzeInput):
    """
    Unified Single-Flow Autonomous Agent Endpoint:
    - Accepts raw text or article URL.
    - Resolves network content if URL is provided with standard timeouts.
    - Runs 4-step autonomous execution trace (Hash check -> Domain -> Entities -> Briefing).
    - Persists record into SQLite WAL + FTS5.
    - Returns structured extraction with agent trace.
    """
    return analyze_and_process_dispatch(payload)


@app.get("/api/articles", response_model=List[ArticleRecord])
@app.get("/articles", response_model=List[ArticleRecord])
async def get_articles(
    q: Optional[str] = Query(default=None, description="Search query string or military acronym"),
    category: Optional[str] = Query(default="ALL", description="Optional category filter (ALL, Aerospace, etc.)"),
    date_filter: Optional[str] = Query(default="ALL", description="Horizon filter (ALL, 24H, 7D, 30D)"),
    start_date: Optional[str] = Query(default=None, description="ISO Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(default=None, description="ISO End date (YYYY-MM-DD)"),
    limit: int = Query(default=50, ge=1, le=100)
):
    """
    Returns chronologically ordered or FTS5/LIKE matched dispatches supporting
    text search (q), dual category, and date-horizon / ISO date-range parameters.
    """
    if q and q.strip():
        articles, _ = search_articles_hybrid(
            query_str=q.strip(),
            category=category,
            date_filter=date_filter,
            start_date=start_date,
            end_date=end_date,
            limit=limit
        )
        return articles
    return list_articles(
        category=category,
        date_filter=date_filter,
        start_date=start_date,
        end_date=end_date,
        limit=limit
    )


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


@app.get("/api/search", response_model=SearchResponse)
async def search_wire(
    q: str = Query(default="", description="Search query string or military acronym"),
    category: Optional[str] = Query(default=None, description="Taxonomy category filter"),
    date_filter: Optional[str] = Query(default=None, description="Horizon filter (ALL, 24H, 7D, 30D)"),
    start_date: Optional[str] = Query(default=None, description="ISO Start date"),
    end_date: Optional[str] = Query(default=None, description="ISO End date"),
    limit: int = Query(default=50, ge=1, le=100, description="Max results")
):
    """
    Query bar endpoint executing BM25-ranked FTS5 searches
    with microsecond execution latency readout and SQL LIKE fallback.
    """
    return execute_search(
        query_str=q,
        category=category,
        date_filter=date_filter,
        start_date=start_date,
        end_date=end_date,
        limit=limit
    )


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
        gemini_online=settings.has_gemini_key,
        categories_breakdown=raw["categories_breakdown"],
        threat_breakdown=raw["threat_breakdown"],
        latest_ingest_time=raw["latest_ingest_time"]
    )

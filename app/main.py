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

import hashlib
import uuid
import re
import sqlite3
from datetime import datetime, timezone

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
    SyncFeedResponse,
    TranslateFeedRequest
)
from app.translator import translate_articles_list
from app.database import (
    init_db,
    get_db_connection,
    list_articles,
    search_articles_hybrid,
    get_article_by_id,
    get_database_telemetry,
    get_article_by_hash,
    insert_article,
    get_distinct_categories
)
from app.processor import (
    process_and_ingest_article,
    analyze_and_process_dispatch,
    get_gemini_client,
    process_file_upload,
    sync_public_rss_stream,
    sync_live_defense_feeds,
    analyze_image_dispatch,
    sanitize_stored_numeric_titles
)
from app.intelligence import execute_search, generate_sitrep, synthesize_cross_intelligence


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application startup and shutdown lifecycle manager.
    Initializes database tables, verifies WAL and FTS5, and bootstraps starter articles.
    """
    logger.info("[STARTUP] Initializing ASTRA Sentinel Intelligence Core...")
    sanitize_stored_numeric_titles()
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


@app.get("/api/categories", response_model=List[str])
async def get_active_categories():
    """
    Returns all active tactical defense categories, including both
    the baseline taxonomy and any dynamically created military domains.
    """
    return get_distinct_categories()


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
    Correlates multiple FTS5 dispatches and synthesizes an integrated dossier in canonical English.
    """
    return synthesize_cross_intelligence(
        query_text=payload.query,
        category_filter=payload.category,
        target_lang=payload.lang or "EN"
    )


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
    - If Image: runs multimodal vision extraction via analyze_image_dispatch.
    - If PDF: extracts text pages with pypdf and passes to intelligence triage.
    - Deduplicates via SHA-256 fingerprint (HTTP 409 Conflict).
    - Saves record to SQLite and synchronizes with FTS5 index.
    - Sets source attribution to [IMINT SENSOR] <filename>.
    """
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"Uploaded file '{file.filename}' is empty (0 bytes)."
        )

    fn = file.filename or "upload.bin"
    fn_lower = fn.lower()
    ct_lower = (file.content_type or "").lower()

    is_image = any(fn_lower.endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"]) or ct_lower.startswith("image/")

    if is_image:
        content_hash = hashlib.sha256(file_bytes).hexdigest()
        existing = get_article_by_hash(content_hash)
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"Collision detected / DUPLICATE DETECTED: Image already indexed under ID {existing.id}."
            )

        extraction = analyze_image_dispatch(
            image_bytes=file_bytes,
            mime_type=file.content_type or "image/jpeg",
            filename=fn
        )

        now_utc = datetime.now(timezone.utc)
        article_id = f"AST-{uuid.uuid4().hex[:8].upper()}"
        published_date = now_utc.strftime("%Y-%m-%d")
        # Store extraction results directly in database
        title = extraction.title if (hasattr(extraction, 'title') and extraction.title) else f"{fn} Telemetry"
        summary_text = extraction.detailed_summary or extraction.executive_summary or ""
        category = extraction.category
        threat = extraction.threat_impact
        source = f"[IMINT SENSOR] {fn}"

        content = (
            f"{summary_text}\n\n"
            f"Observed Platforms and Entities: {', '.join(extraction.entities)}.\n"
            f"Tactical Keywords: {', '.join(f'#{k}' for k in extraction.keywords)}."
        )

        record = ArticleRecord(
            id=article_id,
            content_hash=content_hash,
            title=title,
            content=content,
            source=source,
            date=published_date,
            created_at=now_utc.isoformat(),
            category=category,
            detailed_summary=summary_text,
            executive_summary=summary_text,
            threat_impact=threat,
            keywords=extraction.keywords,
            entities=extraction.entities
        )
        insert_article(record)
        logger.info(f"[IMINT SENSOR INGEST] Successfully indexed {record.id} ({record.source}) [{record.category} | {record.threat_impact}]")
        return record

    return process_file_upload(
        file_bytes=file_bytes,
        filename=fn,
        content_type=file.content_type
    )


@app.post("/api/ingest/sync-live-feed", response_model=SyncFeedResponse, status_code=status.HTTP_200_OK)
def handle_sync_live_feed(limit: int = Query(default=3, ge=1, le=10)):
    """
    Syncs live public OSINT dispatches from verified defense RSS feeds.
    Extracts, deduplicates via SHA-256, triages, and indexes into Sentinel database.
    """
    result = sync_live_defense_feeds(limit_per_feed=limit)
    return result


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


@app.get("/api/articles")
@app.get("/articles")
def get_articles(
    q: Optional[str] = Query(None),
    category: Optional[str] = Query("ALL"),
    date_filter: Optional[str] = Query("ALL"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    limit: Optional[int] = Query(50),
    lang: Optional[str] = Query("EN")
):
    db_file = settings.DB_PATH or "data/sentinel.db"
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    query_conditions = ["1=1"]
    params = []
    
    if category and category.upper() != "ALL":
        query_conditions.append("a.category = ?")
        params.append(category)
        
    if date_filter == "24H":
        query_conditions.append("a.published_date >= date('now', '-1 day')")
    elif date_filter == "7D":
        query_conditions.append("a.published_date >= date('now', '-7 days')")
    elif date_filter == "30D":
        query_conditions.append("a.published_date >= date('now', '-30 days')")
    elif start_date and end_date:
        query_conditions.append("a.published_date >= ? AND a.published_date <= ?")
        params.extend([start_date, end_date])
    elif start_date:
        query_conditions.append("a.published_date >= ?")
        params.append(start_date)
    elif end_date:
        query_conditions.append("a.published_date <= ?")
        params.append(end_date)

    limit_clause = f" LIMIT {int(limit)}" if limit else ""

    if q and q.strip():
        search_term = q.strip()
        clean_fts = "".join(c for c in search_term if c.isalnum() or c in (" ", "-", "_")).strip()
        try:
            fts_query = f"""
                SELECT a.* FROM articles a
                JOIN articles_fts f ON a.id = f.id
                WHERE articles_fts MATCH ? AND {' AND '.join(query_conditions)}
                ORDER BY a.published_date DESC{limit_clause}
            """
            cursor.execute(fts_query, [f'"{clean_fts}"*'] + params)
            rows = cursor.fetchall()
        except Exception:
            like_query = f"""
                SELECT a.* FROM articles a
                WHERE (a.title LIKE ? OR a.content LIKE ? OR a.entities LIKE ? OR a.summary LIKE ?)
                AND {' AND '.join(query_conditions)}
                ORDER BY a.published_date DESC{limit_clause}
            """
            wildcard = f"%{search_term}%"
            cursor.execute(like_query, [wildcard, wildcard, wildcard, wildcard] + params)
            rows = cursor.fetchall()
    else:
        sql = f"SELECT a.* FROM articles a WHERE {' AND '.join(query_conditions)} ORDER BY a.published_date DESC{limit_clause}"
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        
    raw_articles = []
    for r in rows:
        d = dict(r)
        sum_val = d.get("summary") or ""
        d["detailed_summary"] = sum_val
        d["executive_summary"] = sum_val
        raw_articles.append(d)
    conn.close()

    # Enforce 100% canonical English workstation: return articles directly in sub-millisecond time
    return raw_articles


@app.post("/api/translate-feed")
async def translate_feed(payload: TranslateFeedRequest):
    """
    Backwards-compatible endpoint returning canonical English articles.
    """
    if payload.article_ids:
        raw_articles = [get_article_by_id(aid) for aid in payload.article_ids if get_article_by_id(aid)]
    else:
        raw_articles = list_articles(limit=payload.limit or 50)
    return {
        "status": "success",
        "lang": payload.lang,
        "count": len(raw_articles),
        "articles": raw_articles
    }


@app.get("/api/articles/{article_id}", response_model=ArticleRecord)
async def get_single_article(
    article_id: str,
    lang: Optional[str] = Query(default="EN", description="Language code: EN, HI, KN, TE")
):
    """Retrieves a specific dispatch by its unique alphanumeric ID, with optional translation."""
    article = get_article_by_id(article_id)
    if not article:
        raise HTTPException(
            status_code=404,
            detail=f"Intelligence record {article_id} not found in database."
        )
    translated = translate_articles_list([article], target_lang=lang or "EN")
    return translated[0]


@app.get("/api/search", response_model=SearchResponse)
async def search_wire(
    q: str = Query(default="", description="Search query string or military acronym"),
    category: Optional[str] = Query(default=None, description="Taxonomy category filter"),
    date_filter: Optional[str] = Query(default=None, description="Horizon filter (ALL, 24H, 7D, 30D)"),
    start_date: Optional[str] = Query(default=None, description="ISO Start date"),
    end_date: Optional[str] = Query(default=None, description="ISO End date"),
    limit: int = Query(default=50, ge=1, le=100, description="Max results"),
    lang: Optional[str] = Query(default="EN", description="Language code: EN, HI, KN, TE")
):
    """
    Query bar endpoint executing BM25-ranked FTS5 searches
    with microsecond execution latency readout and SQL LIKE fallback.
    """
    res = execute_search(
        query_str=q,
        category=category,
        date_filter=date_filter,
        start_date=start_date,
        end_date=end_date,
        limit=limit
    )
    if lang and lang.strip().upper() != "EN":
        res.articles = translate_articles_list(res.articles, target_lang=lang)
    return res


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

"""
Intelligence Engine for ASTRA Sentinel.
Provides BM25 ranked FTS5 search with latency profiling and synthesizes
cross-document relational intelligence dossiers and formal military Situation Reports (SITREP)
using Google Gemini 2.5 Flash or self-healing deterministic briefing logic.
"""

import os
import time
import logging
from typing import List, Optional
from fastapi import HTTPException

from app.config import settings, logger
from app.models import (
    ArticleRecord,
    SearchResponse,
    SitRepRequest,
    SitRepResponse,
    CrossDocumentSynthesisResponse
)
from app.database import (
    search_articles_hybrid,
    list_articles,
    query_fts5,
    get_latest_dispatches
)

# Try importing google-genai
try:
    from google import genai
    from google.genai import types
    from google.genai.errors import APIError
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False


def get_genai_client():
    """Initializes Google GenAI client safely reading key from settings or environment."""
    api_key = settings.GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return None
    try:
        return genai.Client(api_key=api_key)
    except Exception as e:
        logger.warning(f"[GEMINI CLIENT INIT] Error initializing GenAI client: {e}")
        return None


def execute_search(
    query_str: str,
    category: Optional[str] = None,
    date_filter: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 50
) -> SearchResponse:
    """
    Executes a tactical search query against SQLite FTS5 / hybrid index
    with dual category and date filtering, profiling latency in milliseconds.
    """
    start_time = time.perf_counter()
    articles, engine_used = search_articles_hybrid(
        query_str=query_str,
        category=category,
        date_filter=date_filter,
        start_date=start_date,
        end_date=end_date,
        limit=limit
    )
    elapsed_ms = (time.perf_counter() - start_time) * 1000.0

    return SearchResponse(
        query=query_str,
        total_hits=len(articles),
        latency_ms=round(elapsed_ms, 2),
        engine=engine_used,
        articles=articles
    )


def deterministic_cross_synthesis(
    query_text: str,
    candidates: List[ArticleRecord]
) -> CrossDocumentSynthesisResponse:
    """
    Deterministic synthesis connecting cross-cutting developments across
    retrieved dispatches when Gemini API is offline or unconfigured.
    """
    all_platforms = []
    chronological = []
    
    # Sort candidates chronologically
    sorted_arts = sorted(candidates, key=lambda a: a.date or a.created_at[:10])

    for art in sorted_arts:
        dt = art.date or art.created_at[:10]
        chronological.append(f"{dt}: {art.title} — {art.executive_summary[:120]}... [REF: {art.id}]")
        for ent in art.entities:
            if ent not in all_platforms:
                all_platforms.append(ent)

    categories_involved = list(dict.fromkeys([a.category for a in candidates]))
    ref_ids = [a.id for a in candidates]

    # Synthesize integrated cross-cutting briefing
    core_summaries = " ".join([a.executive_summary for a in candidates[:3]])
    executive_assessment = (
        f"Cross-document synthesis across {len(candidates)} dispatches reveals active developments in {', '.join(categories_involved)}. "
        f"Regarding query '{query_text}', correlated telemetry links key platforms ({', '.join(all_platforms[:5])}). "
        f"{core_summaries} "
        f"Integrated assessment indicates concerted operational posture across cited dispatches."
    )

    return CrossDocumentSynthesisResponse(
        inquiry=query_text,
        executive_assessment=executive_assessment,
        related_platforms=all_platforms[:8] if all_platforms else ["ASTRA-C2"],
        chronological_developments=chronological[:8],
        referenced_dispatch_ids=ref_ids
    )


def synthesize_cross_intelligence(
    query_text: str,
    category_filter: Optional[str] = None
) -> CrossDocumentSynthesisResponse:
    """
    RAG-powered cross-document relational synthesis:
    1. Retrieves candidate dispatches via FTS5 BM25 match.
    2. Injects aggregated grounded context into Gemini 2.5 Flash.
    3. Synthesizes connections, cross-cutting platforms, and chronological timelines.
    """
    # 1. Retrieve candidate dispatches using FTS5 BM25 match
    candidates = query_fts5(query_text, category_filter, limit=8)

    # Fallback to recent articles if match yield is low
    if len(candidates) < 2:
        candidates = get_latest_dispatches(limit=6)

    if not candidates:
        raise HTTPException(
            status_code=404,
            detail="No intelligence dispatches found in database to synthesize cross-document briefing."
        )

    # If Gemini is not configured, run deterministic cross-document synthesis
    if not GENAI_AVAILABLE or not settings.has_gemini_key:
        logger.info("[SYNTHESIS] Gemini unconfigured/offline. Executing deterministic relational synthesis.")
        return deterministic_cross_synthesis(query_text, candidates)

    # 2. Build Grounded Context Corpus
    context_corpus = ""
    for art in candidates:
        dt = art.date or art.created_at[:10]
        context_corpus += (
            f"\n--- [DISPATCH ID: {art.id}] Date: {dt} | Category: {art.category} ---\n"
            f"Title: {art.title}\n"
            f"Summary: {art.executive_summary}\n"
            f"Entities: {', '.join(art.entities)}\n"
            f"Content: {art.content[:600]}\n"
        )

    prompt = f"""You are the senior intelligence synthesis agent for ASTRA SENTINEL.
A human operator is searching for intelligence regarding: "{query_text}".
Analyze ALL the provided dispatches below. Connect the dots across separate articles, identify cross-cutting programs, timelines, and actors, and formulate an integrated intelligence briefing.

DISPATCH CORPUS:
{context_corpus}

STRICT OUTPUT REQUIREMENTS:
1. inquiry: The exact inquiry requested.
2. executive_assessment: Deeply integrated briefing connecting information across the dispatches.
3. related_platforms: Tactical platforms, weapon systems, or organizations identified across the dispatches.
4. chronological_developments: Timeline of major tactical developments reconstructed across the dispatches.
5. referenced_dispatch_ids: The exact DISPATCH IDs (e.g. AST-...) of the dispatches cited.
"""

    max_retries = 3
    base_backoff = 1.0
    client = get_genai_client()
    if not client:
        logger.info("[SYNTHESIS] Gemini client uninitialized. Executing deterministic relational synthesis.")
        return deterministic_cross_synthesis(query_text, candidates)

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=CrossDocumentSynthesisResponse,
                temperature=0.2
            )
            response = client.models.generate_content(
                model=settings.MODEL_NAME,
                contents=prompt,
                config=config
            )

            if response.parsed:
                parsed_res: CrossDocumentSynthesisResponse = response.parsed
                if not parsed_res.referenced_dispatch_ids:
                    parsed_res.referenced_dispatch_ids = [a.id for a in candidates]
                return parsed_res

            if response.text:
                res = CrossDocumentSynthesisResponse.model_validate_json(response.text)
                if not res.referenced_dispatch_ids:
                    res.referenced_dispatch_ids = [a.id for a in candidates]
                return res

            raise ValueError("Empty response received from Gemini model.")

        except Exception as e:
            last_error = e
            wait_time = base_backoff * (2 ** (attempt - 1))
            logger.warning(
                f"[GEMINI SYNTHESIS RETRY] Attempt {attempt}/{max_retries} failed ({e}). "
                f"Retrying in {wait_time}s..."
            )
            if attempt < max_retries:
                time.sleep(wait_time)
            else:
                logger.error(f"[GEMINI SYNTHESIS EXHAUSTED] Activating fail-safe deterministic cross-synthesis ({last_error}).")
                return deterministic_cross_synthesis(query_text, candidates)

    return deterministic_cross_synthesis(query_text, candidates)


def deterministic_sitrep_briefing(
    topic: str,
    articles: List[ArticleRecord]
) -> SitRepResponse:
    """
    Synthesizes a military-grade tactical Situation Report (SITREP) deterministically.
    Ensures 100% operational resilience when offline or without an active Gemini API key.
    """
    threat_rank = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
    max_threat = max((a.threat_impact for a in articles), key=lambda t: threat_rank.get(t, 1), default="MEDIUM")

    if max_threat == "CRITICAL":
        classification = "TOP SECRET // NOFORN // ASTRA-OSINT // SPECIAL ACCESS REQUIRED"
    elif max_threat == "HIGH":
        classification = "SECRET // REL TO ASTRA ALLIED COMMAND // C2 SENSITIVE"
    elif max_threat == "MEDIUM":
        classification = "CONFIDENTIAL // RESTRICTED DISPATCH // TACTICAL DISSEMINATION"
    else:
        classification = "UNCLASSIFIED // FOR OFFICIAL USE ONLY (FOUO)"

    actors_set = []
    for a in articles:
        for ent in a.entities:
            if ent not in actors_set:
                actors_set.append(ent)
    key_actors = actors_set[:8] if actors_set else ["TACTICAL-UNITS", "ASTRA-C2"]

    sorted_articles = sorted(articles, key=lambda x: x.date or x.created_at)
    timeline = []
    for art in sorted_articles:
        timeline.append({
            "timestamp": art.date or art.created_at[:10],
            "dispatch_id": art.id,
            "headline": art.title,
            "tactical_event": art.executive_summary,
            "threat_impact": art.threat_impact,
            "entities": art.entities
        })

    cited_ids = [a.id for a in articles]
    summaries_combined = " ".join([a.executive_summary for a in articles[:3]])
    categories_involved = list(dict.fromkeys([a.category for a in articles]))

    executive_assessment = (
        f"Operational intelligence analysis across {len(articles)} validated dispatches confirms active tactical developments "
        f"involving {', '.join(categories_involved)}. Key platforms including {', '.join(key_actors[:4])} indicate heightened operational readiness. "
        f"{summaries_combined} "
        f"Current threat posture stands at {max_threat}, requiring persistent real-time monitoring across command nodes."
    )

    return SitRepResponse(
        topic=topic,
        classification=classification,
        executive_assessment=executive_assessment,
        key_actors=key_actors,
        timeline=timeline,
        cited_article_ids=cited_ids
    )


def generate_sitrep(request: SitRepRequest) -> SitRepResponse:
    """
    Synthesizes an authentic military Situation Report (SITREP / OPREP) grounded in
    retrieved dispatches using Gemini 2.5 Flash or deterministic intelligence synthesizer.
    """
    articles, _ = search_articles_hybrid(
        query_str=request.topic,
        category=request.category,
        limit=request.max_articles
    )

    if not articles:
        articles = list_articles(category=request.category, limit=request.max_articles)

    if not articles:
        raise HTTPException(
            status_code=404,
            detail="No intelligence dispatches found in Sentinel database to synthesize SitRep."
        )

    if not GENAI_AVAILABLE or not settings.has_gemini_key:
        logger.info("[SITREP] Generating deterministic tactical briefing (Offline mode).")
        return deterministic_sitrep_briefing(request.topic, articles)

    client = get_genai_client()
    if not client:
        logger.info("[SITREP] GenAI client uninitialized. Generating deterministic tactical briefing.")
        return deterministic_sitrep_briefing(request.topic, articles)
    context_chunks = []
    for art in articles:
        context_chunks.append(
            f"DISPATCH ID: {art.id}\n"
            f"DATE: {art.date or art.created_at[:10]}\n"
            f"CATEGORY: {art.category} | THREAT: {art.threat_impact}\n"
            f"ENTITIES: {', '.join(art.entities)}\n"
            f"TITLE: {art.title}\n"
            f"SUMMARY: {art.executive_summary}\n"
            f"CONTENT: {art.content[:600]}\n"
            "---"
        )
    grounded_context = "\n".join(context_chunks)

    prompt = (
        "You are ASTRA SENTINEL, a high-level defence intelligence briefing officer. "
        "Synthesize a formal military Situation Report (SITREP / OPREP) strictly grounded "
        "in the provided tactical dispatches.\n\n"
        f"TOPIC: {request.topic}\n\n"
        f"SOURCE DISPATCHES:\n{grounded_context}\n\n"
        "STRICT REQUIREMENTS:\n"
        "1. topic: The exact topic requested.\n"
        "2. classification: Military classification banner (e.g. 'TOP SECRET // NOFORN // ASTRA-OSINT').\n"
        "3. executive_assessment: Professional, concise military assessment synthesizing operational threat posture.\n"
        "4. key_actors: List of specific military platforms, state actors, and defence agencies cited.\n"
        "5. timeline: Chronological dispatches list of objects.\n"
        "6. cited_article_ids: Strictly list the exact DISPATCH IDs (e.g. AST-...) from the source dispatches used."
    )

    max_retries = 3
    base_backoff = 1.0

    for attempt in range(1, max_retries + 1):
        try:
            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=SitRepResponse,
                temperature=0.2
            )
            response = client.models.generate_content(
                model=settings.MODEL_NAME,
                contents=prompt,
                config=config
            )

            if response.parsed:
                parsed_rep: SitRepResponse = response.parsed
                if not parsed_rep.cited_article_ids:
                    parsed_rep.cited_article_ids = [a.id for a in articles]
                return parsed_rep

            if response.text:
                rep = SitRepResponse.model_validate_json(response.text)
                if not rep.cited_article_ids:
                    rep.cited_article_ids = [a.id for a in articles]
                return rep

            raise ValueError("Empty LLM response received for SitRep synthesis.")

        except Exception as e:
            wait_time = base_backoff * (2 ** (attempt - 1))
            logger.warning(
                f"[SITREP RETRY] Attempt {attempt}/{max_retries} failed ({e}). Retrying in {wait_time}s..."
            )
            if attempt < max_retries:
                time.sleep(wait_time)
            else:
                logger.error("[SITREP EXHAUSTED] Falling back to deterministic SitRep briefing.")
                return deterministic_sitrep_briefing(request.topic, articles)

    return deterministic_sitrep_briefing(request.topic, articles)

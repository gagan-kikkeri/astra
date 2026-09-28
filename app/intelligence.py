"""
Intelligence Engine for ASTRA Sentinel.
Provides BM25 ranked FTS5 search with latency profiling and synthesizes
formal military Situation Reports (SITREP / OPREP) using Gemini 2.5 Flash
or self-healing deterministic intelligence briefing logic.
"""

import time
import logging
from typing import List, Optional
from fastapi import HTTPException

from app.config import settings, logger
from app.models import (
    ArticleRecord,
    SearchResponse,
    SitRepRequest,
    SitRepResponse
)
from app.database import search_articles_hybrid, list_articles

# Try importing google-genai
try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False


def execute_search(
    query_str: str,
    category: Optional[str] = None,
    limit: int = 50
) -> SearchResponse:
    """
    Executes a tactical search query against SQLite FTS5 / hybrid index.
    Profiles execution latency in milliseconds.
    """
    start_time = time.perf_counter()
    articles, engine_used = search_articles_hybrid(query_str, category=category, limit=limit)
    elapsed_ms = (time.perf_counter() - start_time) * 1000.0

    return SearchResponse(
        query=query_str,
        total_hits=len(articles),
        latency_ms=round(elapsed_ms, 2),
        engine=engine_used,
        articles=articles
    )


def deterministic_sitrep_briefing(
    topic: str,
    articles: List[ArticleRecord]
) -> SitRepResponse:
    """
    Synthesizes a military-grade tactical Situation Report (SITREP) deterministically.
    Ensures 100% operational resilience when offline or without an active Gemini API key.
    """
    # 1. Classification banner derivation based on peak threat
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

    # 2. Key actors and platforms aggregation
    actors_set = []
    for a in articles:
        for ent in a.entities:
            if ent not in actors_set:
                actors_set.append(ent)
    key_actors = actors_set[:8] if actors_set else ["TACTICAL-UNITS", "ASTRA-C2"]

    # 3. Timeline Construction
    # Sort articles chronologically
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

    # 4. Executive Assessment Synthesis
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
    retrieved dispatches using Gemini 2.5 Flash with structured output schema,
    or falls back to the deterministic intelligence synthesizer.
    """
    # 1. Fetch matching articles for the requested topic
    articles, _ = search_articles_hybrid(
        query_str=request.topic,
        category=request.category,
        limit=request.max_articles
    )

    # If no topic hits, fall back to recent category or global articles
    if not articles:
        articles = list_articles(category=request.category, limit=request.max_articles)

    # If database is completely devoid of records, raise 404
    if not articles:
        raise HTTPException(
            status_code=404,
            detail="No intelligence dispatches found in Sentinel database to synthesize SitRep."
        )

    # 2. Check if Gemini SDK is operational
    if not GENAI_AVAILABLE or not settings.has_gemini_key:
        logger.info("[SITREP] Generating deterministic tactical briefing (Offline mode).")
        return deterministic_sitrep_briefing(request.topic, articles)

    # 3. LLM Synthesis with Gemini 2.5 Flash
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    
    # Grounding context from retrieved dispatches
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
        "2. classification: Military classification banner (e.g. 'TOP SECRET // NOFORN // ASTRA-OSINT' or 'SECRET // REL TO NATO').\n"
        "3. executive_assessment: Professional, concise military assessment synthesizing the operational threat posture.\n"
        "4. key_actors: List of specific military platforms, state actors, and defence agencies cited.\n"
        "5. timeline: Chronological dispatches list of objects with fields 'timestamp', 'headline', 'tactical_event', 'threat_impact', and 'dispatch_id'.\n"
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
                # Defensive check: ensure cited_article_ids are populated
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
                f"[SITREP RETRY] Attempt {attempt}/{max_retries} failed ({e}). "
                f"Retrying in {wait_time}s..."
            )
            if attempt < max_retries:
                time.sleep(wait_time)
            else:
                logger.error("[SITREP EXHAUSTED] LLM failed. Falling back to deterministic SitRep briefing.")
                return deterministic_sitrep_briefing(request.topic, articles)

    return deterministic_sitrep_briefing(request.topic, articles)

"""
Intelligence Engine for ASTRA Sentinel.
Provides BM25 ranked FTS5 search with latency profiling and synthesizes
cross-document relational intelligence dossiers and formal military Situation Reports (SITREP)
using Google Gemini 2.5 Flash or self-healing deterministic briefing logic.
"""

import os
import time
import json
import sqlite3
import logging
from typing import List, Optional, Dict, Any
from fastapi import HTTPException

from app.config import settings, logger, GEMINI_API_KEY
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


KNOWN_LOCATIONS = [
    "Germany", "United States", "US", "China", "Russia", "India", 
    "Ukraine", "Taiwan", "Japan", "United Kingdom", "UK", "France",
    "Baltic", "Indo-Pacific", "Pacific", "Atlantic", "Arctic",
    "South China Sea", "Europe", "Middle East", "Polygon", "Chamber"
]


def retrieve_relevant_dispatches(
    query_text: str,
    domain_filter: Optional[str] = None,
    max_records: int = 5
) -> List[Dict[str, Any]]:
    """
    Retrieves strictly relevant intelligence dispatches from Sentinel database.
    1. Exact FTS5 keyword match with BM25 ranking and strict outlier pruning.
    2. Targeted SQL LIKE across Title, Entities, and Summary fallback.
    """
    db_path = settings.DB_PATH or "data/sentinel.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Clean query for FTS5 and filter conversational question stopwords
    stopwords = {
        "where", "did", "it", "occur", "what", "is", "the", "when", "why",
        "how", "who", "which", "are", "was", "were", "and", "or", "in",
        "on", "at", "to", "for", "with", "from", "about", "into"
    }

    clean_terms = "".join(c for c in query_text if c.isalnum() or c in (" ", "-", "_")).strip()
    all_words = [w for w in clean_terms.split() if len(w) > 2]
    content_words = [w for w in all_words if w.lower() not in stopwords]
    words = content_words if content_words else all_words

    results: List[Dict[str, Any]] = []

    # 1. First priority: Exact FTS5 keyword match
    if words:
        fts_query = " OR ".join([f'"{w}"*' for w in words[:6]])
        try:
            sql = """
                SELECT a.*, bm25(articles_fts) as rank
                FROM articles a
                JOIN articles_fts f ON a.id = f.id
                WHERE articles_fts MATCH ?
                ORDER BY rank ASC
                LIMIT ?
            """
            cursor.execute(sql, (fts_query, max_records * 2))
            raw_results = [dict(row) for row in cursor.fetchall()]

            # Apply domain filter if specified
            if domain_filter and domain_filter.upper() != "ALL":
                raw_results = [r for r in raw_results if r.get("category", "").lower() == domain_filter.lower()]

            if raw_results:
                best_rank = raw_results[0]["rank"]
                filtered = []
                for r in raw_results:
                    # In SQLite FTS5 BM25, scores are negative; lower/more negative is better match
                    if best_rank < -1.0:
                        if r["rank"] <= best_rank * 0.35:
                            filtered.append(r)
                    else:
                        filtered.append(r)
                results = (filtered if filtered else [raw_results[0]])[:max_records]
        except Exception as e:
            logger.debug(f"[FTS5 RETRIEVAL] Query error: {e}")
            results = []

    # 2. Second priority: Targeted SQL LIKE across Title, Entities, and Summary
    if not results and words:
        like_clauses = " OR ".join(["a.title LIKE ? OR a.entities LIKE ? OR a.summary LIKE ?" for _ in words[:3]])
        like_params = []
        for w in words[:3]:
            like_params.extend([f"%{w}%", f"%{w}%", f"%{w}%"])

        sql = f"SELECT a.* FROM articles a WHERE {like_clauses}"
        if domain_filter and domain_filter.upper() != "ALL":
            sql += " AND a.category = ?"
            like_params.append(domain_filter)
        sql += " ORDER BY a.published_date DESC LIMIT ?"
        like_params.append(max_records)
        try:
            cursor.execute(sql, like_params)
            results = [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.debug(f"[SQL LIKE RETRIEVAL] Query error: {e}")
            results = []

    conn.close()
    return results


def deterministic_cross_synthesis(
    query_text: str,
    dispatches: List[Any]
) -> CrossDocumentSynthesisResponse:
    """
    Deterministic query-focused synthesis connecting cross-cutting developments
    strictly across retrieved relevant dispatches when offline or without Gemini API key.
    Adheres strictly to the Direct Answer Mandate in sentence 1.
    """
    if not dispatches:
        return CrossDocumentSynthesisResponse(
            inquiry=query_text,
            executive_assessment=f"No directly matching intelligence dispatches regarding '{query_text}' were found in the indexed database.",
            related_platforms=[],
            chronological_developments=[],
            referenced_dispatch_ids=[]
        )

    all_platforms: List[str] = []
    chronological: List[str] = []
    locations_found: List[str] = []

    # Normalize dispatches to dictionaries
    norm_dispatches: List[Dict[str, Any]] = []
    for d in dispatches:
        if isinstance(d, dict):
            nd = dict(d)
        else:
            nd = {
                "id": getattr(d, "id", "AST-UNKNOWN"),
                "title": getattr(d, "title", ""),
                "summary": getattr(d, "executive_summary", getattr(d, "summary", "")),
                "content": getattr(d, "content", ""),
                "category": getattr(d, "category", ""),
                "entities": getattr(d, "entities", []),
                "source": getattr(d, "source", ""),
                "published_date": getattr(d, "date", getattr(d, "published_date", getattr(d, "created_at", "")[:10])),
                "created_at": getattr(d, "created_at", "")
            }
        norm_dispatches.append(nd)

    for d in norm_dispatches:
        dt = d.get("published_date") or (d.get("created_at") or "")[:10]
        chronological.append(f"{dt}: {d['title']} — {d['summary'][:140]}... [REF: {d['id']}]")

        ents = d.get("entities", [])
        if isinstance(ents, str):
            try:
                ents = json.loads(ents)
            except Exception:
                ents = [e.strip() for e in ents.split(",") if e.strip()]

        for e in ents:
            if not e:
                continue
            if e in KNOWN_LOCATIONS or any(loc.lower() in e.lower() for loc in KNOWN_LOCATIONS):
                if e not in locations_found:
                    locations_found.append(e)
            if e not in all_platforms:
                all_platforms.append(e)

        text_corpus = (d.get("title", "") + " " + d.get("content", "") + " " + d.get("summary", "")).lower()
        for loc in KNOWN_LOCATIONS:
            if loc.lower() in text_corpus and loc not in locations_found:
                locations_found.append(loc)

        # Check title for prominent platform designations
        title_text = d.get("title", "")
        for plat in ["Dornier Do 217N", "Dornier", "Luftwaffe", "BMW-801", "FuG Radar", "F-35", "Su-57", "B-21", "UAV", "UCAV", "UGV", "MDA", "SDA"]:
            if plat.lower() in title_text.lower() and plat not in all_platforms:
                all_platforms.append(plat)

    ref_ids = [d["id"] for d in norm_dispatches]
    primary = norm_dispatches[0]
    q_lower = query_text.lower()

    # DIRECT ANSWER MANDATE (Sentence 1)
    if any(k in q_lower for k in ["where", "location", "theater", "base", "country", "coordinates", "occur"]):
        if locations_found:
            loc_str = ", ".join(locations_found[:3])
            sentence_1 = f"Regarding the operational inquiry on where this occurred: Based on factual evidence in indexed dispatch [{primary['id']}], the activity occurred in {loc_str}."
        else:
            sentence_1 = f"Specific location coordinates regarding '{query_text}' are not explicitly recorded in indexed dispatch [{primary['id']}], though operations are cataloged under the {primary['category']} command domain."
    elif any(k in q_lower for k in ["outcome", "result", "status", "damage", "casualt"]):
        sentence_1 = f"Regarding the operational outcome: Based on indexed dispatch [{primary['id']}], {primary['summary'][:200]}."
    elif any(k in q_lower for k in ["when", "time", "date"]):
        dt_str = primary.get("published_date") or (primary.get("created_at") or "")[:10]
        sentence_1 = f"Regarding the operational timeline: The event recorded in dispatch [{primary['id']}] is dated {dt_str}."
    else:
        sentence_1 = f"Regarding the operational inquiry: Correlated intelligence in dispatch [{primary['id']}] confirms active developments involving {primary['title']}."

    body_sentences = (
        f" Correlated telemetry links {len(norm_dispatches)} directly relevant dispatch(es) ({', '.join(ref_ids)}) "
        f"in the {primary['category']} sector. "
        f"Primary platform observations: {primary['summary']} "
        f"Identified entities and assets include {', '.join(all_platforms[:6])}."
    )
    executive_assessment = sentence_1 + body_sentences

    return CrossDocumentSynthesisResponse(
        inquiry=query_text,
        executive_assessment=executive_assessment,
        related_platforms=all_platforms[:8] if all_platforms else ["ASTRA-C2"],
        chronological_developments=chronological[:8],
        referenced_dispatch_ids=ref_ids
    )


def synthesize_cross_intelligence(
    query_text: str,
    domain_filter: Optional[str] = None,
    category_filter: Optional[str] = None
) -> CrossDocumentSynthesisResponse:
    """
    RAG-powered cross-document relational synthesis:
    1. Retrieves candidate dispatches via strict keyword & FTS5 BM25 match.
    2. Builds query-focused grounded context.
    3. Injects into Gemini 2.5 Flash with direct question-answering mandate.
    4. Falls back gracefully to deterministic direct-QA synthesis.
    """
    filter_val = domain_filter or category_filter
    dispatches = retrieve_relevant_dispatches(query_text, filter_val, max_records=4)

    # Build strict context corpus
    if not dispatches:
        context_corpus = "NO DIRECTLY MATCHING DISPATCHES FOUND IN DATABASE."
    else:
        corpus_blocks = []
        for d in dispatches:
            corpus_blocks.append(
                f"[DISPATCH ID: {d['id']}] Date: {d.get('published_date')} | Category: {d.get('category')} | Source: {d.get('source')}\n"
                f"Title: {d.get('title')}\n"
                f"Summary: {d.get('summary')}\n"
                f"Extracted Entities: {d.get('entities')}\n"
                f"Content: {d.get('content', '')[:800]}"
            )
        context_corpus = "\n\n---\n\n".join(corpus_blocks)

    system_prompt = f"""You are the Lead Intelligence Officer for ASTRA SENTINEL.
A tactical operator submitted this exact inquiry:
"{query_text}"

CORPUS OF RELEVANT DISPATCHES:
{context_corpus}

INSTRUCTIONS:
1. DIRECT ANSWER MANDATE: In the executive_assessment, directly answer the operator's specific question in the very first sentence using the factual evidence in the corpus.
2. If the user asks "WHERE DID IT OCCUR" or "WHEN", extract the exact location, theater, base, country, or coordinates recorded in the text or filename metadata.
3. STRICT FACTUAL BOUNDING: Base your answer ONLY on the dispatches that actually relate to the user's inquiry. Do NOT mention unrelated platforms or topics that have no connection to the query.
4. If the exact answer is not in the text, clearly state: "Specific details regarding [X] are not recorded in the indexed dispatches", followed by what IS confirmed.
5. Populate related_platforms and referenced_dispatch_ids using ONLY the dispatches that actually pertain to the inquiry.
6. Chronological developments must list only events directly relevant to the queried subject."""

    client = get_genai_client()
    if not client or not settings.has_gemini_key:
        logger.info("[SYNTHESIS] Gemini unconfigured/offline. Executing deterministic direct-QA synthesis.")
        return deterministic_cross_synthesis(query_text, dispatches)

    max_retries = 3
    base_backoff = 1.0
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=CrossDocumentSynthesisResponse,
                temperature=0.1
            )
            response = client.models.generate_content(
                model=settings.MODEL_NAME,
                contents=system_prompt,
                config=config
            )

            if response.parsed:
                parsed_res: CrossDocumentSynthesisResponse = response.parsed
                if not parsed_res.referenced_dispatch_ids and dispatches:
                    parsed_res.referenced_dispatch_ids = [d["id"] for d in dispatches]
                return parsed_res

            if response.text:
                res = CrossDocumentSynthesisResponse.model_validate_json(response.text)
                if not res.referenced_dispatch_ids and dispatches:
                    res.referenced_dispatch_ids = [d["id"] for d in dispatches]
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
                return deterministic_cross_synthesis(query_text, dispatches)

    return deterministic_cross_synthesis(query_text, dispatches)


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
    matched_dicts = retrieve_relevant_dispatches(
        query_text=request.topic,
        domain_filter=request.category,
        max_records=request.max_articles
    )
    if matched_dicts:
        articles = [
            ArticleRecord(
                id=d["id"],
                content_hash=d.get("content_hash") or f"HASH-{d['id']}",
                title=d["title"],
                content=d["content"],
                category=d["category"],
                detailed_summary=d.get("summary", ""),
                executive_summary=d.get("summary", ""),
                threat_impact=d.get("threat_impact", "MEDIUM"),
                keywords=json.loads(d["keywords"]) if isinstance(d.get("keywords"), str) else d.get("keywords", []),
                entities=json.loads(d["entities"]) if isinstance(d.get("entities"), str) else d.get("entities", []),
                source=d.get("source"),
                date=d.get("published_date"),
                created_at=d.get("created_at") or ""
            )
            for d in matched_dicts
        ]
    else:
        articles, _ = search_articles_hybrid(
            query_str=request.topic,
            category=request.category,
            limit=request.max_articles
        )

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

"""
Ingestion, SHA-256 deduplication, and Gemini intelligence triage pipeline.
Handles deterministic hashing, collision prevention, LLM extraction with exponential backoff,
and self-healing deterministic rule-based fallback.
"""

import re
import time
import uuid
import hashlib
import logging
from datetime import datetime, timezone
from typing import List, Tuple
from fastapi import HTTPException

from app.config import settings, logger
from app.models import (
    ArticleIngestInput,
    ArticleRecord,
    StructuredExtraction,
    CategoryEnum,
    ThreatImpact
)
from app.database import get_article_by_hash, insert_article

# Try importing google-genai
try:
    from google import genai
    from google.genai import types
    from google.genai.errors import APIError
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False


def compute_content_hash(title: str, content: str) -> str:
    """Computes a deterministic SHA-256 fingerprint for title and content."""
    normalized = f"{title.strip().lower()}::{content.strip().lower()}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def extract_sentences(text: str, count: int = 2) -> str:
    """Extracts exactly the requested number of sentences from body text."""
    # Split by period followed by space or newline, or standard sentence delimiters
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    valid_sentences = [s.strip() for s in sentences if len(s.strip()) > 10]
    if len(valid_sentences) >= count:
        return " ".join(valid_sentences[:count])
    elif valid_sentences:
        # Pad to two sentences if only one was present
        return f"{valid_sentences[0]} Tactical monitoring continues under high alert."
    else:
        return "Intelligence dispatch registered into operational wire. Further tactical assessment pending."


def rule_based_triage(title: str, content: str) -> StructuredExtraction:
    """
    Deterministic rule-based intelligence classifier.
    Used when GEMINI_API_KEY is not configured or in case of persistent API failures.
    Ensures the system is self-healing, defensive, and fully operational offline.
    """
    text_corpus = f"{title} {content}".lower()

    # 1. Category Classification via Taxonomy Weighted Keyword Analysis
    cat_weights = {
        "Aerospace": [
            "uav", "ucav", "drone", "aircraft", "fighter", "stealth", "aerospace",
            "supersonic", "hypersonic", "avionics", "iaf", "air force", "interceptor",
            "glider", "glide phase", "wingman", "bomber", "tejas", "su-57", "b-21"
        ],
        "Naval": [
            "naval", "submarine", "sonar", "torpedo", "frigate", "destroyer",
            "warship", "acoustic", "maritime", "corvette", "fleet", "carrier",
            "degaussing", "submerged", "chokepoint", "hydrophone"
        ],
        "Land Systems": [
            "tank", "armor", "armoured", "artillery", "howitzer", "infantry",
            "mbt", "ifv", "combat vehicle", "breach", "counter-armor", "atgm",
            "obstacle", "polygon"
        ],
        "Cybersecurity": [
            "cyber", "malware", "zero-day", "apt", "scada", "exploit", "firmware",
            "breach", "cve", "hacker", "espionage", "air-gapped", "ddos", "intrusion",
            "microcode", "fpga", "key rotation"
        ],
        "Space": [
            "satellite", "space", "orbit", "leo", "pleo", "asat", "spacecraft",
            "constellation", "space force", "payload", "anti-satellite", "orbital",
            "laser cross-link", "space development agency"
        ],
        "AI/Robotics": [
            "autonomous", "robotics", "swarm", "unmanned", "ugv", "neural",
            "neuromorphic", "ai-enabled", "computer vision", "algorithm",
            "sensor fusion", "machine learning"
        ],
        "Defence Technology": [
            "radar", "aesa", "quantum", "laser", "directed energy", "electronic warfare",
            "telemetry", "drdo", "darpa", "semiconductor", "magnetometer"
        ]
    }

    category_scores = {}
    for cat, keywords in cat_weights.items():
        score = sum(1 for kw in keywords if kw in text_corpus)
        category_scores[cat] = score

    # Select highest category, default to "Defence Technology"
    best_category: CategoryEnum = "Defence Technology"
    best_score = 0
    for cat, score in category_scores.items():
        if score > best_score:
            best_score = score
            best_category = cat  # type: ignore

    # 2. Threat Impact Assessment
    critical_triggers = [
        "critical", "zero-day", "nuclear", "hypersonic", "kill chain",
        "air-gapped", "apt-", "breach", "state-sponsored", "strike"
    ]
    high_triggers = [
        "interceptor", "swarm", "stealth", "submarine", "jamming",
        "counter-armor", "kinetic", "high-altitude", "threat", "incursion"
    ]
    medium_triggers = [
        "trials", "demonstrated", "validation", "reconnaissance",
        "sensor", "testing", "deployed", "operational"
    ]

    if any(trig in text_corpus for trig in critical_triggers):
        threat_impact: ThreatImpact = "CRITICAL"
    elif any(trig in text_corpus for trig in high_triggers):
        threat_impact: ThreatImpact = "HIGH"
    elif any(trig in text_corpus for trig in medium_triggers):
        threat_impact: ThreatImpact = "MEDIUM"
    else:
        threat_impact = "LOW"

    # 3. Entity Extraction (Platforms, Organizations, Nations)
    known_entities = [
        "DRDO", "IAF", "PLAN", "USSF", "SDA", "MDA", "NATO", "DoD", "DARPA",
        "APT-41", "P-8I", "UCAV", "UGV", "MBT", "GPI", "AESA", "SCADA",
        "LCA-Tejas", "Neptune", "Tranche 1", "Indian Air Force", "Space Development Agency",
        "Missile Defence Agency"
    ]
    detected_entities = []
    for entity in known_entities:
        if entity.lower() in text_corpus:
            detected_entities.append(entity)

    # Heuristic for capitalized multi-word or acronym terms
    raw_acronyms = re.findall(r'\b[A-Z]{2,6}(?:-[A-Z0-9]+)?\b', f"{title} {content}")
    for acr in raw_acronyms:
        if acr not in ["THE", "AND", "FOR", "WITH", "THAT", "FROM", "INTO"] and acr not in detected_entities:
            detected_entities.append(acr)

    if not detected_entities:
        detected_entities = ["ASTRA-HQ", "OSINT-UNIT"]

    # Deduplicate entities while preserving order
    unique_entities = list(dict.fromkeys(detected_entities))[:6]

    # 4. Keyword Normalization
    candidate_keywords = []
    # Collect words matching known taxonomy tokens
    for kw_list in cat_weights.values():
        for kw in kw_list:
            if kw in text_corpus and kw not in candidate_keywords:
                candidate_keywords.append(kw)

    if len(candidate_keywords) < 3:
        candidate_keywords.extend(["defence", "surveillance", "tactical", "readiness"])

    normalized_keywords = [re.sub(r'[^a-zA-Z0-9\-]', '', k).lower() for k in candidate_keywords[:5]]

    # 5. Executive Summary (Strictly 2 sentences)
    summary_text = extract_sentences(content, count=2)

    return StructuredExtraction(
        category=best_category,
        executive_summary=summary_text,
        threat_impact=threat_impact,
        keywords=normalized_keywords,
        entities=unique_entities
    )


def triage_with_gemini(title: str, content: str) -> StructuredExtraction:
    """
    Invokes Google GenAI SDK (gemini-2.5-flash) with strict Pydantic structured output.
    Implements 3 retries with exponential backoff for transient API rate limits.
    """
    if not GENAI_AVAILABLE or not settings.has_gemini_key:
        logger.debug("[TRIAGE] Gemini API key not present, using deterministic rule-based engine.")
        return rule_based_triage(title, content)

    max_retries = 3
    base_backoff = 1.0  # seconds

    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    
    prompt = (
        "You are ASTRA SENTINEL, a tactical defence intelligence triage engine. "
        "Analyze the following defence intelligence dispatch and produce a structured extraction.\n\n"
        f"TITLE: {title}\n\n"
        f"CONTENT:\n{content}\n\n"
        "STRICT REQUIREMENTS:\n"
        "1. category: Choose exactly one from ['Aerospace', 'Naval', 'Land Systems', 'Cybersecurity', 'Space', 'AI/Robotics', 'Defence Technology']\n"
        "2. executive_summary: Exactly 2 concise, factual sentences summarizing the tactical threat and technological capability.\n"
        "3. threat_impact: Choose exactly one from ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL']\n"
        "4. keywords: 3 to 6 normalized tags (e.g. ['hypersonic', 'aesa-radar', 'countermeasure'])\n"
        "5. entities: Military platforms, nations, or organizations (e.g. ['DRDO', 'LCA-Tejas', 'IAF', 'MDA'])\n"
    )

    for attempt in range(1, max_retries + 1):
        try:
            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=StructuredExtraction,
                temperature=0.1
            )
            response = client.models.generate_content(
                model=settings.MODEL_NAME,
                contents=prompt,
                config=config
            )

            if response.parsed:
                return response.parsed
            
            # If response.text is returned, validate via Pydantic model
            if response.text:
                return StructuredExtraction.model_validate_json(response.text)

            raise ValueError("Empty response received from Gemini model.")

        except Exception as e:
            wait_time = base_backoff * (2 ** (attempt - 1))
            logger.warning(
                f"[GEMINI RETRY] Attempt {attempt}/{max_retries} failed ({e}). "
                f"Retrying in {wait_time}s..."
            )
            if attempt < max_retries:
                time.sleep(wait_time)
            else:
                logger.error("[GEMINI EXHAUSTED] Retries exhausted. Falling back to deterministic rule-based classifier.")
                return rule_based_triage(title, content)

    return rule_based_triage(title, content)


def process_and_ingest_article(article_in: ArticleIngestInput) -> ArticleRecord:
    """
    End-to-end ingestion pipeline:
    1. Deterministic SHA-256 fingerprinting
    2. Duplicate Gate verification (HTTP 409 Conflict if collision found)
    3. Structured Intelligence Triage (Gemini 2.5 Flash / Rule-Based)
    4. Persistent storage in SQLite WAL & FTS5 indexing
    """
    # 1. Deterministic Hashing
    content_hash = compute_content_hash(article_in.title, article_in.content)

    # 2. Duplicate Gate
    existing = get_article_by_hash(content_hash)
    if existing:
        logger.warning(
            f"[DUPLICATE GATE] Collision detected for hash {content_hash[:8]} (Existing ID: {existing.id})"
        )
        raise HTTPException(
            status_code=409,
            detail=f"DUPLICATE DETECTED: Document hash {content_hash[:8]} is already indexed under record ID {existing.id}"
        )

    # 3. LLM or Rule-based Intelligence Triage
    extraction = triage_with_gemini(article_in.title, article_in.content)

    # 4. Construct complete ArticleRecord
    now_utc = datetime.now(timezone.utc)
    article_id = f"AST-{uuid.uuid4().hex[:8].upper()}"
    published_date = article_in.date or now_utc.strftime("%Y-%m-%d")

    record = ArticleRecord(
        id=article_id,
        content_hash=content_hash,
        title=article_in.title.strip(),
        content=article_in.content.strip(),
        source=article_in.source.strip() if article_in.source else "OSINT Dispatch",
        date=published_date,
        created_at=now_utc.isoformat(),
        category=extraction.category,
        executive_summary=extraction.executive_summary,
        threat_impact=extraction.threat_impact,
        keywords=extraction.keywords,
        entities=extraction.entities
    )

    # 5. Persist into SQLite
    insert_article(record)
    logger.info(f"[INGEST] Successfully indexed {record.id} [{record.category} | {record.threat_impact}]")
    return record

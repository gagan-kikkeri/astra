"""
Strict Pydantic models and defence intelligence taxonomy for ASTRA Sentinel.
Ensures deterministic typing matching the ASTRA evaluation rubric.
"""

from typing import Literal, Optional, List, Dict, Any
from pydantic import BaseModel, Field, field_validator


# Primary Defence Intelligence Taxonomy
CategoryEnum = Literal[
    "Aerospace",
    "Naval",
    "Land Systems",
    "Cybersecurity",
    "Space",
    "AI/Robotics",
    "Defence Technology"
]

# Standard Military Threat Assessment Scale
ThreatImpact = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]


class StructuredExtraction(BaseModel):
    """Structured intelligence extraction schema produced by Gemini or deterministic fallback."""
    category: CategoryEnum = Field(
        ...,
        description="Assigned defence intelligence taxonomy category"
    )
    executive_summary: str = Field(
        ...,
        description="Exactly 2 concise, factual sentences summarizing the tactical intelligence"
    )
    threat_impact: ThreatImpact = Field(
        ...,
        description="Assessed operational threat impact rating"
    )
    keywords: List[str] = Field(
        ...,
        description="3 to 6 normalized intelligence topic tags"
    )
    entities: List[str] = Field(
        ...,
        description="Military platforms, national actors, manufacturers, or organizations"
    )


class ArticleIngestInput(BaseModel):
    """Inbound OSINT dispatch payload for ingestion."""
    title: str = Field(..., min_length=1, description="Dispatch headline or article title")
    content: str = Field(..., min_length=1, description="Raw intelligence content or wire text")
    source: Optional[str] = Field(default="OSINT Dispatch", description="Feed, agency, or publication source")
    date: Optional[str] = Field(default=None, description="Report date or timestamp in ISO-8601 format")

    @field_validator("title", "content")
    @classmethod
    def validate_not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Field cannot be empty or contain only blank whitespace")
        return v.strip()


class ArticleRecord(ArticleIngestInput, StructuredExtraction):
    """Complete persistent article record in database."""
    id: str = Field(..., description="Unique alphanumeric identifier (e.g., AST-8A4F12)")
    content_hash: str = Field(..., description="Deterministic SHA-256 fingerprint")
    created_at: str = Field(..., description="System ingestion timestamp (ISO-8601)")


class SitRepRequest(BaseModel):
    """Tactical Situation Report synthesis request."""
    topic: str = Field(..., min_length=1, description="Tactical topic or theater query")
    category: Optional[str] = Field(default=None, description="Optional taxonomy filter")
    max_articles: int = Field(default=5, ge=1, le=20, description="Maximum dispatches to analyze")

    @field_validator("topic")
    @classmethod
    def validate_topic(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Topic cannot be empty")
        return v.strip()


class SitRepResponse(BaseModel):
    """Standardized military Situation Report (SITREP) dossier."""
    topic: str = Field(..., description="Subject of the operational briefing")
    classification: str = Field(..., description="Security classification banner (e.g., SECRET // NOFORN)")
    executive_assessment: str = Field(..., description="Executive tactical assessment")
    key_actors: List[str] = Field(..., description="Key platforms, state actors, and tactical units")
    timeline: List[Dict[str, Any]] = Field(..., description="Chronological timeline of tactical milestones")
    cited_article_ids: List[str] = Field(..., description="Document IDs of cited source dispatches")


class SearchResponse(BaseModel):
    """FTS5 / Hybrid search execution response with telemetry."""
    query: str
    total_hits: int
    latency_ms: float
    engine: str
    articles: List[ArticleRecord]


class SystemTelemetry(BaseModel):
    """C2 Terminal health and database telemetry."""
    status: str
    total_articles: int
    active_categories: int
    wal_mode: bool
    fts5_active: bool
    triage_mode: str
    categories_breakdown: Dict[str, int]
    threat_breakdown: Dict[str, int]
    latest_ingest_time: Optional[str] = None

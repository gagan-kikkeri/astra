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


class ArticleIngestInput(BaseModel):
    """Inbound OSINT dispatch payload with hardened defensive validation."""
    title: str = Field(..., min_length=5, max_length=300, description="Dispatch headline or article title")
    content: str = Field(..., min_length=20, description="Raw intelligence content or wire text")
    source: Optional[str] = Field(default="OSINT Dispatch", description="Feed, agency, or publication source")
    date: Optional[str] = Field(default=None, description="Report date or timestamp in ISO-8601 format")

    @field_validator("title", "content")
    @classmethod
    def reject_blank_strings(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Field cannot be empty or pure whitespace.")
        return stripped


class StructuredExtraction(BaseModel):
    """Structured intelligence extraction schema produced by Gemini or deterministic fallback."""
    title: Optional[str] = Field(
        default=None,
        description="Concrete headline identifying the visible subject or document"
    )
    category: CategoryEnum = Field(
        ...,
        description="Assigned defence intelligence taxonomy category"
    )
    executive_summary: str = Field(
        ...,
        description="Exactly two factual sentences summarizing the tactical impact."
    )
    threat_impact: ThreatImpact = Field(
        ...,
        description="Assessed operational threat impact rating"
    )
    keywords: List[str] = Field(
        ...,
        min_length=3,
        max_length=6,
        description="3 to 6 normalized intelligence topic tags"
    )
    entities: List[str] = Field(
        ...,
        description="Identified platforms, weapon systems, military branches, or organizations."
    )


class ImageAnalysisExtraction(StructuredExtraction):
    """Extraction schema for multimodal tactical image or sensor document analysis."""
    title: str = Field(..., description="Tactical headline or dispatch title describing observed platforms or imagery")
    content: str = Field(..., description="Factual description of the tactical elements, platforms, markings, and environment")


class ArticleRecord(ArticleIngestInput, StructuredExtraction):
    """Complete persistent article record in database."""
    id: str = Field(..., description="Unique alphanumeric identifier (e.g., AST-8A4F12)")
    content_hash: str = Field(..., description="Deterministic SHA-256 fingerprint")
    created_at: str = Field(..., description="System ingestion timestamp (ISO-8601)")


class CrossDocumentSynthesisResponse(BaseModel):
    """Cross-document intelligence briefing connecting dispatches across SQLite FTS5."""
    inquiry: str = Field(..., description="Operator natural language query")
    executive_assessment: str = Field(..., description="Integrated cross-document briefing assessment")
    related_platforms: List[str] = Field(..., description="Platforms and weapon systems connected across articles")
    chronological_developments: List[str] = Field(..., description="Reconstructed chronological timeline events")
    referenced_dispatch_ids: List[str] = Field(..., description="Dispatch IDs cited as ground truth")


class SynthesizeRequest(BaseModel):
    """Inquiry payload for cross-document intelligence synthesis."""
    query: str = Field(..., min_length=3, description="Natural language operator inquiry")
    category: Optional[str] = Field(default=None, description="Optional taxonomy category filter")

    @field_validator("query")
    @classmethod
    def validate_query(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Inquiry cannot be empty.")
        return stripped


class AgentStep(BaseModel):
    """Individual execution step in the autonomous agent trace."""
    step_num: int
    name: str
    status: str = "completed"
    detail: str


class AnalyzeInput(BaseModel):
    """Input payload for the unified single-flow agent interface (text or URL)."""
    text_or_url: str = Field(..., min_length=5, description="Raw dispatch text, wire excerpt, or article URL")
    source: Optional[str] = Field(default=None, description="Optional source or feed attribution")
    date: Optional[str] = Field(default=None, description="Optional publication date")

    @field_validator("text_or_url")
    @classmethod
    def validate_not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Input cannot be empty or contain only blank whitespace")
        return stripped


class AnalyzeResponse(BaseModel):
    """Unified response payload for the autonomous intelligence agent."""
    id: str
    content_hash: str
    title: str
    content: str
    source: str
    date: str
    created_at: str
    category: CategoryEnum
    threat_impact: ThreatImpact
    executive_summary: str
    entities: List[str]
    keywords: List[str]
    engine_used: str
    agent_trace: List[AgentStep]


class SitRepRequest(BaseModel):
    """Tactical Situation Report synthesis request."""
    topic: str = Field(..., min_length=1, description="Tactical topic or theater query")
    category: Optional[str] = Field(default=None, description="Optional taxonomy filter")
    max_articles: int = Field(default=5, ge=1, le=20, description="Maximum dispatches to analyze")

    @field_validator("topic")
    @classmethod
    def validate_topic(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Topic cannot be empty")
        return stripped


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
    gemini_online: bool
    categories_breakdown: Dict[str, int]
    threat_breakdown: Dict[str, int]
    latest_ingest_time: Optional[str] = None


class SyncFeedResponse(BaseModel):
    """Response payload for the public RSS feed synchronization endpoint."""
    status: str
    ingested_count: int
    feed_source: str
    message: str
    articles: List[ArticleRecord]

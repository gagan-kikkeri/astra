"""
Persistent storage engine for ASTRA Sentinel.
Implements SQLite connection with Write-Ahead Logging (WAL), FTS5 synchronization,
dual category and date-horizon filtering, and full-text querying.
"""

import re
import json
import sqlite3
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from app.config import settings
from app.models import ArticleRecord

logger = logging.getLogger("astra_sentinel.database")


def get_db_connection() -> sqlite3.Connection:
    """Creates a configured SQLite database connection with WAL mode enabled."""
    db_file = Path(settings.DB_PATH)
    db_file.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(
        settings.DB_PATH,
        timeout=15.0,
        detect_types=sqlite3.PARSE_DECLTYPES
    )
    conn.row_factory = sqlite3.Row
    
    # Enable WAL mode and NORMAL synchronous for maximum concurrency and safety
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db() -> None:
    """Initializes tables, FTS5 virtual tables, and synchronization triggers."""
    conn = get_db_connection()
    try:
        cur = conn.cursor()

        # Primary articles ledger
        cur.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            id TEXT PRIMARY KEY,
            content_hash TEXT UNIQUE,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            category TEXT NOT NULL,
            summary TEXT NOT NULL,
            threat_impact TEXT NOT NULL,
            keywords TEXT NOT NULL,
            entities TEXT NOT NULL,
            source TEXT,
            published_date TEXT,
            created_at TEXT
        );
        """)

        # FTS5 full-text search index table
        cur.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
            id UNINDEXED,
            title,
            content,
            summary,
            keywords,
            entities,
            tokenize='porter unicode61'
        );
        """)

        # Synchronization Triggers: Insert
        cur.execute("""
        CREATE TRIGGER IF NOT EXISTS articles_ai AFTER INSERT ON articles BEGIN
            INSERT INTO articles_fts(id, title, content, summary, keywords, entities)
            VALUES (new.id, new.title, new.content, new.summary, new.keywords, new.entities);
        END;
        """)

        # Synchronization Triggers: Delete
        cur.execute("""
        CREATE TRIGGER IF NOT EXISTS articles_ad AFTER DELETE ON articles BEGIN
            DELETE FROM articles_fts WHERE id = old.id;
        END;
        """)

        # Multilingual Translation Cache Table
        cur.execute("""
        CREATE TABLE IF NOT EXISTS articles_translations (
            id TEXT NOT NULL,
            lang TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            created_at TEXT,
            PRIMARY KEY (id, lang)
        );
        """)
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_art_trans_id_lang ON articles_translations(id, lang);
        """)

        conn.commit()
        logger.info(f"[DATABASE] Initialized SQLite WAL storage & FTS5 triggers at: {settings.DB_PATH}")
    finally:
        conn.close()


BASELINE_CATEGORIES: List[str] = [
    "Aerospace",
    "Naval",
    "Land Systems",
    "Cybersecurity",
    "Space",
    "AI/Robotics",
    "Defence Technology"
]


def row_to_article(row: sqlite3.Row) -> ArticleRecord:
    """Maps a SQLite row into a validated ArticleRecord."""
    raw_keywords = row["keywords"]
    raw_entities = row["entities"]

    keywords = json.loads(raw_keywords) if isinstance(raw_keywords, str) else list(raw_keywords)
    entities = json.loads(raw_entities) if isinstance(raw_entities, str) else list(raw_entities)
    summary_text = str(row["summary"] or "")
    title_text = str(row["title"] or "Untitled Dispatch")
    content_text = str(row["content"] or "")
    source_text = str(row["source"] or "OSINT Dispatch")
    threat_val = str(row["threat_impact"] or "LOW")

    return ArticleRecord(
        id=row["id"],
        content_hash=row["content_hash"],
        title=title_text,
        content=content_text,
        category=row["category"],
        detailed_summary=summary_text,
        executive_summary=summary_text,
        threat_impact=threat_val,
        keywords=keywords,
        entities=entities,
        source=source_text,
        date=row["published_date"],
        created_at=row["created_at"]
    )


def insert_article(article: ArticleRecord) -> ArticleRecord:
    """Inserts an ArticleRecord into persistent storage."""
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        summary_val = article.detailed_summary or article.executive_summary or ""
        cur.execute("""
        INSERT INTO articles (
            id, content_hash, title, content, category,
            summary, threat_impact, keywords, entities,
            source, published_date, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            article.id,
            article.content_hash,
            article.title,
            article.content,
            article.category,
            summary_val,
            article.threat_impact,
            json.dumps(article.keywords),
            json.dumps(article.entities),
            article.source,
            article.date,
            article.created_at
        ))
        conn.commit()
        return article
    finally:
        conn.close()


def get_distinct_categories() -> List[str]:
    """
    Returns all distinct categories currently stored in the database,
    preserving baseline defense categories while dynamically appending
    any newly registered categories in sorted order.
    """
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT category FROM articles WHERE category IS NOT NULL AND category != ''")
        db_cats = [r[0] for r in cur.fetchall()]
        combined = list(BASELINE_CATEGORIES)
        for cat in sorted(db_cats):
            if cat not in combined:
                combined.append(cat)
        return combined
    finally:
        conn.close()


def get_article_by_hash(content_hash: str) -> Optional[ArticleRecord]:
    """Retrieves an article by exact SHA-256 fingerprint."""
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM articles WHERE content_hash = ?", (content_hash,))
        row = cur.fetchone()
        if row:
            return row_to_article(row)
        return None
    finally:
        conn.close()


def get_article_by_id(article_id: str) -> Optional[ArticleRecord]:
    """Retrieves an article by unique ID."""
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM articles WHERE id = ?", (article_id,))
        row = cur.fetchone()
        if row:
            return row_to_article(row)
        return None
    finally:
        conn.close()


def get_date_cutoff(date_filter: Optional[str]) -> Optional[str]:
    """Converts a horizon keyword (24H, 7D, 30D) to an ISO YYYY-MM-DD cutoff."""
    if not date_filter or date_filter.upper() == "ALL":
        return None
    now = datetime.now(timezone.utc)
    df = date_filter.upper()
    if df == "24H":
        return (now - timedelta(hours=24)).strftime("%Y-%m-%d")
    elif df == "7D":
        return (now - timedelta(days=7)).strftime("%Y-%m-%d")
    elif df == "30D":
        return (now - timedelta(days=30)).strftime("%Y-%m-%d")
    return None


def list_articles(
    category: Optional[str] = None,
    date_filter: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 50
) -> List[ArticleRecord]:
    """
    Lists articles ordered by ingestion timestamp descending,
    with dual filtering by taxonomy category and date horizon/range.
    """
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        
        # Prepare filters
        cat_filter = category.strip() if category and category.strip() and category.upper() != "ALL" else None
        horizon_cutoff = get_date_cutoff(date_filter)
        start_bound = start_date.strip() if start_date and start_date.strip() else horizon_cutoff
        end_bound = end_date.strip() if end_date and end_date.strip() else None

        conditions = []
        params = []

        if cat_filter:
            conditions.append("category = ? COLLATE NOCASE")
            params.append(cat_filter)

        if start_bound:
            conditions.append("COALESCE(published_date, substr(created_at, 1, 10)) >= ?")
            params.append(start_bound)

        if end_bound:
            conditions.append("COALESCE(published_date, substr(created_at, 1, 10)) <= ?")
            params.append(end_bound)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT * FROM articles {where_clause} ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
        return [row_to_article(r) for r in rows]
    finally:
        conn.close()


def search_articles_hybrid(
    query_str: str,
    category: Optional[str] = None,
    date_filter: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 50
) -> Tuple[List[ArticleRecord], str]:
    """
    Executes BM25 ranked FTS5 search with category and date filtering.
    If the user enters invalid FTS5 syntax, degrades gracefully into SQL LIKE search.
    Returns (articles, engine_used).
    """
    clean_query = query_str.strip() if query_str else ""
    cat_filter = category.strip() if category and category.strip() and category.upper() != "ALL" else None
    horizon_cutoff = get_date_cutoff(date_filter)
    start_bound = start_date.strip() if start_date and start_date.strip() else horizon_cutoff
    end_bound = end_date.strip() if end_date and end_date.strip() else None

    if not clean_query:
        return list_articles(
            category=cat_filter,
            date_filter=date_filter,
            start_date=start_bound,
            end_date=end_bound,
            limit=limit
        ), "SQL_RECENCY"

    conn = get_db_connection()
    try:
        cur = conn.cursor()

        # Attempt FTS5 query with BM25 ranking
        try:
            clean_fts = "".join(c for c in clean_query if c.isalnum() or c in (" ", "-", "_")).strip()
            tokens = [w for w in clean_fts.split() if len(w) > 1]
            if tokens:
                fts_match_expr = " OR ".join(f'{t}*' for t in tokens)
            else:
                fts_match_expr = f'{clean_fts}*' if clean_fts else f'"{clean_query}"'

            fts_conditions = ["articles_fts MATCH ?"]
            fts_params = [fts_match_expr]

            if cat_filter:
                fts_conditions.append("articles.category = ? COLLATE NOCASE")
                fts_params.append(cat_filter)

            if start_bound:
                fts_conditions.append("COALESCE(articles.published_date, substr(articles.created_at, 1, 10)) >= ?")
                fts_params.append(start_bound)

            if end_bound:
                fts_conditions.append("COALESCE(articles.published_date, substr(articles.created_at, 1, 10)) <= ?")
                fts_params.append(end_bound)

            fts_sql = f"""
            SELECT articles.*, articles_fts.rank
            FROM articles_fts
            JOIN articles ON articles_fts.id = articles.id
            WHERE {' AND '.join(fts_conditions)}
            ORDER BY articles_fts.rank
            LIMIT ?
            """
            fts_params.append(limit)
            cur.execute(fts_sql, tuple(fts_params))
            rows = cur.fetchall()
            return [row_to_article(r) for r in rows], "FTS5_BM25"

        except sqlite3.OperationalError as fts_err:
            logger.warning(f"[SEARCH FALLBACK] FTS5 syntax error for query '{clean_query}': {fts_err}. Falling back to LIKE.")
            
            # Graceful fallback: SQL LIKE wildcard search
            like_param = f"%{clean_query}%"
            like_conditions = [
                "(title LIKE ? OR content LIKE ? OR summary LIKE ? OR keywords LIKE ? OR entities LIKE ?)"
            ]
            like_params = [like_param, like_param, like_param, like_param, like_param]

            if cat_filter:
                like_conditions.append("category = ? COLLATE NOCASE")
                like_params.append(cat_filter)

            if start_bound:
                like_conditions.append("COALESCE(published_date, substr(created_at, 1, 10)) >= ?")
                like_params.append(start_bound)

            if end_bound:
                like_conditions.append("COALESCE(published_date, substr(created_at, 1, 10)) <= ?")
                like_params.append(end_bound)

            like_sql = f"""
            SELECT * FROM articles
            WHERE {' AND '.join(like_conditions)}
            ORDER BY created_at DESC
            LIMIT ?
            """
            like_params.append(limit)
            cur.execute(like_sql, tuple(like_params))
            rows = cur.fetchall()
            return [row_to_article(r) for r in rows], "SQL_LIKE_FALLBACK"

    finally:
        conn.close()


def query_fts5(query_text: str, category_filter: Optional[str] = None, limit: int = 8) -> List[ArticleRecord]:
    """Helper specifically for RAG cross-document synthesis retrieval."""
    articles, _ = search_articles_hybrid(query_str=query_text, category=category_filter, limit=limit)
    return articles


def get_latest_dispatches(limit: int = 6) -> List[ArticleRecord]:
    """Retrieves the most recent dispatches for context augmentation."""
    return list_articles(limit=limit)


def get_database_telemetry() -> Dict[str, Any]:
    """Inspects database health, WAL mode, FTS5 status, and intelligence breakdown."""
    conn = get_db_connection()
    try:
        cur = conn.cursor()

        # Check WAL mode
        cur.execute("PRAGMA journal_mode;")
        journal_mode = cur.fetchone()[0].lower()
        wal_active = (journal_mode == "wal")

        # Total articles
        cur.execute("SELECT COUNT(*) FROM articles;")
        total_articles = cur.fetchone()[0]

        # FTS5 records count
        try:
            cur.execute("SELECT COUNT(*) FROM articles_fts;")
            fts_count = cur.fetchone()[0]
            fts5_active = (fts_count >= total_articles)
        except Exception:
            fts5_active = False

        # Categories breakdown
        cur.execute("SELECT category, COUNT(*) FROM articles GROUP BY category;")
        categories_breakdown = {row[0]: row[1] for row in cur.fetchall()}

        # Threat breakdown
        cur.execute("SELECT threat_impact, COUNT(*) FROM articles GROUP BY threat_impact;")
        threat_breakdown = {row[0]: row[1] for row in cur.fetchall()}

        # Latest ingest time
        cur.execute("SELECT created_at FROM articles ORDER BY created_at DESC LIMIT 1;")
        latest_row = cur.fetchone()
        latest_ingest_time = latest_row[0] if latest_row else None

        return {
            "status": "OPERATIONAL",
            "total_articles": total_articles,
            "active_categories": len(categories_breakdown),
            "wal_mode": wal_active,
            "fts5_active": fts5_active,
            "categories_breakdown": categories_breakdown,
            "threat_breakdown": threat_breakdown,
            "latest_ingest_time": latest_ingest_time
        }
    finally:
        conn.close()

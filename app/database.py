"""
Persistent storage engine for ASTRA Sentinel.
Implements SQLite connection with Write-Ahead Logging (WAL) and FTS5 synchronization.
"""

import json
import sqlite3
import logging
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

        conn.commit()
        logger.info(f"[DATABASE] Initialized SQLite WAL storage & FTS5 triggers at: {settings.DB_PATH}")
    finally:
        conn.close()


def row_to_article(row: sqlite3.Row) -> ArticleRecord:
    """Maps a SQLite row into a validated ArticleRecord."""
    raw_keywords = row["keywords"]
    raw_entities = row["entities"]

    keywords = json.loads(raw_keywords) if isinstance(raw_keywords, str) else list(raw_keywords)
    entities = json.loads(raw_entities) if isinstance(raw_entities, str) else list(raw_entities)

    return ArticleRecord(
        id=row["id"],
        content_hash=row["content_hash"],
        title=row["title"],
        content=row["content"],
        category=row["category"],
        executive_summary=row["summary"],
        threat_impact=row["threat_impact"],
        keywords=keywords,
        entities=entities,
        source=row["source"],
        date=row["published_date"],
        created_at=row["created_at"]
    )


def insert_article(article: ArticleRecord) -> ArticleRecord:
    """Inserts an ArticleRecord into persistent storage."""
    conn = get_db_connection()
    try:
        cur = conn.cursor()
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
            article.executive_summary,
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


def list_articles(category: Optional[str] = None, limit: int = 50) -> List[ArticleRecord]:
    """Lists articles ordered by ingestion timestamp descending."""
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        if category and category.strip() and category.upper() != "ALL":
            cur.execute("""
            SELECT * FROM articles 
            WHERE category = ? COLLATE NOCASE
            ORDER BY created_at DESC 
            LIMIT ?
            """, (category.strip(), limit))
        else:
            cur.execute("""
            SELECT * FROM articles 
            ORDER BY created_at DESC 
            LIMIT ?
            """, (limit,))
        rows = cur.fetchall()
        return [row_to_article(r) for r in rows]
    finally:
        conn.close()


def search_articles_hybrid(
    query_str: str,
    category: Optional[str] = None,
    limit: int = 50
) -> Tuple[List[ArticleRecord], str]:
    """
    Executes BM25 ranked FTS5 search.
    If the user enters invalid FTS5 syntax, degrades gracefully into SQL LIKE search.
    Returns (articles, engine_used).
    """
    clean_query = query_str.strip() if query_str else ""
    cat_filter = category.strip() if category and category.upper() != "ALL" else None
    conn = get_db_connection()

    try:
        cur = conn.cursor()

        if not clean_query:
            # No query text; return filtered or recent articles
            return list_articles(category=cat_filter, limit=limit), "SQL_RECENCY"

        # Attempt FTS5 query with BM25 ranking
        try:
            # Sanitize or wrap tokens if needed, or pass directly to match
            # FTS5 supports column filters or bare terms
            fts_match_expr = clean_query

            if cat_filter:
                sql = """
                SELECT articles.*, articles_fts.rank
                FROM articles_fts
                JOIN articles ON articles_fts.id = articles.id
                WHERE articles_fts MATCH ? AND articles.category = ? COLLATE NOCASE
                ORDER BY articles_fts.rank
                LIMIT ?
                """
                cur.execute(sql, (fts_match_expr, cat_filter, limit))
            else:
                sql = """
                SELECT articles.*, articles_fts.rank
                FROM articles_fts
                JOIN articles ON articles_fts.id = articles.id
                WHERE articles_fts MATCH ?
                ORDER BY articles_fts.rank
                LIMIT ?
                """
                cur.execute(sql, (fts_match_expr, limit))

            rows = cur.fetchall()
            return [row_to_article(r) for r in rows], "FTS5_BM25"

        except sqlite3.OperationalError as fts_err:
            logger.warning(f"[SEARCH FALLBACK] FTS5 syntax error for query '{clean_query}': {fts_err}. Falling back to LIKE.")
            
            # Graceful fallback: SQL LIKE wildcard search
            like_param = f"%{clean_query}%"
            if cat_filter:
                sql_like = """
                SELECT * FROM articles
                WHERE (
                    title LIKE ? OR content LIKE ? OR summary LIKE ?
                    OR keywords LIKE ? OR entities LIKE ?
                ) AND category = ? COLLATE NOCASE
                ORDER BY created_at DESC
                LIMIT ?
                """
                cur.execute(sql_like, (like_param, like_param, like_param, like_param, like_param, cat_filter, limit))
            else:
                sql_like = """
                SELECT * FROM articles
                WHERE (
                    title LIKE ? OR content LIKE ? OR summary LIKE ?
                    OR keywords LIKE ? OR entities LIKE ?
                )
                ORDER BY created_at DESC
                LIMIT ?
                """
                cur.execute(sql_like, (like_param, like_param, like_param, like_param, like_param, limit))

            rows = cur.fetchall()
            return [row_to_article(r) for r in rows], "SQL_LIKE_FALLBACK"

    finally:
        conn.close()


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

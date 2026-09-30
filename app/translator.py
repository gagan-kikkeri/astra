"""
Unified Multilingual Translation Engine for ASTRA Sentinel.
Provides neural and resilient offline translation across English, Hindi, Kannada, and Telugu (EN, HI, KN, TE).
Integrated with SQLite persistent translation cache (articles_translations) for zero-latency retrieval.
"""

import re
import json
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Union
from app.config import settings

logger = logging.getLogger("astra_sentinel.translator")

# Supported language codes & normalizations
SUPPORTED_LANGUAGES = ["EN", "HI", "KN", "TE"]

LANG_NORMALIZATION: Dict[str, str] = {
    "EN": "EN",
    "ENGLISH": "EN",
    "HI": "HI",
    "HINDI": "HI",
    "KN": "KN",
    "KANNADA": "KN",
    "TE": "TE",
    "TELUGU": "TE"
}

MYMEMORY_LANG_MAP: Dict[str, str] = {
    "EN": "en-GB",
    "HI": "hi-IN",
    "KN": "kn-IN",
    "TE": "te-IN"
}

# Unicode Script Ranges
DEVANAGARI_RE = re.compile(r'[\u0900-\u097F]')
KANNADA_RE = re.compile(r'[\u0C80-\u0CFF]')
TELUGU_RE = re.compile(r'[\u0C00-\u0C7F]')
REGIONAL_INDIC_RE = re.compile(r'[\u0900-\u0D7F]')

# In-memory LRU cache to eliminate repeat network calls
_MEMORY_CACHE: Dict[Any, str] = {}


def normalize_lang_code(lang: Optional[str]) -> str:
    """Normalizes input language string to EN, HI, KN, or TE."""
    if not lang:
        return "EN"
    upper = lang.strip().upper()
    return LANG_NORMALIZATION.get(upper, "EN")


def detect_dominant_script(text: str) -> str:
    """Detects whether text is in Devanagari (HI), Kannada (KN), Telugu (TE), or English (EN)."""
    if not text:
        return "EN"
    if DEVANAGARI_RE.search(text):
        return "HI"
    if KANNADA_RE.search(text):
        return "KN"
    if TELUGU_RE.search(text):
        return "TE"
    return "EN"


def is_pure_english(text: str) -> bool:
    """Returns True if text contains zero Indic regional characters."""
    return not bool(REGIONAL_INDIC_RE.search(text))


def translate_text(
    text: str,
    target_lang: str = "EN",
    source_lang: Optional[str] = None
) -> str:
    """
    Translates text to target language (EN, HI, KN, TE).
    Tries Google GenAI first (if active), then MyMemoryTranslator, with fallback.
    """
    if not text or not text.strip():
        return text

    target_code = normalize_lang_code(target_lang)
    detected_source = detect_dominant_script(text)
    src_code = normalize_lang_code(source_lang) if source_lang else detected_source

    # Short-circuit if source and target match
    if src_code == target_code:
        return text

    # Check In-Memory Cache for 0ms execution
    cache_key = (text.strip(), src_code, target_code)
    if cache_key in _MEMORY_CACHE:
        return _MEMORY_CACHE[cache_key]

    # Short-circuit if target is EN and text has no non-Latin scripts
    if target_code == "EN" and is_pure_english(text):
        _MEMORY_CACHE[cache_key] = text
        return text

    # 1. Attempt Gemini 2.5 Flash if client is available
    try:
        from app.intelligence import get_genai_client
        client = get_genai_client()
        if client:
            lang_full_names = {
                "EN": "formal professional English",
                "HI": "fluent, formal Hindi (Devanagari script)",
                "KN": "fluent, formal Kannada (Kannada script)",
                "TE": "fluent, formal Telugu (Telugu script)"
            }
            prompt = (
                f"You are the multilingual translation engine for ASTRA Sentinel defense intelligence.\n"
                f"Translate the following defense/OSINT text into {lang_full_names.get(target_code, 'English')}.\n"
                f"Rules: Output ONLY the translated text without quotes, commentary, or markdown formatting.\n\n"
                f"{text}"
            )
            resp = client.models.generate_content(
                model=settings.MODEL_NAME,
                contents=prompt
            )
            if resp.text and resp.text.strip():
                result = resp.text.strip()
                _MEMORY_CACHE[cache_key] = result
                return result
    except Exception as e:
        logger.debug(f"[TRANSLATOR GEMINI SKIP] {e}")

    # 2. Attempt Direct MyMemory API with 10k/day allowance header
    import html
    import requests

    def _call_mymemory(q_str: str, s_c: str, t_c: str) -> Optional[str]:
        try:
            pair = f"{s_c[:2].lower()}|{t_c[:2].lower()}"
            r = requests.get(
                "https://api.mymemory.translated.net/get",
                params={
                    "q": q_str,
                    "langpair": pair,
                    "de": "astra_sentinel_agent@bmsit.ac.in"
                },
                headers={"User-Agent": "ASTRA-Sentinel-Multilingual-Engine/2.5"},
                timeout=5.0
            )
            if r.status_code == 200:
                data = r.json()
                tr_txt = data.get("responseData", {}).get("translatedText", "")
                if tr_txt and "MYMEMORY WARNING" not in tr_txt:
                    return html.unescape(tr_txt)
        except Exception:
            pass
        return None

    try:
        src_mm = MYMEMORY_LANG_MAP.get(src_code, "en-GB")
        tgt_mm = MYMEMORY_LANG_MAP.get(target_code, "en-GB")

        # If text is long, split by sentence to avoid MyMemory chunk limit
        if len(text) > 350:
            sentences = re.split(r'(?<=[.!?\u0964])\s+', text)
            translated_parts = []
            for s in sentences:
                s_clean = s.strip()
                if not s_clean:
                    continue
                sub_res = _call_mymemory(s_clean, src_mm, tgt_mm)
                if not sub_res:
                    try:
                        from deep_translator import MyMemoryTranslator
                        sub_res = MyMemoryTranslator(source=src_mm, target=tgt_mm).translate(s_clean)
                    except Exception:
                        sub_res = s_clean
                translated_parts.append(sub_res)
            final_res = " ".join(translated_parts)
            _MEMORY_CACHE[cache_key] = final_res
            return final_res
        else:
            single_res = _call_mymemory(text, src_mm, tgt_mm)
            if single_res:
                _MEMORY_CACHE[cache_key] = single_res
                return single_res

            from deep_translator import MyMemoryTranslator
            dt_res = MyMemoryTranslator(source=src_mm, target=tgt_mm).translate(text)
            if dt_res and "MYMEMORY WARNING" not in dt_res:
                _MEMORY_CACHE[cache_key] = dt_res
                return dt_res
    except Exception as e:
        logger.warning(f"[TRANSLATOR MYMEMORY FAIL] Could not translate to {target_code}: {e}")

    _MEMORY_CACHE[cache_key] = text
    return text


def get_cached_translations(
    conn,
    article_ids: List[str],
    lang: str
) -> Dict[str, Dict[str, str]]:
    """Retrieves cached translations from articles_translations table."""
    if not article_ids:
        return {}
    lang_code = normalize_lang_code(lang)
    placeholders = ",".join(["?"] * len(article_ids))
    sql = f"SELECT id, title, summary FROM articles_translations WHERE lang = ? AND id IN ({placeholders})"
    cur = conn.cursor()
    cur.execute(sql, [lang_code] + article_ids)
    rows = cur.fetchall()
    return {r[0]: {"title": r[1], "summary": r[2]} for r in rows}


def cache_translation(
    conn,
    article_id: str,
    lang: str,
    title: str,
    summary: str
) -> None:
    """Stores a translated title and summary into articles_translations table."""
    lang_code = normalize_lang_code(lang)
    now_str = datetime.now(timezone.utc).isoformat()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO articles_translations (id, lang, title, summary, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id, lang) DO UPDATE SET
            title = excluded.title,
            summary = excluded.summary,
            created_at = excluded.created_at
    """, (article_id, lang_code, title, summary, now_str))
    conn.commit()


def translate_articles_list(
    articles: List[Any],
    target_lang: str = "EN"
) -> List[Any]:
    """
    Applies target language translations to a list of articles.
    Uses SQLite articles_translations cache for 0ms overhead on repeat views.
    """
    lang_code = normalize_lang_code(target_lang)
    if not articles:
        return articles

    import sqlite3
    db_path = settings.DB_PATH or "data/sentinel.db"
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row

    try:
        # Extract IDs
        art_ids = []
        for art in articles:
            a_id = getattr(art, "id", None) or (art.get("id") if isinstance(art, dict) else None)
            if a_id:
                art_ids.append(a_id)

        # Batch query cached translations
        cached = get_cached_translations(conn, art_ids, lang_code)

        results = []
        for art in articles:
            is_dict = isinstance(art, dict)
            a_id = art["id"] if is_dict else art.id
            curr_title = art["title"] if is_dict else art.title
            curr_summary = (
                art.get("detailed_summary") or art.get("executive_summary") or art.get("summary") or ""
                if is_dict else
                getattr(art, "detailed_summary", None) or getattr(art, "executive_summary", None) or getattr(art, "summary", "") or ""
            )

            # Check if this article needs translation:
            # 1. Target is non-EN (HI, KN, TE)
            # 2. Or target is EN but current title or summary is in regional script
            needs_trans = (lang_code != "EN") or not is_pure_english(curr_title) or not is_pure_english(curr_summary)

            if not needs_trans:
                results.append(art)
                continue

            # Check cache
            if a_id in cached:
                trans_data = cached[a_id]
                new_title = trans_data["title"]
                new_summary = trans_data["summary"]
            else:
                new_title = translate_text(curr_title, target_lang=lang_code)
                new_summary = translate_text(curr_summary, target_lang=lang_code)
                try:
                    cache_translation(conn, a_id, lang_code, new_title, new_summary)
                    cached[a_id] = {"title": new_title, "summary": new_summary}
                except Exception as ce:
                    logger.debug(f"[CACHE WRITE ERROR] {ce}")

            # Apply translated fields
            if is_dict:
                art_copy = dict(art)
                art_copy["title"] = new_title
                art_copy["summary"] = new_summary
                art_copy["detailed_summary"] = new_summary
                art_copy["executive_summary"] = new_summary
                results.append(art_copy)
            else:
                art_copy = art.model_copy(update={
                    "title": new_title,
                    "detailed_summary": new_summary,
                    "executive_summary": new_summary
                })
                results.append(art_copy)

        return results
    finally:
        conn.close()

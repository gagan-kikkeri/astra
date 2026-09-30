"""
Unified LLM Gateway for ASTRA SENTINEL.
Provides resilient dual-agent execution and seamless 429 quota fallback:
1. Primary Provider: Google Gemini 2.5 Flash (via official google-genai SDK)
2. Secondary Provider: Standard OpenAI-compatible API (OpenAI, Groq, OpenRouter) via HTTP
3. Tertiary Provider: Deterministic local rule-based intelligence
"""

import json
import logging
import re
import urllib.request
import urllib.error
from typing import Optional, Dict, Any, List

from app.config import settings, logger

# Google GenAI SDK import
try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False


def _get_gemini_client():
    if not settings.has_gemini_key or not GENAI_AVAILABLE:
        return None
    try:
        return genai.Client(api_key=settings.GEMINI_API_KEY)
    except Exception as e:
        logger.warning(f"[LLM GATEWAY] Gemini client init failed: {e}")
        return None


def _is_quota_exhausted(error: Exception) -> bool:
    """Detects HTTP 429 or Resource Exhausted quota errors."""
    msg = str(error).lower()
    return "429" in msg or "resource_exhausted" in msg or "quota" in msg or "rate limit" in msg


def call_openai_compatible(
    system_prompt: str,
    user_prompt: str,
    response_json: bool = True,
    timeout: float = 12.0
) -> Optional[str]:
    """
    Executes a chat completion request to standard OpenAI/Groq/OpenRouter compatible endpoints.
    Uses native urllib for zero extra dependency overhead.
    """
    if not settings.has_openai_key:
        return None

    base_url = settings.OPENAI_BASE_URL.rstrip("/")
    endpoint = f"{base_url}/chat/completions"

    headers = {
        "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
        "Content-Type": "application/json",
        "User-Agent": "ASTRA-SENTINEL/2.5"
    }

    payload: Dict[str, Any] = {
        "model": settings.OPENAI_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.1
    }

    if response_json:
        payload["response_format"] = {"type": "json_object"}

    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(endpoint, data=data_bytes, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            result = json.loads(body)
            choices = result.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "").strip()
            return None
    except Exception as e:
        logger.warning(f"[LLM GATEWAY] OpenAI provider request failed ({endpoint}): {e}")
        return None


def call_llm_json(
    system_prompt: str,
    user_prompt: str,
    schema_model: Optional[Any] = None,
    timeout: float = 12.0
) -> Optional[Dict[str, Any]]:
    """
    Multi-provider JSON intelligence synthesis.
    Tries Gemini first; if 429 or offline, seamlessly tries OpenAI/Groq/OpenRouter.
    Returns parsed dictionary or None for local rule-based fallback.
    """
    # 1. Try Gemini
    gemini_client = _get_gemini_client()
    if gemini_client:
        try:
            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1
            )
            if schema_model:
                config.response_schema = schema_model

            response = gemini_client.models.generate_content(
                model=settings.MODEL_NAME,
                contents=[system_prompt, user_prompt],
                config=config
            )
            if getattr(response, "parsed", None):
                if hasattr(response.parsed, "model_dump"):
                    return response.parsed.model_dump()
                return response.parsed
            if response.text:
                raw_text = response.text.strip()
                if raw_text.startswith("```"):
                    raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
                    raw_text = re.sub(r"\s*```$", "", raw_text)
                return json.loads(raw_text)
        except Exception as e:
            if _is_quota_exhausted(e):
                logger.warning(f"[LLM GATEWAY] Gemini quota exhausted (HTTP 429). Activating secondary provider fallback: {e}")
            else:
                logger.warning(f"[LLM GATEWAY] Gemini JSON call failed: {e}")

    # 2. Try Secondary OpenAI / Groq / OpenRouter provider
    if settings.has_openai_key:
        logger.info(f"[LLM GATEWAY] Routing intelligence task to secondary provider ({settings.OPENAI_MODEL})...")
        content = call_openai_compatible(system_prompt, user_prompt, response_json=True, timeout=timeout)
        if content:
            try:
                raw_text = content.strip()
                if raw_text.startswith("```"):
                    raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
                    raw_text = re.sub(r"\s*```$", "", raw_text)
                return json.loads(raw_text)
            except Exception as e:
                logger.warning(f"[LLM GATEWAY] Failed to parse JSON from secondary provider: {e}")

    # 3. Both external providers offline/exhausted -> return None for deterministic fallback
    return None


def call_llm_text(
    system_prompt: str,
    user_prompt: str,
    timeout: float = 12.0
) -> Optional[str]:
    """
    Multi-provider text completion.
    Tries Gemini first, then OpenAI/Groq/OpenRouter, then returns None.
    """
    # 1. Try Gemini
    gemini_client = _get_gemini_client()
    if gemini_client:
        try:
            response = gemini_client.models.generate_content(
                model=settings.MODEL_NAME,
                contents=[system_prompt, user_prompt]
            )
            if response.text:
                return response.text.strip()
        except Exception as e:
            if _is_quota_exhausted(e):
                logger.warning(f"[LLM GATEWAY] Gemini quota exhausted (HTTP 429). Activating secondary provider fallback: {e}")
            else:
                logger.warning(f"[LLM GATEWAY] Gemini text call failed: {e}")

    # 2. Try Secondary OpenAI Provider
    if settings.has_openai_key:
        logger.info(f"[LLM GATEWAY] Routing text task to secondary provider ({settings.OPENAI_MODEL})...")
        return call_openai_compatible(system_prompt, user_prompt, response_json=False, timeout=timeout)

    return None

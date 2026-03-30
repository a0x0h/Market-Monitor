import json
import logging
import asyncio
import re
import time
from typing import Optional

import google.generativeai as genai
from groq import AsyncGroq

from config import Config

logger = logging.getLogger(__name__)

# ── Groq fallback model chain (tried in order on failure/rate-limit) ─────────
_GROQ_MODELS = [
    "llama-3.3-70b-versatile",   # best quality
    "mixtral-8x7b-32768",        # good, different architecture
    "llama-3.1-8b-instant",      # smaller, higher rate limits
    "gemma2-9b-it",              # last resort
]

# ── Gemini model chain (each has its own free-tier RPD quota) ─────────────────
_GEMINI_MODELS = [
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
]

# Per-model quota-exceeded timestamps (epoch seconds); 0 = available
_gemini_quota_exceeded_until: dict[str, float] = {}

# ── System prompt ─────────────────────────────────────────────────────────────
ANALYSIS_PROMPT = """
You are an expert geopolitical and commodity market analyst and a native professional Persian (Farsi) translator.
Given a tweet, return ONLY a valid JSON object — no markdown, no explanation.

JSON schema:
{
  "translation": "<Accurate, fluent, and highly professional Persian translation of the tweet>",
  "analysis": "<2-3 sentence analysis in Persian. MUST BE EMPTY STRING (\"\") if the tweet has NO DIRECT IMPACT on Iran or the oil market>",
  "oil_sentiment": "BULLISH" | "BEARISH" | "NEUTRAL",
  "sentiment_emoji": "🟢" | "🔴" | "⬜",
  "urgency": "BREAKING" | "HIGH" | "NORMAL",
  "urgency_emoji": "🚨" | "⚡" | "📌",
  "keywords": ["keyword1", "keyword2"]
}

ABSOLUTE LANGUAGE RULES — NO EXCEPTIONS:
- The "translation" and "analysis" fields must contain ONLY Persian (Farsi) script (ا ب پ ت ث ج چ ح خ ...).
- STRICTLY FORBIDDEN in these fields: Chinese (中文/漢字), Japanese (かな/カナ), Korean (한글), Russian/Cyrillic (кириллица), or ANY other non-Persian script.
- If the tweet contains words written in Chinese, Japanese, Korean, or Russian script, TRANSLITERATE them into Persian phonetics. Example: "习近平" → "شی جین‌پینگ", "Путин" → "پوتین".
- Allowed characters: Persian/Arabic letters, spaces, Persian punctuation، ؟ ؛, numbers (0–9 or ۰–۹), and standard punctuation (. , ! ? - ( )).
- Any violation of the above rules is a critical error. Re-check your output before responding.

Additional rules:
- The translation must be perfectly natural, accurate, and completely avoid robotic or machine-translation artifacts.
- If providing an "analysis", it must explain the real impact on oil prices or regional stability and Iran. If trivial or unrelated, return an empty string "" for "analysis".
- sentiment_emoji: use 🟢 if the tweet implies oil prices will go UP, 🔴 if prices will go DOWN, and ⬜ if NEUTRAL.
- urgency BREAKING = direct military action / ceasefire / sanctions announcement
- urgency HIGH = political statements with clear market implications
- urgency NORMAL = routine updates / analysis
"""

# ── Regex to detect forbidden scripts in Persian-only fields ─────────────────
_FORBIDDEN_SCRIPT_RE = re.compile(
    "["
    "\u4e00-\u9fff"    # CJK Unified Ideographs
    "\u3400-\u4dbf"    # CJK Extension A
    "\u3000-\u303f"    # CJK Symbols & Punctuation
    "\u3040-\u30ff"    # Hiragana + Katakana
    "\uac00-\ud7af"    # Korean Hangul
    "\u0400-\u04ff"    # Cyrillic
    "\uff00-\uffef"    # Fullwidth Latin / Halfwidth Katakana
    "]+"
)


def _strip_forbidden_scripts(text: str) -> str:
    """Remove any CJK / Cyrillic / non-Persian characters that leaked through."""
    return _FORBIDDEN_SCRIPT_RE.sub("", text).strip()


def _clean_text_fields(result: dict) -> dict:
    """Post-process AI result: strip forbidden scripts from translation/analysis."""
    for field in ("translation", "analysis"):
        if isinstance(result.get(field), str):
            cleaned = _strip_forbidden_scripts(result[field])
            if cleaned != result[field]:
                logger.warning(
                    f"Stripped forbidden characters from '{field}': "
                    f"{repr(result[field][:80])} → {repr(cleaned[:80])}"
                )
            result[field] = cleaned
    return result


# ── Gemini setup ──────────────────────────────────────────────────────────────
if Config.GEMINI_API_KEY:
    genai.configure(api_key=Config.GEMINI_API_KEY)

# ── Groq setup ────────────────────────────────────────────────────────────────
_groq_client = AsyncGroq(api_key=Config.GROQ_API_KEY) if Config.GROQ_API_KEY else None

_DEFAULT_RESULT = {
    "translation": "ترجمه در دسترس نیست",
    "analysis": "تحلیل در دسترس نیست",
    "oil_sentiment": "NEUTRAL",
    "sentiment_emoji": "⬜",
    "urgency": "NORMAL",
    "urgency_emoji": "📌",
    "keywords": [],
}


def _parse_json_response(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 1)[-1]
        if raw.startswith("json"):
            raw = raw[4:].strip()
        if "```" in raw:
            raw = raw.split("```")[0].strip()

    start_idx = raw.find("{")
    end_idx = raw.rfind("}")
    if start_idx != -1 and end_idx != -1:
        raw = raw[start_idx : end_idx + 1]

    try:
        return json.loads(raw)
    except json.JSONDecodeError as decode_error:
        logger.error(f"JSON Parsing failed on string: {repr(raw)}")
        raise decode_error


def _is_quota_error(e: Exception) -> bool:
    err = str(e).lower()
    return any(kw in err for kw in ("quota", "429", "resource exhausted", "rate limit"))


def _is_not_found_error(e: Exception) -> bool:
    err = str(e).lower()
    return "404" in err or "not found" in err


async def _analyze_with_gemini(tweet_text: str) -> Optional[dict]:
    if not Config.GEMINI_API_KEY:
        return None
    now = time.time()
    loop = asyncio.get_event_loop()
    for model_name in _GEMINI_MODELS:
        if now < _gemini_quota_exceeded_until.get(model_name, 0):
            logger.debug(f"Skipping Gemini {model_name}: unavailable")
            continue
        try:
            model = genai.GenerativeModel(
                model_name=model_name,
                generation_config={"temperature": 0.3, "max_output_tokens": 512},
                system_instruction=ANALYSIS_PROMPT,
            )
            response = await loop.run_in_executor(
                None,
                lambda m=model: m.generate_content(f"Tweet:\n{tweet_text}"),
            )
            result = _parse_json_response(response.text)
            logger.debug(f"Used Gemini model {model_name}")
            return result
        except Exception as e:
            if _is_quota_error(e):
                _gemini_quota_exceeded_until[model_name] = now + 23 * 3600
                logger.warning(f"Gemini {model_name} quota exhausted, skipping for 23h")
                continue
            if _is_not_found_error(e):
                _gemini_quota_exceeded_until[model_name] = now + 7 * 24 * 3600
                logger.warning(f"Gemini {model_name} not available on this account, skipping for 7d")
                continue
            logger.warning(f"Gemini {model_name} failed: {e}")
            return None  # Auth / content-filter errors — don't try other models
    return None


async def _groq_complete(
    messages: list[dict],
    temperature: float = 0.3,
    max_tokens: int = 512,
) -> Optional[str]:
    """Try all Groq models in order; return the first successful raw text."""
    if not _groq_client:
        return None
    for model in _GROQ_MODELS:
        try:
            chat = await _groq_client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            logger.debug(f"Used Groq model {model}")
            return chat.choices[0].message.content
        except Exception as e:
            logger.warning(f"Groq model {model} failed: {e}")
            continue
    return None


async def _analyze_with_groq(tweet_text: str) -> Optional[dict]:
    if not _groq_client:
        return None
    raw = await _groq_complete(
        messages=[
            {"role": "system", "content": ANALYSIS_PROMPT},
            {"role": "user", "content": f"Tweet:\n{tweet_text}"},
        ],
        temperature=0.3,
        max_tokens=512,
    )
    if raw is None:
        return None
    try:
        return _parse_json_response(raw)
    except Exception as e:
        logger.warning(f"Groq JSON parse failed: {e}")
        return None


async def analyze_tweet(tweet_text: str) -> dict:
    """
    Primary:    Gemini 2.0 Flash → 1.5 Flash → 1.5 Flash-8B (per-model quota tracking)
    Fallback:   Groq llama-3.3-70b → mixtral-8x7b → llama-3.1-8b → gemma2-9b
    Last resort: default neutral result
    """
    result = await _analyze_with_gemini(tweet_text)
    if result:
        return _clean_text_fields(result)

    result = await _analyze_with_groq(tweet_text)
    if result:
        return _clean_text_fields(result)

    logger.warning("Both AI providers failed, using default result")
    return _DEFAULT_RESULT.copy()


async def generate_plain_text(prompt: str) -> Optional[str]:
    """Plain-text Gemini call (no tweet-analysis system prompt), Groq fallback."""
    if Config.GEMINI_API_KEY:
        now = time.time()
        loop = asyncio.get_event_loop()
        for model_name in _GEMINI_MODELS:
            if now < _gemini_quota_exceeded_until.get(model_name, 0):
                continue
            try:
                model = genai.GenerativeModel(
                    model_name=model_name,
                    generation_config={"temperature": 0.4, "max_output_tokens": 600},
                )
                response = await loop.run_in_executor(
                    None, lambda m=model: m.generate_content(prompt)
                )
                return response.text.strip()
            except Exception as e:
                if _is_quota_error(e):
                    _gemini_quota_exceeded_until[model_name] = now + 23 * 3600
                    logger.warning(f"Gemini {model_name} quota exhausted (plain-text), skipping 23h")
                    continue
                if _is_not_found_error(e):
                    _gemini_quota_exceeded_until[model_name] = now + 7 * 24 * 3600
                    logger.warning(f"Gemini {model_name} not available on this account, skipping 7d")
                    continue
                logger.warning(f"Gemini {model_name} plain-text failed: {e}")
                return None

    # Groq fallback
    raw = await _groq_complete(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
        max_tokens=600,
    )
    return raw.strip() if raw else None


async def summarize_daily(events: list[str]) -> str:
    """Generate a daily summary of all events in Persian."""
    if not events:
        return "رویداد مهمی در ۲۴ ساعت گذشته ثبت نشد."

    events_text = "\n".join(f"- {e}" for e in events[-50:])
    prompt = f"""این رویدادهای ۲۴ ساعت گذشته است:
{events_text}

یک خلاصه جامع ۵-۷ جمله‌ای به فارسی بنویس که:
۱. مهم‌ترین تحولات را پوشش دهد
۲. تأثیر بر قیمت نفت را بررسی کند
۳. چشم‌انداز روز آینده را بدهد
فقط متن خلاصه را بنویس، بدون هیچ توضیح اضافی."""

    text = await generate_plain_text(prompt)
    if text:
        return text

    return "خلاصه‌سازی روزانه در دسترس نیست."

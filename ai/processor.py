import json
import logging
import asyncio
from typing import Optional

import google.generativeai as genai
from groq import AsyncGroq

from config import Config

logger = logging.getLogger(__name__)

# ── System prompt ─────────────────────────────────────────────────────────────
ANALYSIS_PROMPT = """
You are an expert geopolitical and commodity market analyst.
Given a tweet, return ONLY a valid JSON object — no markdown, no explanation.

JSON schema:
{
  "translation": "<Persian translation of the tweet>",
  "analysis": "<2-3 sentence analysis in Persian about geopolitical/market impact>",
  "oil_sentiment": "BULLISH" | "BEARISH" | "NEUTRAL",
  "sentiment_emoji": "🟢" | "🔴" | "⬜",
  "urgency": "BREAKING" | "HIGH" | "NORMAL",
  "urgency_emoji": "🚨" | "⚡" | "📌",
  "keywords": ["keyword1", "keyword2"]
}

Rules:
- translation and analysis must be in Persian (Farsi)
- analysis must explain impact on oil prices or regional stability
- urgency BREAKING = direct military action / ceasefire / sanctions announcement
- urgency HIGH = political statements with clear market implications
- urgency NORMAL = routine updates / analysis
"""

# ── Gemini setup ─────────────────────────────────────────────────────────────
if Config.GEMINI_API_KEY:
    genai.configure(api_key=Config.GEMINI_API_KEY)
    _gemini_model = genai.GenerativeModel(
        model_name="gemini-2.0-flash",
        generation_config={"temperature": 0.3, "max_output_tokens": 512},
        system_instruction=ANALYSIS_PROMPT,
    )
else:
    _gemini_model = None

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
        # Remove the first ``` block marker entirely
        raw = raw.split("```", 1)[-1]
        
        # Remove optional 'json' tag at start
        if raw.startswith("json"):
            raw = raw[4:].strip()
            
        # If there's a closing ```, remove it
        if "```" in raw:
            raw = raw.split("```")[0].strip()

    # Sometimes LLMs don't escape double quotes properly inside translation arrays,
    # or they accidentally insert conversational text outside the JSON boundaries.
    # We will try to find the strict indices for {...} bounds.
    start_idx = raw.find("{")
    end_idx = raw.rfind("}")
    
    if start_idx != -1 and end_idx != -1:
        raw = raw[start_idx : end_idx + 1]

    try:
        return json.loads(raw)
    except json.JSONDecodeError as decode_error:
        # If there's an emergency parsing failure, log the raw payload for debugging
        logger.error(f"JSON Parsing failed on string: {repr(raw)}")
        raise decode_error


async def _analyze_with_gemini(tweet_text: str) -> Optional[dict]:
    if not _gemini_model:
        return None
    try:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: _gemini_model.generate_content(
                f"Tweet:\n{tweet_text}"
            ),
        )
        return _parse_json_response(response.text)
    except Exception as e:
        logger.warning(f"Gemini failed: {e}")
        return None


async def _analyze_with_groq(tweet_text: str) -> Optional[dict]:
    if not _groq_client:
        return None
    try:
        chat = await _groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": ANALYSIS_PROMPT},
                {"role": "user",   "content": f"Tweet:\n{tweet_text}"},
            ],
            temperature=0.3,
            max_tokens=512,
        )
        return _parse_json_response(chat.choices[0].message.content)
    except Exception as e:
        logger.warning(f"Groq failed: {e}")
        return None


async def analyze_tweet(tweet_text: str) -> dict:
    """
    Primary: Gemini 1.5 Flash
    Fallback: Groq Llama 3.3
    Last resort: default neutral result
    """
    result = await _analyze_with_gemini(tweet_text)
    if result:
        logger.debug("Used Gemini for analysis")
        return result

    result = await _analyze_with_groq(tweet_text)
    if result:
        logger.debug("Used Groq as fallback")
        return result

    logger.warning("Both AI providers failed, using default result")
    return _DEFAULT_RESULT.copy()


async def generate_plain_text(prompt: str) -> str | None:
    """Public: plain-text Gemini call (no tweet-analysis system prompt)."""
    """Call Gemini without the tweet-analysis system instruction for free-form text."""
    if not Config.GEMINI_API_KEY:
        return None
    try:
        loop = asyncio.get_event_loop()
        model = genai.GenerativeModel(
            model_name="gemini-2.0-flash",
            generation_config={"temperature": 0.4, "max_output_tokens": 600},
        )
        response = await loop.run_in_executor(None, lambda: model.generate_content(prompt))
        return response.text.strip()
    except Exception as e:
        logger.warning(f"Gemini plain-text generation failed: {e}")
        return None


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

    if _groq_client:
        try:
            chat = await _groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4,
                max_tokens=600,
            )
            return chat.choices[0].message.content.strip()
        except Exception:
            pass

    return "خلاصه‌سازی روزانه در دسترس نیست."

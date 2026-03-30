import json
import os
import logging
from datetime import datetime, timezone

import pytz

logger = logging.getLogger(__name__)

SEEN_TWEETS_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "seen_tweets.json")
LAST_PRICES_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "last_prices.json")
SEEN_NEWS_FILE   = os.path.join(os.path.dirname(__file__), "..", "data", "seen_news.json")


# ── Seen tweets persistence ───────────────────────────────────────────────────

def load_seen_tweets() -> set[str]:
    try:
        with open(SEEN_TWEETS_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_seen_tweets(seen: set[str]) -> None:
    os.makedirs(os.path.dirname(SEEN_TWEETS_FILE), exist_ok=True)
    # Merge with on-disk state so concurrent monitors don't overwrite each other
    existing = load_seen_tweets()
    merged = existing | seen
    trimmed = list(merged)[-2000:]
    with open(SEEN_TWEETS_FILE, "w", encoding="utf-8") as f:
        json.dump(trimmed, f)


# ── Price history persistence ─────────────────────────────────────────────────

def load_last_prices() -> dict:
    try:
        with open(LAST_PRICES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_last_prices(prices: dict) -> None:
    os.makedirs(os.path.dirname(LAST_PRICES_FILE), exist_ok=True)
    with open(LAST_PRICES_FILE, "w", encoding="utf-8") as f:
        json.dump(prices, f)


# ── Formatting helpers ────────────────────────────────────────────────────────

def fmt_price(value: float, unit: str = "$") -> str:
    if unit == "$":
        return f"${value:,.2f}"
    return f"{value:,.2f}"


def pct_arrow(pct: float) -> str:
    if pct > 0:
        return f"🟢 +{pct:.2f}%"
    elif pct < 0:
        return f"🔴 {pct:.2f}%"
    return f"⬜ {pct:.2f}%"


def utc_now_str() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%H:%M UTC  |  %d %b %Y")


def relative_time(dt: datetime) -> str:
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    diff = now - dt
    seconds = int(diff.total_seconds())
    if seconds < 60:
        return f"{seconds} ثانیه پیش"
    elif seconds < 3600:
        return f"{seconds // 60} دقیقه پیش"
    elif seconds < 86400:
        return f"{seconds // 3600} ساعت پیش"
    return f"{seconds // 86400} روز پیش"


def load_seen_news() -> set[str]:
    try:
        with open(SEEN_NEWS_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_seen_news(seen: set[str]) -> None:
    os.makedirs(os.path.dirname(SEEN_NEWS_FILE), exist_ok=True)
    existing = load_seen_news()
    merged   = existing | seen
    with open(SEEN_NEWS_FILE, "w", encoding="utf-8") as f:
        json.dump(list(merged)[-3000:], f)


def tehran_now_str() -> str:
    tz = pytz.timezone("Asia/Tehran")
    now = datetime.now(tz)
    return now.strftime("%H:%M  |  %d %b %Y (IRST)")


def escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )

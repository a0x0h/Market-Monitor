import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # ── Telegram ─────────────────────────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHANNEL_ID: str = os.getenv("TELEGRAM_CHANNEL_ID", "")
    TELEGRAM_CHANNEL: str = os.getenv("TELEGRAM_CHANNEL", "")  # e.g. @mychannel, optional if ID is provided (without @)

    # ── Bale Messenger ────────────────────────────────────────────────────────
    BALE_BOT_TOKEN: str = os.getenv("BALE_BOT_TOKEN", "")
    BALE_CHANNEL_ID: str = os.getenv("BALE_CHANNEL_ID", "")
    BALE_CHANNEL: str = os.getenv("BALE_CHANNEL", "")  # channel username for ble.ir deep links (without @)

    # ── AI Keys ───────────────────────────────────────────────────────────────
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")

    # ── TruthSocial OAuth ─────────────────────────────────────────────────────
    TRUTHSOCIAL_ACCESS_TOKEN: str = os.getenv("TRUTHSOCIAL_ACCESS_TOKEN", "")

    # ── Intervals ─────────────────────────────────────────────────────────────
    PRICE_INTERVAL_MINUTES: int = 5
    TWITTER_POLL_INTERVAL_MINUTES: int = 5
    TWITTER_FAST_INTERVAL_SECONDS: int = 20      # tier-1 fast poll
    TRUTHSOCIAL_FAST_INTERVAL_SECONDS: int = 60
    NEWS_POLL_INTERVAL_MINUTES: int = 10
    TEHRAN_TIMEZONE: str = "Asia/Tehran"

    # ── Nitter instances (fallback chain) ────────────────────────────────────
    NITTER_INSTANCES: list[str] = [
        "https://nitter.privacydev.net",
        "https://nitter.poast.org",
        "https://nitter.net",
        "https://nitter.1d4.us",
        "https://nitter.kavin.rocks",
    ]

    # ── Price tickers (Yahoo Finance symbols) ────────────────────────────────
    PRICE_TICKERS: dict[str, dict] = {
        "CL=F":      {"name": "WTI Crude",    "emoji": "🛢",  "unit": "$"},
        "BZ=F":      {"name": "Brent Crude",  "emoji": "🛢",  "unit": "$"},
        "GC=F":      {"name": "Gold",          "emoji": "🥇",  "unit": "$"},
        "SI=F":      {"name": "Silver",        "emoji": "🥈",  "unit": "$"},
        "USDT-IRT":  {"name": "Tether",        "emoji": "🪙", "unit": "TMN"},
        "DX-Y.NYB":  {"name": "DXY",           "emoji": "💵",  "unit": ""},
        "NG=F":      {"name": "Nat Gas",        "emoji": "⚡",  "unit": "$"},
    }

    # ── 20 monitored Twitter accounts ────────────────────────────────────────
    MONITORED_ACCOUNTS: list[dict] = [
        {"username": "realDonaldTrump", "name": "Donald Trump", "priority": 1,  "category": "Politics", "credibility": "✅"},
    ]

    # ── Top Truth Social accounts to monitor ─────────────────────────────────
    TRUTHSOCIAL_ACCOUNTS: list[dict] = [
        {"username": "realDonaldTrump", "name": "Donald Trump", "priority": 1},
    ]

    # ── Tier 1 accounts (fast-polled every 20 s) ─────────────────────────────
    TIER1_ACCOUNTS: list[str] = [
        "realDonaldTrump"
    ]

    # ── RSS news feeds ────────────────────────────────────────────────────────
    RSS_FEEDS: list[dict] = []

    # ── Credibility legend ───────────────────────────────────────────────────
    CREDIBILITY_LABELS: dict[str, str] = {
        "✅":              "منبع معتبر",
        "🟡":              "منبع نیمه‌معتبر",
        "🔴 State Media":  "رسانه دولتی",
        "⚠️":             "منبع تأییدنشده",
    }

    # ── Alert thresholds ─────────────────────────────────────────────────────
    PRICE_ALERT_PCT: float = 1.5    # alert if price moves > 1.5% in one cycle
    HIGH_PRIORITY_ACCOUNTS: list[str] = [
        "realDonaldTrump"
    ]

"""
ai/market_analysis.py — Layer 3: Morning & evening market analysis.

Pulls recent events from the EventStore, formats a context prompt,
and sends a structured Persian analysis focused on oil markets.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytz

from ai.processor import generate_plain_text
from bot.sender import TelegramSender
from core.event_store import event_store, NewsEvent
from monitors.prices import fetch_all_prices
from utils.helpers import fmt_price, pct_arrow

logger = logging.getLogger(__name__)

TEHRAN = pytz.timezone("Asia/Tehran")


def _format_events(events: list[NewsEvent]) -> str:
    if not events:
        return "رویدادی ثبت نشده است."
    lines = []
    for e in events[-60:]:
        if e.source == "Price":
            continue
        lines.append(
            f"[{e.source}] {e.headline[:150]} | تأثیر نفت: {e.oil_sentiment}"
        )
    return "\n".join(lines) if lines else "رویداد قابل توجهی ثبت نشده است."


def _format_prices(prices: dict) -> str:
    from config import Config
    parts = []
    for sym, d in prices.items():
        meta = Config.PRICE_TICKERS.get(sym, {})
        parts.append(
            f"{meta.get('name', sym)}: {fmt_price(d['price'], meta.get('unit','$'))} "
            f"({d['pct_change']:+.2f}%)"
        )
    return " | ".join(parts) if parts else "قیمت‌ها در دسترس نیست"


async def _ai_analyze(prompt: str) -> str:
    result = await generate_plain_text(prompt)
    if result:
        return result
    return "تحلیل در این لحظه در دسترس نیست."


async def send_morning_analysis(sender: TelegramSender) -> None:
    """7:00 AM Tehran — overnight brief + outlook."""
    try:
        events = event_store.get_recent(hours=10)
        prices = await fetch_all_prices()
        now_ir = datetime.now(TEHRAN).strftime("%H:%M  |  %d %b %Y")

        prompt = f"""تحلیلگر ارشد بازارهای انرژی هستی.

قیمت‌های فعلی:
{_format_prices(prices)}

رویدادهای ۱۰ ساعت اخیر:
{_format_events(events)}

یک تحلیل صبحگاهی ۵ تا ۷ جمله‌ای به فارسی بنویس که:
۱. مهم‌ترین تحولات شب گذشته را بررسی کند
۲. تأثیر مستقیم هر رویداد بر قیمت نفت خام را توضیح دهد
۳. چشم‌انداز امروز بازار انرژی را ارائه دهد
۴. ریسک‌های اصلی روز را نام ببرد

فقط متن تحلیل را بنویس، هیچ عنوان یا توضیح اضافی نده."""

        analysis = await _ai_analyze(prompt)

        msg = (
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🌅  <b>تحلیل صبح بازار</b>  |  {now_ir}\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{analysis}\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "#تحلیل #صبح #نفت #بازار"
        )
        await sender.send_text(msg)
    except Exception as exc:
        logger.error("Morning analysis failed: %s", exc, exc_info=True)


async def send_evening_analysis(sender: TelegramSender) -> None:
    """9:00 PM Tehran — full-day recap + tomorrow outlook."""
    try:
        events = event_store.get_recent(hours=14)
        prices = await fetch_all_prices()
        now_ir = datetime.now(TEHRAN).strftime("%H:%M  |  %d %b %Y")

        prompt = f"""تحلیلگر ارشد بازارهای انرژی هستی.

قیمت‌های پایانی روز:
{_format_prices(prices)}

رویدادهای ۱۴ ساعت اخیر:
{_format_events(events)}

یک جمع‌بندی عصرگاهی ۵ تا ۷ جمله‌ای به فارسی بنویس که:
۱. مهم‌ترین رویدادهای امروز را دسته‌بندی کند
۲. تحلیل تکنیکال کوتاه از قیمت نفت بدهد (حمایت/مقاومت)
۳. تأثیر رویدادهای جیوپلیتیکی روز بر نفت را ارزیابی کند
۴. چشم‌انداز فردا و ریسک‌های پیش‌رو را بگوید

فقط متن تحلیل را بنویس، هیچ عنوان یا توضیح اضافی نده."""

        analysis = await _ai_analyze(prompt)

        msg = (
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🌙  <b>تحلیل عصر بازار</b>  |  {now_ir}\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{analysis}\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "#تحلیل #شب #نفت #بازار"
        )
        await sender.send_text(msg)
    except Exception as exc:
        logger.error("Evening analysis failed: %s", exc, exc_info=True)

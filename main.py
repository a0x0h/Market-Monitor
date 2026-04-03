import asyncio
import logging
import sys

import pytz
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ContentType
from aiogram.client.default import DefaultBotProperties
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import Config
from core.event_store import event_store
from monitors.twitter import TwitterMonitor
from monitors.truthsocial import TruthSocialMonitor
from monitors.prices import PriceMonitor
from ai.market_analysis import send_morning_analysis, send_evening_analysis
from bot.sender import TelegramSender, BaleSender, MultiSender
import utils.screenshot as screenshot_module

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")

TEHRAN = pytz.timezone(Config.TEHRAN_TIMEZONE)


async def on_startup(sender: TelegramSender) -> None:
    await sender.send_text(
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🟢  <b>Market Monitor Bot</b> Activated\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📡 Monitoring:\n"
        # f"• {len(Config.MONITORED_ACCOUNTS)} X Accounts  (Tier1 every {Config.TWITTER_FAST_INTERVAL_SECONDS}s)\n"
        f"• {len(Config.TRUTHSOCIAL_ACCOUNTS)} Truth Social Accounts  (every {Config.TRUTHSOCIAL_FAST_INTERVAL_SECONDS}s)\n"
        # f"• {len(Config.RSS_FEEDS)} News Feeds  (every {Config.NEWS_POLL_INTERVAL_MINUTES} minutes)\n"
        f"• {len(Config.PRICE_TICKERS)} Price Tickers  (every {Config.PRICE_INTERVAL_MINUTES} minutes)\n\n"
        # "🌅 Morning Analysis: 7:00 AM  |  🌙 Evening Analysis: 9:00 PM (Tehran)\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )


async def main() -> None:
    if not Config.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is missing. Check your .env file.")
        sys.exit(1)
    if not Config.TELEGRAM_CHANNEL_ID:
        logger.error("TELEGRAM_CHANNEL_ID is missing. Check your .env file.")
        sys.exit(1)

    bot = Bot(
        token=Config.TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    dp = Dispatcher()

    @dp.message(F.pinned_message)
    @dp.channel_post(F.pinned_message)
    @dp.message(F.content_type.in_({ContentType.PINNED_MESSAGE}))
    @dp.channel_post(F.content_type.in_({ContentType.PINNED_MESSAGE}))
    async def delete_pinned_system_message(message: Message):
        try:
            await message.delete()
            logger.info("Deleted pinned system message overlay successfully.")
        except Exception as e:
            logger.warning(f"Failed to delete pinned system message: {e}")

    sender = TelegramSender(bot, Config.TELEGRAM_CHANNEL_ID)

    bale_sender = None
    if Config.BALE_BOT_TOKEN and Config.BALE_CHANNEL_ID:
        bale_sender = BaleSender(Config.BALE_BOT_TOKEN, Config.BALE_CHANNEL_ID)
        logger.info("Bale sender initialized (channel %s)", Config.BALE_CHANNEL_ID)

    multi_sender = MultiSender([sender, bale_sender]) if bale_sender else sender

    truthsocial_monitor = TruthSocialMonitor(multi_sender)
    price_monitor = PriceMonitor(sender, bale_sender=bale_sender)

    scheduler = AsyncIOScheduler(timezone="UTC")

    # TruthSocial fast poll (30 s)
    scheduler.add_job(
        truthsocial_monitor.check_new_posts,
        "interval",
        seconds=Config.TRUTHSOCIAL_FAST_INTERVAL_SECONDS,
        id="truthsocial_poll",
        name="TruthSocial Poll",
    )

    # ── Layer 2: Price updates ────────────────────────────────────────────────

    # Price update every 5 min (text only)
    scheduler.add_job(
        price_monitor.send_price_update,
        "interval",
        minutes=Config.PRICE_INTERVAL_MINUTES,
        id="price_update",
        name="Price Update",
    )

    # Price update every 1 hour (with oilprice screenshot)
    async def hourly_price_update():
        await price_monitor.send_price_update(with_screenshot=True)

    scheduler.add_job(
        hourly_price_update,
        "interval",
        hours=1,
        id="hourly_price_update",
        name="Hourly Price Update with Screenshot",
    )

    # ── Layer 3: AI market analysis ───────────────────────────────────────────
    # TBD

    scheduler.start()
    logger.info("Scheduler started with %d jobs", len(scheduler.get_jobs()))

    await on_startup(sender)

    # Immediate first run of each layer
    await price_monitor.send_price_update()
    await truthsocial_monitor.check_new_posts()

    logger.info("Bot is running. Press Ctrl+C to stop.")

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler.shutdown(wait=False)
        await truthsocial_monitor.close()
        await screenshot_module.close()
        if bale_sender:
            await bale_sender.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

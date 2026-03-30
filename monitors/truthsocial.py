import asyncio
import logging
import aiohttp
from datetime import datetime
import pytz
import re

from ai.processor import analyze_tweet
from config import Config
from core.event_store import event_store, NewsEvent
from utils.helpers import (
    escape_html,
    load_seen_tweets,
    save_seen_tweets,
    tehran_now_str,
)
import utils.screenshot as screenshot_module
from bot.sender import TelegramSender

logger = logging.getLogger(__name__)

TRUMP_ACCOUNT_ID = "107780257626128497"
TRUMP_USERNAME = "realDonaldTrump"
TRUTHSOCIAL_API_URL = (
    f"https://truthsocial.com/api/v1/accounts/{TRUMP_ACCOUNT_ID}/statuses"
)


class TruthSocialMonitor:
    def __init__(self, sender: TelegramSender):
        self.sender = sender
        self.seen_tweets = load_seen_tweets()
        self.session = None

    async def close(self):
        if self.session:
            await self.session.close()

    async def check_new_posts(self):
        if not self.session:
            self.session = aiohttp.ClientSession()

        try:
            logger.info("Checking Truth Social API for new posts...")
            async with self.session.get(TRUTHSOCIAL_API_URL, timeout=15) as resp:
                if resp.status != 200:
                    logger.error(f"Failed to fetch truth social API: {resp.status}")
                    return
                tweets = await resp.json()

            new_tweets = []
            for tweet in tweets:
                if isinstance(tweet, dict) and tweet.get("id"):
                    account = tweet.get("account", {})
                    # Ensure it's original tweet by him
                    is_trump_account = (
                        account.get("username") == TRUMP_USERNAME
                        or account.get("id") == TRUMP_ACCOUNT_ID
                    )

                    if not is_trump_account:
                        continue

                    raw_content = tweet.get("content", "")
                    clean_text_pre = re.sub(r"<[^>]+>", " ", raw_content)

                    # only send if it's for him (include president, DJT, ...)
                    # we can filter out RTs unless they are from him
                    is_rt = clean_text_pre.strip().startswith("RT @")
                    signature_keywords = ["president", "djt", "donald"]
                    has_signature = any(
                        kw in clean_text_pre.lower() for kw in signature_keywords
                    )

                    if is_rt and not has_signature:
                        # Skip retweets that don't look like his own
                        continue

                    if tweet["id"] not in self.seen_tweets:
                        new_tweets.append(tweet)
                        self.seen_tweets.add(tweet["id"])

            if not new_tweets:
                return

            save_seen_tweets(self.seen_tweets)

            for tweet in reversed(new_tweets):
                await self._process_tweet(tweet)

        except Exception as e:
            logger.error(f"Error checking Truth Social API: {e}")

    async def _process_tweet(self, tweet: dict):
        tweet_id = tweet["id"]
        # extract content text removing html tags
        raw_content = tweet.get("content", "")
        # Very simple tag removal for basic text
        clean_text = re.sub(r"<[^>]+>", "\n", raw_content)
        # remove multiple newlines
        clean_text = re.sub(r"\n+", "\n", clean_text).strip()

        # Analyze and translate
        analysis = await analyze_tweet(clean_text)

        # Take screenshot
        logger.info(f"Taking screenshot for tweet {tweet_id}")
        ts_url = f"https://truthsocial.com/@realDonaldTrump/posts/{tweet_id}"
        screenshot_bytes = await screenshot_module.screenshot_truthsocial(ts_url)

        # formatting date
        created_at_str = tweet.get("created_at")
        if created_at_str:
            try:
                # "2026-03-30T02:29:30.926Z" -> datetime
                dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                tz = pytz.timezone("Asia/Tehran")
                dt_tehran = dt.astimezone(tz)
                date_str = dt_tehran.strftime("%H:%M  |  %d %b %Y (IRST)")
            except:
                date_str = tehran_now_str()
        else:
            date_str = tehran_now_str()

        urgency_emoji = analysis.get("urgency_emoji", "🚨")
        translation = analysis.get("translation", "ترجمه در دسترس نیست.")

        msg = (
            f"{urgency_emoji} <b>#New_Truth by Donald J. Trump</b>\n\n"
            f"<i>{escape_html(clean_text)}</i>\n\n"
            f"━━━━━\n"
            f"🇮🇷 {escape_html(translation)}\n\n"
            f"{date_str}\n"
        )

        event_store.add_event(
            NewsEvent(
                timestamp=datetime.now(),
                source="TruthSocial",
                title=clean_text[:50] + "...",
                text=f"Trump truth: {clean_text}",
                url=f"https://truthsocial.com/@realDonaldTrump/posts/{tweet_id}",
            )
        )

        try:
            if screenshot_bytes:
                # Check for Telegram caption length limit
                if len(msg) > 1024:
                    # Truncate clean text and re-assemble safely
                    msg = (
                        f"{urgency_emoji} <b>New Truth by Donald J. Trump</b>\n\n"
                        f"\n<i>{escape_html(clean_text[:300])}...</i>\n\n"
                        f"━━━━━\n"
                        f"🇮🇷\n{escape_html(translation[:300])}...\n\n"
                        f"{date_str}"
                    )

                await self.sender.send_photo(
                    screenshot_bytes,
                    msg,
                    pin=True
                )
            else:
                await self.sender.send_text(msg, pin=True)
        except Exception as e:
            logger.error(f"Error sending Truth Social post to Telegram: {e}")

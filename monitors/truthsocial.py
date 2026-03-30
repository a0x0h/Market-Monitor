import asyncio
import logging
import json
import os
import aiohttp
import aiofiles
from datetime import datetime, timezone, timedelta
import pytz
import re
from bs4 import BeautifulSoup

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

try:
    from botasaurus.browser import browser as boto_browser, Driver as BotoDriver
    _BOTASAURUS_AVAILABLE = True
except ImportError:
    _BOTASAURUS_AVAILABLE = False

logger = logging.getLogger(__name__)

TRUMP_ACCOUNT_ID = "107780257626128497"
TRUMP_USERNAME = "realDonaldTrump"
TRUTHSOCIAL_API_URL = (
    f"https://truthsocial.com/api/v1/accounts/{TRUMP_ACCOUNT_ID}/statuses"
)

if _BOTASAURUS_AVAILABLE:
    @boto_browser(
        headless=True,
        add_arguments=["--no-sandbox", "--disable-dev-shm-usage"],
        output=None,
        reuse_driver=True, # Keep browser open to save resources
    )
    def fetch_truthsocial_api_sync(driver: BotoDriver, data: dict) -> dict | None:
        api_url = data.get("url")
        logger.info("Botasaurus fetching Truth Social API...")
        driver.get(api_url, bypass_cloudflare=True)
        driver.sleep(2)
        try:
            text = driver.run_js("return document.body.innerText;")
            cookies = driver.get_cookies_dict()
            return {"data": json.loads(text), "cookies": cookies}
        except Exception as e:
            logger.error(f"Failed to parse Botasaurus JSON: {e}")
            return None


class TruthSocialMonitor:
    def __init__(self, sender: TelegramSender):
        self.sender = sender
        self.seen_tweets = load_seen_tweets()

    async def close(self):
        pass

    async def check_new_posts(self):
        if not _BOTASAURUS_AVAILABLE:
            logger.error("Botasaurus not available. Cannot fetch Truth Social.")
            return

        try:
            logger.info("Checking Truth Social API for new posts using Botasaurus...")

            # Run the synchronous Botasaurus fetcher in a background thread
            result = await asyncio.to_thread(fetch_truthsocial_api_sync, {"url": TRUTHSOCIAL_API_URL})

            if not result or not isinstance(result, dict):
                logger.error("Failed to fetch truth social API via Botasaurus or invalid format.")
                return

            tweets = result.get("data", [])
            botasaurus_cookies = result.get("cookies", {})

            if not isinstance(tweets, list):
                logger.error("Failed to fetch truth social API via Botasaurus or invalid format for tweets.")
                return

            new_tweets = []
            now_utc = datetime.now(timezone.utc)

            for tweet in tweets:
                if isinstance(tweet, dict) and tweet.get("id"):
                    
                    # Check age (within 48 hours)
                    created_at_str = tweet.get("created_at")
                    if created_at_str:
                        try:
                            # Parse "2026-03-30T02:29:30.926Z" and remove milliseconds for safety or just parse
                            dt_str = created_at_str.replace("Z", "+00:00")
                            pub_dt = datetime.fromisoformat(dt_str)
                            if (now_utc - pub_dt) > timedelta(hours=48):
                                continue # Skip older posts
                        except Exception as e:
                            logger.error(f"Error parsing date {created_at_str}: {e}")
                            
                    account = tweet.get("account", {})
                    # Ensure it's original tweet by him
                    is_trump_account = (
                        account.get("username") == TRUMP_USERNAME
                        or account.get("id") == TRUMP_ACCOUNT_ID
                    )

                    if not is_trump_account:
                        continue

                    raw_content = tweet.get("content", "")
                    
                    soup = BeautifulSoup(raw_content, "html.parser")
                    clean_text_pre = soup.get_text(separator=" ").strip()

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
                await self._process_tweet(tweet, cookies=botasaurus_cookies)

        except Exception as e:
            logger.error(f"Error checking Truth Social API: {e}")

    async def _process_tweet(self, tweet: dict, cookies: dict = None):
        tweet_id = tweet["id"]
        # extract content text removing html tags
        raw_content = tweet.get("content", "")
        soup = BeautifulSoup(raw_content, "html.parser")
        for br in soup.find_all("br"):
            br.replace_with("\n")
        for p in soup.find_all("p"):
            p.insert_after("\n\n")

        clean_text = soup.get_text()
        # remove multiple newlines safely
        clean_text = re.sub(r"\n{3,}", "\n\n", clean_text).strip()

        # Check for media attachments (especially video)
        video_url = None
        media_attachments = tweet.get("media_attachments", [])
        for media in media_attachments:
            if media.get("type") == "video":
                video_url = media.get("url")
                break

        # Handle empty text with video attachment
        if not clean_text:
            if video_url:
                clean_text = "📹 [Video Attachment]"
            elif media_attachments:
                clean_text = "📸 [Image Attachment]"

        # Analyze and translate
        analysis = await analyze_tweet(clean_text)

        video_path = None
        screenshot_bytes = None

        if video_url:
            logger.info(f"Downloading video for tweet {tweet_id} from {video_url}")
            try:
                os.makedirs("media_downloads", exist_ok=True)
                download_path = f"media_downloads/ts_video_{tweet_id}.mp4"
                
                async with aiohttp.ClientSession(cookies=cookies) as session:
                    async with session.get(video_url) as resp:
                        if resp.status == 200:
                            async with aiofiles.open(download_path, 'wb') as f:
                                await f.write(await resp.read())
                            video_path = download_path
                        else:
                            logger.error(f"Failed to download video, HTTP {resp.status}")
            except Exception as e:
                logger.error(f"Failed to download video: {e}")
                
        # Always take screenshot
        logger.info(f"Taking screenshot for tweet {tweet_id}")
        ts_url = f"https://truthsocial.com/@realDonaldTrump/posts/{tweet_id}"
        screenshot_bytes = await screenshot_module.screenshot_truthsocial(ts_url)

        # formatting date
        created_at_str = tweet.get("created_at")
        pub_dt = datetime.now(timezone.utc)
        if created_at_str:
            try:
                # "2026-03-30T02:29:30.926Z" -> datetime
                dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                pub_dt = dt
                tz = pytz.timezone("Asia/Tehran")
                dt_tehran = dt.astimezone(tz)
                date_str = dt_tehran.strftime("- %H:%M %d/%m/%Y (IR)")
            except:
                date_str = f"- {tehran_now_str()}"
        else:
            date_str = f"- {tehran_now_str()}"

        channel_tag = "📡 @MonitorIR"

        # Check if the text contains president-related keywords to decide pinning and analysis
        president_keywords = ["president", "djt", "donald", "trump"]
        should_pin = any(kw in clean_text.lower() for kw in president_keywords)

        urgency_emoji = analysis.get("urgency_emoji", "🚨")
        translation = analysis.get("translation", "ترجمه در دسترس نیست.")
        analysis_text = analysis.get("analysis", "")
        sentiment_emoji = analysis.get("sentiment_emoji", "⬜")

        # Include the oil sentiment and analysis block if it's considered a president tweet
        analysis_block = f"🛢️{sentiment_emoji} {escape_html(analysis_text)}\n\n" if should_pin and analysis_text else ""

        date_link = f"<a href='{ts_url}'>{date_str} {channel_tag}</a>"

        msg = (
            f"{urgency_emoji} <b>#New_Truth by <a href='{ts_url}'>Donald J. Trump</a></b>\n\n"
            f"<i>{escape_html(clean_text)}</i>\n\n"
            f"━━━━━\n"
            f"🇮🇷 {escape_html(translation)}\n\n"
            f"━━━━━\n"
            f"{analysis_block}"
            f"{date_link}"
        )

        event_store.append(
            NewsEvent(
                source="TruthSocial",
                source_tag="#TruthSocial",
                headline=clean_text[:300],
                oil_sentiment=analysis.get("oil_sentiment", "NEUTRAL"),
                urgency=analysis.get("urgency", "NORMAL"),
                keywords=analysis.get("keywords", []),
                username="realDonaldTrump",
                published_at=pub_dt
            )
        )

        try:
            # Check for Telegram caption length limit
            if len(msg) > 1024:
                msg = (
                    f"{urgency_emoji} <b><a href='{ts_url}'>#New_Truth by Donald J. Trump</a></b>\n\n"
                    f"🇮🇷 {escape_html(translation)}\n"
                    f"━━━━━\n"
                    f"{analysis_block}"
                    f"{date_link}"
                )
            
            if screenshot_bytes and video_path:
                await self.sender.send_photo_and_video(
                    screenshot_bytes,
                    video_path,
                    msg,
                    pin=should_pin
                )
                # Cleanup downloaded video
                try:
                    os.remove(video_path)
                    logger.info(f"Cleaned up downloaded video {video_path}")
                except Exception as e:
                    logger.warning(f"Failed to clean up video file: {e}")
            elif screenshot_bytes:
                await self.sender.send_photo(
                    screenshot_bytes,
                    msg,
                    pin=should_pin
                )
            elif video_path:
                await self.sender.send_video(
                    video_path,
                    msg,
                    pin=should_pin
                )
                # Cleanup downloaded video
                try:
                    os.remove(video_path)
                    logger.info(f"Cleaned up downloaded video {video_path}")
                except Exception as e:
                    logger.warning(f"Failed to clean up video file: {e}")
            else:
                await self.sender.send_text(msg, pin=should_pin)

        except Exception as e:
            logger.error(f"Error sending Truth Social post to Telegram: {e}")

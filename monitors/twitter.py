import asyncio
import logging
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import aiohttp
import feedparser
from bs4 import BeautifulSoup

from config import Config
from ai.processor import analyze_tweet
from core.event_store import event_store, NewsEvent
from utils.helpers import load_seen_tweets, save_seen_tweets, escape_html
from utils.screenshot import screenshot_nitter
from utils.social_cards import render_x_card, fetch_avatar

logger = logging.getLogger(__name__)

ACCOUNT_MAP: dict[str, dict] = {
    acc["username"].lower(): acc
    for acc in Config.MONITORED_ACCOUNTS
}


class NitterFetcher:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.session = session
        self._working_instance: str | None = None

    async def _try_instance(self, instance: str, username: str) -> str | None:
        url = f"{instance}/{username}/rss"
        try:
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    return await resp.text()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            pass
        return None

    async def fetch_rss(self, username: str):
        instances = (
            [self._working_instance] + Config.NITTER_INSTANCES
            if self._working_instance
            else Config.NITTER_INSTANCES
        )
        for instance in instances:
            if not instance:
                continue
            raw = await self._try_instance(instance, username)
            if raw:
                self._working_instance = instance
                parsed = feedparser.parse(raw)
                if parsed.entries:
                    return parsed, instance
        return None, None

    async def download_bytes(self, url: str) -> bytes | None:
        try:
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status == 200:
                    return await resp.read()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.debug("Download failed %s: %s", url, exc)
        return None


def _extract_images(description_html: str, nitter_instance: str) -> list[str]:
    if not description_html:
        return []
    soup = BeautifulSoup(description_html, "html.parser")
    urls = []
    for img in soup.find_all("img"):
        src = img.get("src", "")
        if src.startswith("/pic/"):
            urls.append(f"{nitter_instance}{src}")
        elif src.startswith("http"):
            urls.append(src)
    return urls[:4]


def _clean_tweet_text(raw: str) -> str:
    return BeautifulSoup(raw, "html.parser").get_text(separator=" ").strip()


def _tweet_id_from_url(url: str) -> str:
    match = re.search(r"/status/(\d+)", url)
    return match.group(1) if match else url


def _original_tweet_url(nitter_url: str) -> str:
    return re.sub(r"https?://[^/]+/", "https://x.com/", nitter_url)


def build_tweet_caption(
    username: str,
    tweet_text: str,
    tweet_url: str,
    pub_date: datetime,
    ai_result: dict,
    should_pin: bool = False,
) -> str:
    urgency_emoji = ai_result.get("urgency_emoji", "📌")
    original_url = _original_tweet_url(tweet_url)
    translation = escape_html(ai_result.get("translation", "ترجمه در دسترس نیست"))
    clean_text = escape_html(tweet_text[:1200])
    date_time = pub_date.strftime("[ %H:%M - %d %b %Y UTC ]")

    analysis_text = ai_result.get("analysis", "")
    sentiment_emoji = ai_result.get("sentiment_emoji", "⬜")
    
    analysis_block = f"🛢️{sentiment_emoji} {escape_html(analysis_text)}\n\n" if should_pin and analysis_text else ""

    date_link = f"<a href='{original_url}'>{date_time}</a>"

    return (
        f"{urgency_emoji} <a href='{original_url}'>@{username}</a>\n"
        f"{clean_text}\n"
        f"━━\n"
        f"{translation}\n\n"
        f"━━\n"
        f"{analysis_block}\n"
        f"{date_link}\n\n"
        "#Twitter"
    )


class TwitterMonitor:
    def __init__(self, sender) -> None:
        self.sender = sender
        self._seen: set[str] = load_seen_tweets()
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()         # prevents concurrent runs / duplicate sends

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": "Mozilla/5.0 (compatible; RSSBot/1.0)"}
            )
        return self._session

    async def _process_account(self, account: dict, fetcher: NitterFetcher) -> None:
        username = account["username"]
        feed, instance = await fetcher.fetch_rss(username)
        if not feed or not feed.entries:
            return

        # Extract avatar URL from RSS channel image (same for all tweets)
        avatar_url: str | None = None
        feed_image = getattr(feed.feed, "image", None) or {}
        if isinstance(feed_image, dict):
            avatar_url = feed_image.get("href") or feed_image.get("url")

        # Collect new tweets (stop at first already-seen — feed is newest-first)
        new_entries = []
        for entry in feed.entries:
            tweet_id = _tweet_id_from_url(entry.get("link", ""))
            if tweet_id in self._seen:
                break
            new_entries.append((tweet_id, entry))

        # Send in chronological order
        for tweet_id, entry in reversed(new_entries):
            try:
                await self._send_tweet(account, entry, fetcher, instance, avatar_url)
                self._seen.add(tweet_id)
                await asyncio.sleep(1.5)
            except Exception as exc:
                logger.error("Failed tweet %s for @%s: %s", tweet_id, username, exc, exc_info=True)

        if new_entries:
            save_seen_tweets(self._seen)

    async def _send_tweet(
        self,
        account: dict,
        entry,
        fetcher: NitterFetcher,
        instance: str,
        avatar_url: str | None,
    ) -> None:
        description = entry.get("description", "") or entry.get("summary", "")
        tweet_text = _clean_tweet_text(description)
        tweet_url = entry.get("link", "")   # this is already a Nitter URL

        try:
            pub_date = parsedate_to_datetime(entry.get("published", ""))
            if pub_date.tzinfo is None:
                pub_date = pub_date.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            pub_date = datetime.now(timezone.utc)

        ai_result = await analyze_tweet(tweet_text)
        
        president_keywords = ["president", "djt", "donald", "trump"]
        should_pin = any(kw in tweet_text.lower() for kw in president_keywords) or account.get("username", "").lower() == "realdonaldtrump"

        caption = build_tweet_caption(
            account.get("username", "unknown"), tweet_text, tweet_url, pub_date, ai_result, should_pin
        )

        # ── Screenshot via Playwright (best quality, real browser render) ──────
        card_bytes: bytes | None = None
        video_url: str | None = None

        card_bytes, video_url = await screenshot_nitter(tweet_url)

        # ── PIL fallback if Playwright not available / failed ─────────────────
        if card_bytes is None:
            avatar_bytes: bytes | None = None
            if avatar_url:
                avatar_bytes = await fetch_avatar(fetcher.session, avatar_url,
                                                  account.get("username", ""))
            image_urls = _extract_images(description, instance)
            attached: list[bytes] = []
            for url in image_urls:
                img = await fetcher.download_bytes(url)
                if img:
                    attached.append(img)

            loop = asyncio.get_event_loop()
            card_bytes = await loop.run_in_executor(
                None,
                lambda: render_x_card(
                    username=account.get("username", "unknown"),
                    display_name=account.get("name", account.get("username", "unknown")),
                    avatar_bytes=avatar_bytes,
                    text=tweet_text,
                    posted_at=pub_date,
                    verified=account.get("credibility") == "✅",
                    stats=None,
                    attached_images=attached,
                ),
            )

        # ── Send card + optional video ─────────────────────────────────────────

        if video_url:
            video_bytes = await fetcher.download_bytes(video_url)
            if video_bytes and card_bytes:
                await self.sender.send_photo_and_video(card_bytes, video_bytes, caption, pin=should_pin)
            elif video_bytes:
                await self.sender.send_video(video_bytes, caption, pin=should_pin)
            elif card_bytes:
                await self.sender.send_photo(card_bytes, caption, pin=should_pin)
            else:
                await self.sender.send_text(caption, pin=should_pin)
        else:
            if card_bytes:
                await self.sender.send_photo(card_bytes, caption, pin=should_pin)
            else:
                await self.sender.send_text(caption, pin=should_pin)

        # ── Append to EventStore ───────────────────────────────────────────────
        event_store.append(NewsEvent(
            source="Twitter",
            source_tag="#Twitter",
            headline=tweet_text[:300],
            oil_sentiment=ai_result.get("oil_sentiment", "NEUTRAL"),
            urgency=ai_result.get("urgency", "NORMAL"),
            keywords=ai_result.get("keywords", []),
            username=account.get("username"),
            published_at=pub_date,
        ))

    async def check_new_tweets(self) -> None:
        """Full poll — all monitored accounts, every 5 min."""
        async with self._lock:
            session = await self._get_session()
            fetcher = NitterFetcher(session)
            accounts = sorted(Config.MONITORED_ACCOUNTS, key=lambda a: a["priority"])
            for i in range(0, len(accounts), 5):
                batch = accounts[i: i + 5]
                await asyncio.gather(
                    *[self._process_account(acc, fetcher) for acc in batch],
                    return_exceptions=True,
                )
                await asyncio.sleep(2)

    async def check_tier1_tweets(self) -> None:
        """Fast poll — Tier 1 accounts only, every 20 s.
        Skipped if a full poll is currently running."""
        if self._lock.locked():
            return
        async with self._lock:
            session = await self._get_session()
            fetcher = NitterFetcher(session)
            tier1 = [
                acc for acc in Config.MONITORED_ACCOUNTS
                if acc["username"] in Config.TIER1_ACCOUNTS
            ]
            await asyncio.gather(
                *[self._process_account(acc, fetcher) for acc in tier1],
                return_exceptions=True,
            )

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

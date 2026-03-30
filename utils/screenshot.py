"""
screenshot.py — Playwright-based tweet screenshotter.

Screenshots the main tweet element on a Nitter page using a persistent
headless Chromium instance (started once, reused for all calls).

Falls back gracefully (returns None) if playwright is not installed.
Run once after install: playwright install chromium
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)

try:
    from playwright.async_api import async_playwright

    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False
    logger.info(
        "playwright not installed — screenshot capture disabled, using PIL cards"
    )

try:
    from botasaurus.browser import browser as boto_browser, Driver as BotoDriver

    _BOTASAURUS_AVAILABLE = True
except ImportError:
    _BOTASAURUS_AVAILABLE = False
    logger.info("botasaurus not installed — Truth Social element screenshots disabled")

_pw_instance = None
_browser = None
_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


async def _get_browser():
    if not _AVAILABLE:
        return None
    global _pw_instance, _browser
    async with _get_lock():
        if _browser is None or not _browser.is_connected():
            _pw_instance = await async_playwright().start()
            _browser = await _pw_instance.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-gpu",
                    "--disable-dev-shm-usage",
                ],
            )
    return _browser


_NITTER_CSS = """
    .site-header, nav.nav, .breadcrumb,
    .container > .timeline-header,
    .container > .after-tweet,
    .container > .timeline-item:not(.main-tweet) { display: none !important; }
    body { background: #0f0f0f !important; margin: 0; padding: 0; }
    .container { padding: 0 !important; max-width: 640px !important; margin: 0 auto; }
    .main-tweet {
        border: 1px solid #2f3336 !important;
        border-radius: 12px !important;
        overflow: hidden;
        margin: 8px !important;
    }
"""


async def screenshot_nitter(
    tweet_url: str,
) -> tuple[Optional[bytes], Optional[str]]:
    """
    Screenshot the main tweet card on a Nitter page.

    Args:
        tweet_url: Full Nitter URL, e.g. https://nitter.poast.org/user/status/12345

    Returns:
        (png_bytes, video_src_url) — either may be None on failure.
    """
    browser = await _get_browser()
    if not browser:
        return None, None

    ctx = await browser.new_context(
        viewport={"width": 660, "height": 900},
        device_scale_factor=2,  # 2× Retina quality → 1320px effective
        color_scheme="dark",
        java_script_enabled=True,
    )
    page = await ctx.new_page()
    try:
        await page.goto(tweet_url, wait_until="domcontentloaded", timeout=20_000)
        # Let lazy images / inline player finish loading
        await page.wait_for_timeout(2_000)

        await page.add_style_tag(content=_NITTER_CSS)

        # Try known Nitter tweet container selectors (differs by instance version)
        tweet_el = None
        for sel in (
            ".main-tweet",
            ".tweet-body",
            ".timeline-item .tweet",
            "[data-tweet-id]",
        ):
            tweet_el = await page.query_selector(sel)
            if tweet_el:
                break

        if tweet_el:
            png = await tweet_el.screenshot(type="png")
        else:
            logger.debug("No tweet element found at %s", tweet_url)
            png = await page.screenshot(
                type="png", clip={"x": 0, "y": 0, "width": 660, "height": 800}
            )

        # Try to get video src (Nitter renders an HTML5 <video> for video tweets)
        video_src: str | None = None
        for sel in ("video > source[src]", "video[src]"):
            el = await page.query_selector(sel)
            if el:
                video_src = await el.get_attribute("src")
                if video_src:
                    break

        return png, video_src

    except Exception as exc:
        logger.warning("Nitter screenshot failed %s: %s", tweet_url, exc)
        return None, None
    finally:
        await ctx.close()


def _extract_ts_status_id(post_url: str) -> str | None:
    match = re.search(r"/(?:posts|statuses)/(\d+)(?:/embed)?/?$", post_url)
    return match.group(1) if match else None


if _BOTASAURUS_AVAILABLE:

    @boto_browser(
        headless=True,
        add_arguments=["--no-sandbox", "--disable-dev-shm-usage"],
        window_size=(770, 1000),
        output=None,
        reuse_driver=True, # Keep browser open to save resources
    )
    def _boto_screenshot_truthsocial_sync(
        driver: BotoDriver, data: dict
    ) -> bytes | None:
        status_id = data.get("status_id")
        post_url = data.get("post_url")

        if not status_id and post_url:
            status_id = _extract_ts_status_id(post_url)

        if not status_id:
            logger.error("No status_id found for Truth Social screenshot")
            return None

        canonical_url = f"https://truthsocial.com/@realDonaldTrump/posts/{status_id}"
        embed_url = f"https://truthsocial.com/@realDonaldTrump/posts/{status_id}/embed"

        logger.info(
            "TruthSocial Screenshot API: Opening canonical URL: %s", canonical_url
        )
        driver.get(canonical_url, bypass_cloudflare=True)
        driver.sleep(1)

        logger.info("TruthSocial Screenshot API: Opening embed URL: %s", embed_url)
        driver.get(embed_url, bypass_cloudflare=True)
        driver.sleep(3)

        # Close accept cookie dialog if present before taking screenshot
        try:
            driver.run_js("""
                var closeBtn = document.querySelector("#cookiescript_close");
                if (closeBtn) closeBtn.click();
            """)
            driver.sleep(1)
        except Exception as e:
            logger.debug("No cookie dialog to close or error: %s", e)

        out_dir = "screenshots"
        os.makedirs(out_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(out_dir, f"ts_block_{status_id}_{timestamp}.png")

        tweet_selector = "a.block.bg-white"
        try:
            el = driver.select(tweet_selector)
            if not el:
                raise Exception(f"Element not found: {tweet_selector}")
            driver.run_js("document.documentElement.classList.add('dark');")
            el.save_screenshot(out_path)
            logger.info("✓ Element screenshot saved temporarily")
        except Exception as e:
            logger.warning(
                "Element screenshot failed: %s — falling back to full page", e
            )
            driver.save_screenshot(out_path)

        # Read into bytes
        png_bytes = None
        if os.path.exists(out_path):
            with open(out_path, "rb") as f:
                png_bytes = f.read()
            try:
                os.remove(out_path)
            except Exception as e:
                logger.warning("Failed to remove temp screenshot %s: %s", out_path, e)

        return png_bytes


async def screenshot_truthsocial(post_url: str) -> bytes | None:
    """
    Screenshot a TruthSocial post page via Botasaurus to bypass Cloudflare.

    Args:
        post_url: Canonical URL from the API, e.g.
                  https://truthsocial.com/@realDonaldTrump/statuses/123456

    Returns:
        PNG bytes or None on failure.
    """
    if not _BOTASAURUS_AVAILABLE:
        logger.error("Botasaurus not available for Truth Social screenshots")
        return None

    status_id = _extract_ts_status_id(post_url)
    if not status_id:
        return None

    return await asyncio.to_thread(
        _boto_screenshot_truthsocial_sync, {"status_id": status_id}
    )


async def close() -> None:
    """Cleanly close the persistent browser on application shutdown."""
    global _browser, _pw_instance
    if _browser:
        try:
            await _browser.close()
        except Exception:
            pass
        _browser = None
    if _pw_instance:
        try:
            await _pw_instance.stop()
        except Exception:
            pass
        _pw_instance = None

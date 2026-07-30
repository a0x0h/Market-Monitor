import asyncio
import logging
from datetime import datetime, timezone

import aiohttp
import os

from config import Config
from core.event_store import event_store, NewsEvent
from utils.chart import generate_chart
from utils.helpers import (
    fmt_price,
    pct_arrow,
    utc_now_str,
    emoji_change,
    load_last_prices,
    save_last_prices,
)

logger = logging.getLogger(__name__)

_YF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


_MASSIVE_BASE = os.getenv("MASSIVE_BASE_URL", "https://api.massive.com/v3")


async def _fetch_ticker(
    session: aiohttp.ClientSession,
    symbol: str,
) -> tuple[float | None, float | None]:
    """Returns (current_price, previous_close) via Yahoo Finance chart API."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                logger.warning(f"Failed to fetch {symbol}: HTTP {resp.status}")
                return None, None
            data = await resp.json(content_type=None)
            meta = data["chart"]["result"][0]["meta"]
            price = meta.get("regularMarketPrice")
            prev = meta.get("previousClose") or meta.get("chartPreviousClose")
            return float(price) if price else None, float(prev) if prev else None
    except Exception as e:
        logger.warning(f"Failed to fetch {symbol}: {e}")
        return None, None


async def _fetch_massive_ticker(
    session: aiohttp.ClientSession, symbol: str
) -> tuple[float | None, float | None]:
    """Try Massive REST market quote for `symbol`. Return (price, prev_close) or (None, None)."""
    api_key = Config.MASSIVE_API_KEY if hasattr(Config, "MASSIVE_API_KEY") else os.getenv("MASSIVE_API_KEY", "")
    headers = {"User-Agent": _YF_HEADERS["User-Agent"]}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # Best-effort URL: Massive docs show v3 REST surface; query `markets/quotes` by ticker.
    url = f"{_MASSIVE_BASE}/markets/quotes?ticker={symbol}"
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                logger.debug(f"Massive fetch {symbol} HTTP {resp.status}")
                return None, None
            data = await resp.json(content_type=None)

            # Flexible parsing: Massive responses vary; try common fields
            results = None
            if isinstance(data, dict):
                results = data.get("results") or data.get("data") or data

            item = None
            if isinstance(results, list) and results:
                item = results[0]
            elif isinstance(results, dict):
                # may nest under 'quote' or be the quote itself
                item = results.get("quote") or results

            if not item:
                return None, None

            price = item.get("last") or item.get("price") or item.get("close") or item.get("regularMarketPrice")
            prev = (
                item.get("previousClose")
                or item.get("prev_close")
                or item.get("previous")
                or item.get("prev")
            )
            return (float(price), float(prev)) if price and prev else (
                (float(price), None) if price else (None, None)
            )
    except Exception as e:
        logger.debug(f"Massive fetch failed for {symbol}: {e}")
        return None, None


async def _fetch_okex_usdt_irt(
    session: aiohttp.ClientSession,
) -> tuple[float | None, float | None]:
    url = "https://azapi.ok-ex.io/api/v1/asset/otc/tickers"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                logger.warning(f"Failed to fetch OK-Ex USDT: HTTP {resp.status}")
                return None, None
            data = await resp.json(content_type=None)
            for item in data:
                if item.get("asset") == "USDT":
                    buy_amt = float(item.get("buyAmt", 0))
                    price_change = float(item.get("priceChange", 0))
                    return buy_amt, price_change
            return None, None
    except Exception as e:
        logger.warning(f"Failed to fetch OK-Ex USDT: {e}")
        return None, None


async def fetch_all_prices() -> dict[str, dict]:
    """Fetch all configured tickers concurrently."""
    async with aiohttp.ClientSession(headers=_YF_HEADERS) as session:
        # Use Massive API only for configured tickers (except USDT-IRT)
        tasks = {
            symbol: _fetch_massive_ticker(session, symbol)
            for symbol in Config.PRICE_TICKERS
            if symbol != "USDT-IRT"
        }
        usdt_task = _fetch_okex_usdt_irt(session)

        results = await asyncio.gather(
            *tasks.values(), usdt_task, return_exceptions=True
        )

    usdt_result = results[-1]
    yf_results = results[:-1]

    prices = {}
    for symbol, result in zip(tasks.keys(), yf_results):
        if isinstance(result, Exception) or result is None:
            continue
        price, prev = result
        if price is None:
            continue

        pct = ((price - prev) / prev * 100) if prev and prev != 0 else 0.0
        prices[symbol] = {
            "price": price,
            "prev_close": prev,
            "pct_change": pct,
        }

    if not isinstance(usdt_result, Exception) and usdt_result is not None:
        usdt_price, usdt_pct_change = usdt_result
        if usdt_price is not None:
            prices["USDT-IRT"] = {
                "price": usdt_price,
                "prev_close": usdt_price,
                "pct_change": usdt_pct_change,
            }

    return prices


def build_price_message(
    prices: dict[str, dict], last_prices: dict, channel_link: str = ""
) -> str:
    """Build the formatted price update message. channel_link is the inline URL for price values."""
    if not channel_link:
        channel_link = (
            f"t.me/{Config.TELEGRAM_CHANNEL}"
            if Config.TELEGRAM_CHANNEL
            else "t.me/MonitorIR"
        )

    lines = []
    alerts = []

    for symbol, info in prices.items():
        meta = Config.PRICE_TICKERS.get(symbol, {})
        name = meta.get("name", symbol)
        emoji = meta.get("emoji", "")
        unit = meta.get("unit", "$")
        price = info["price"]
        pct = info["pct_change"]

        last = last_prices.get(symbol, {}).get("price")
        last_diff = last_prices.get(symbol, {}).get("last_different_price")

        # Tether local percentage calculation
        if symbol == "USDT-IRT" and last_diff and last_diff != 0:
            pct = ((price - last_diff) / last_diff) * 100

        price_str = fmt_price(price, unit)
        arrow = pct_arrow(pct)
        # Avoid shadowing the function name
        e_change = emoji_change(pct)

        lines.append(
            f"{e_change}  {name} | <b><a href='{channel_link}'>{price_str}</a></b> ({arrow})"
        )

        # Check alert threshold against last sent price
        if last and abs((price - last) / last * 100) >= Config.PRICE_ALERT_PCT:
            direction = "📈 Up" if price > last else "📉 Down"
            alerts.append(
                f"⚠️ <b>Alert {name}:</b> {direction} more than "
                f"{Config.PRICE_ALERT_PCT}% → {price_str}"
            )

    # 18K Gold Implied Calculation
    # User formula: (Gold price / 31.1034) * Tether price * (18/24) -> 3/4
    gold_info = prices.get("GC=F")
    tether_info = prices.get("USDT-IRT")
    if (
        gold_info
        and tether_info
        and gold_info.get("price")
        and tether_info.get("price")
    ):
        g_price = gold_info["price"]
        t_price = tether_info["price"]
        # Convert Oz to Gram, then multi by purity
        implied_18k_price = (g_price * t_price / 31.1034) * 0.75

        g_prev = gold_info.get("prev_close")
        t_prev = tether_info.get("prev_close")
        pct_18k = 0.0
        if g_prev and t_prev and g_prev > 0 and t_prev > 0:
            implied_18k_prev_price = (g_prev * t_prev / 31.1034) * 0.75
            pct_18k = (
                (implied_18k_price - implied_18k_prev_price) / implied_18k_prev_price
            ) * 100

        # Determine last stored 18K pct maybe? or just calculate from prev close.
        # Add to the message
        unit_18k = Config.PRICE_TICKERS.get("USDT-IRT", {}).get("unit", "TMN")
        name_18k = "18K Gold (Implied)"
        price_str_18k = fmt_price(implied_18k_price, unit_18k)

        lines.append(
            f"{emoji_change(pct_18k)}  {name_18k} | <b><a href='{channel_link}'>{price_str_18k}</a></b> ({pct_arrow(pct_18k)})"
        )

    if alerts:
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━")
        lines.extend(alerts)

    return "\n".join(lines)


class PriceMonitor:
    def __init__(self, sender, bale_sender=None) -> None:
        self.sender = sender
        self.bale_sender = bale_sender
        self._daily_events: list[str] = []

    async def send_price_update(self, with_screenshot: bool = False) -> None:
        try:
            prices = await fetch_all_prices()
            if not prices:
                logger.warning("No price data received")
                return

            last_prices = load_last_prices()

            tg_link = (
                f"https://t.me/{Config.TELEGRAM_CHANNEL}"
                if Config.TELEGRAM_CHANNEL
                else "https://t.me/MonitorIR"
            )
            tg_message = build_price_message(prices, last_prices, tg_link)

            tasks = []

            # Optionally grab screenshot from oilprice.com
            png = None
            if with_screenshot:
                import utils.screenshot as screenshot_module

                png = await screenshot_module.screenshot_oilprice()

            if png:
                tasks.append(self.sender.send_photo(png, tg_message))
            else:
                tasks.append(self.sender.send_text(tg_message))

            if self.bale_sender:
                bale_link = (
                    f"https://ble.ir/{Config.BALE_CHANNEL}"
                    if Config.BALE_CHANNEL
                    else "https://ble.ir/MonitorIR"
                )
                bale_message = build_price_message(prices, last_prices, bale_link)

                # Assume sender has send_photo method
                if png and hasattr(self.bale_sender, "send_photo"):
                    tasks.append(self.bale_sender.send_photo(png, bale_message))
                else:
                    tasks.append(self.bale_sender.send_text(bale_message))

            await asyncio.gather(*tasks, return_exceptions=True)

            # Persist for next cycle comparison
            to_save = {}
            for sym, d in prices.items():
                curr_p = d["price"]
                old_p = last_prices.get(sym, {}).get("price")
                old_diff = last_prices.get(sym, {}).get("last_different_price")

                if old_p and curr_p != old_p:
                    diff_p = old_p
                else:
                    diff_p = old_diff if old_diff else curr_p

                to_save[sym] = {"price": curr_p, "last_different_price": diff_p}

            save_last_prices(to_save)

            # Store event summary for daily report
            summary_parts = []
            for sym, d in prices.items():
                meta = Config.PRICE_TICKERS.get(sym, {})
                summary_parts.append(
                    f"{meta.get('name', sym)}: "
                    f"{fmt_price(d['price'], meta.get('unit', '$'))} "
                    f"({d['pct_change']:+.2f}%)"
                )
            headline = f"Prices: {' | '.join(summary_parts)}"
            self._daily_events.append(f"[{utc_now_str()}] {headline}")

            # Append a price snapshot to the shared EventStore
            event_store.append(
                NewsEvent(
                    source="Price",
                    source_tag="#قیمت",
                    headline=headline[:300],
                    oil_sentiment="NEUTRAL",
                    urgency="NORMAL",
                    keywords=["oil", "price", "market"],
                )
            )

        except Exception as e:
            logger.error(f"Price update failed: {e}", exc_info=True)

    async def send_daily_charts(self) -> None:
        """Generate and send a TradingView-style chart for every configured symbol."""
        try:
            prices = await fetch_all_prices()
            for symbol, d in prices.items():
                if symbol == "USDT-IRT":
                    continue
                meta = Config.PRICE_TICKERS.get(symbol, {})
                name = meta.get("name", symbol)
                unit = meta.get("unit", "$")
                chart_bytes = await generate_chart(symbol, name, d["price"], unit)
                if not chart_bytes:
                    continue
                price_str = fmt_price(d["price"], unit)
                arrow = pct_arrow(d["pct_change"])
                caption = f"📊 <b>{name}</b>  {price_str}  {arrow}\n#Charts #Price"
                await self.sender.send_photo(chart_bytes, caption)
                await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Daily charts failed: {e}", exc_info=True)

    async def send_daily_summary(self) -> None:
        from ai.processor import summarize_daily

        try:
            summary = await summarize_daily(self._daily_events)
            now = datetime.now(timezone.utc).strftime("%d %b %Y")

            msg = (
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🌅  <b>Daily Summary</b>  |  {now}\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"{summary}\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━"
            )
            await self.sender.send_text(msg)
            self._daily_events.clear()
        except Exception as e:
            logger.error(f"Daily summary failed: {e}", exc_info=True)

import asyncio
import logging
from datetime import datetime, timezone

import aiohttp

from config import Config
from core.event_store import event_store, NewsEvent
from utils.chart import generate_chart
from utils.helpers import (
    fmt_price, pct_arrow, utc_now_str, emoji_change,
    load_last_prices, save_last_prices,
)

logger = logging.getLogger(__name__)

_YF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


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


async def _fetch_okex_usdt_irt(session: aiohttp.ClientSession) -> tuple[float | None, float | None]:
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
        tasks = {
            symbol: _fetch_ticker(session, symbol)
            for symbol in Config.PRICE_TICKERS
            if symbol != "USDT-IRT"
        }
        usdt_task = _fetch_okex_usdt_irt(session)
        
        results = await asyncio.gather(*tasks.values(), usdt_task, return_exceptions=True)

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
                "pct_change": usdt_pct_change
            }

    return prices


def build_price_message(prices: dict[str, dict], last_prices: dict) -> str:
    """Build the formatted Telegram price update message."""
    lines = []
    alerts = []

    lines.append(f"📊  <b>#Price</b>  |  {utc_now_str()}")
    lines.append("━━━━━━━━━━━━━━━━━")

    for symbol, info in prices.items():
        channel_link = f"t.me/{Config.TELEGRAM_CHANNEL}" if Config.TELEGRAM_CHANNEL else "t.me/MonitorIR"

        meta = Config.PRICE_TICKERS.get(symbol, {})
        name = meta.get("name", symbol)
        emoji = meta.get("emoji", "")
        unit = meta.get("unit", "$")
        price = info["price"]
        pct = info["pct_change"]

        price_str = fmt_price(price, unit)
        arrow = pct_arrow(pct)
        # Avoid shadowing the function name
        e_change = emoji_change(pct)

        lines.append(f"{e_change}  {name} | <b><a href='{channel_link}'>{price_str}</a></b> ({arrow})")

        # Check alert threshold against last sent price
        last = last_prices.get(symbol, {}).get("price")
        if last and abs((price - last) / last * 100) >= Config.PRICE_ALERT_PCT:
            direction = "📈 Up" if price > last else "📉 Down"
            alerts.append(
                f"⚠️ <b>Alert {name}:</b> {direction} more than "
                f"{Config.PRICE_ALERT_PCT}% → {price_str}"
            )

    if alerts:
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━")
        lines.extend(alerts)

    return "\n".join(lines)


class PriceMonitor:
    def __init__(self, sender) -> None:
        self.sender = sender
        self._daily_events: list[str] = []

    async def send_price_update(self) -> None:
        try:
            prices = await fetch_all_prices()
            if not prices:
                logger.warning("No price data received")
                return

            last_prices = load_last_prices()
            message = build_price_message(prices, last_prices)

            await self.sender.send_text(message)

            # Persist for next cycle comparison
            save_last_prices(
                {sym: {"price": d["price"]} for sym, d in prices.items()}
            )

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
            event_store.append(NewsEvent(
                source="Price",
                source_tag="#قیمت",
                headline=headline[:300],
                oil_sentiment="NEUTRAL",
                urgency="NORMAL",
                keywords=["oil", "price", "market"],
            ))

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
                caption = (
                    f"📊 <b>{name}</b>  {price_str}  {arrow}\n"
                    f"#Charts #Price"
                )
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

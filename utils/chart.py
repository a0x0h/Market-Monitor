"""
chart.py — TradingView-style daily candlestick chart generator.

Uses Yahoo Finance v8 chart API (same endpoint as prices.py) + mplfinance.
All matplotlib calls run in a thread-pool executor so they don't block the loop.
"""

from __future__ import annotations

import asyncio
import logging
from io import BytesIO
from typing import Optional

import aiohttp
import matplotlib
matplotlib.use("Agg")           # non-interactive backend — must be before pyplot
import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd

from config import Config

logger = logging.getLogger(__name__)

_YF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

# ── TradingView dark theme ─────────────────────────────────────────────────────
_MC = mpf.make_marketcolors(
    up="#089981", down="#F23645",
    wick={"up": "#089981", "down": "#F23645"},
    edge={"up": "#089981", "down": "#F23645"},
    volume={"up": "#089981", "down": "#F23645"},
)
_STYLE = mpf.make_mpf_style(
    base_mpf_style="nightclouds",
    marketcolors=_MC,
    facecolor="#131722",
    figcolor="#131722",
    gridcolor="#1e222d",
    gridstyle="-",
    y_on_right=True,
    rc={
        "axes.labelcolor": "#b2b5be",
        "axes.edgecolor":  "#1e222d",
        "xtick.color":     "#b2b5be",
        "ytick.color":     "#b2b5be",
        "text.color":      "#b2b5be",
        "font.size":       11,
    },
)


async def _fetch_ohlcv(
    session: aiohttp.ClientSession,
    symbol:  str,
    interval: str = "1d",
    range_:   str = "1mo",
) -> Optional[pd.DataFrame]:
    url    = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"interval": interval, "range": range_}
    try:
        async with session.get(url, params=params,
                               timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                logger.warning("OHLCV %s: HTTP %s", symbol, resp.status)
                return None
            data = await resp.json(content_type=None)
            res  = data["chart"]["result"][0]
            ts   = res["timestamp"]
            q    = res["indicators"]["quote"][0]
            df   = pd.DataFrame(
                {
                    "Open":   q.get("open",   [None]*len(ts)),
                    "High":   q.get("high",   [None]*len(ts)),
                    "Low":    q.get("low",    [None]*len(ts)),
                    "Close":  q.get("close",  [None]*len(ts)),
                    "Volume": q.get("volume", [0   ]*len(ts)),
                },
                index=pd.to_datetime(ts, unit="s", utc=True),
            )
            df["Volume"] = df["Volume"].fillna(0).astype(float)
            df = df.dropna(subset=["Open", "High", "Low", "Close"])
            return df if len(df) >= 5 else None
    except Exception as exc:
        logger.warning("OHLCV fetch error %s: %s", symbol, exc)
        return None


def _render_sync(
    df:           pd.DataFrame,
    name:         str,
    current:      float,
    unit:         str,
) -> bytes:
    """Blocking chart render — call inside run_in_executor."""
    price_str = f"{unit}{current:,.2f}" if unit == "$" else f"{current:,.2f}"

    addplots = [
        mpf.make_addplot(
            [current] * len(df),
            color="#b2b5be", width=0.7, linestyle="--",
        )
    ]
    if len(df) >= 20:
        addplots.append(
            mpf.make_addplot(
                df["Close"].rolling(20).mean(),
                color="#2196f3", width=1.0,
            )
        )

    fig, axes = mpf.plot(
        df,
        type="candle",
        style=_STYLE,
        volume=True,
        addplot=addplots,
        title=f" {name}   {price_str}  ·  30-Day Daily",
        figsize=(16, 9),
        returnfig=True,
        tight_layout=True,
        datetime_format="%b %d",
        xrotation=0,
        volume_panel=1,
        panel_ratios=(4, 1),
    )

    axes[0].title.set_color("#e7e9ea")
    axes[0].title.set_fontsize(15)
    axes[0].title.set_fontweight("bold")

    fig.text(0.5, 0.5, "Market Monitor",
             transform=fig.transFigure,
             alpha=0.04, fontsize=72, ha="center", va="center",
             color="white", rotation=25)

    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                facecolor="#131722")
    plt.close(fig)
    return buf.getvalue()


async def generate_chart(
    symbol:  str,
    name:    str,
    current: float,
    unit:    str = "$",
) -> Optional[bytes]:
    """Generate a TradingView-style chart. Returns PNG bytes or None on failure."""
    async with aiohttp.ClientSession(headers=_YF_HEADERS) as session:
        df = await _fetch_ohlcv(session, symbol)
    if df is None:
        return None
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(
            None, lambda: _render_sync(df, name, current, unit)
        )
    except Exception as exc:
        logger.error("Chart render failed %s: %s", symbol, exc)
        return None

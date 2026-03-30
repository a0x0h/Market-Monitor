"""
event_store.py — Shared rolling buffer of news events from all monitors.

All three layers write here; the analysis layer reads from it.
"""

from __future__ import annotations

import json
import logging
import os
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
_PERSIST  = os.path.join(_DATA_DIR, "events.json")


@dataclass
class NewsEvent:
    source:       str            # "Twitter" | "TruthSocial" | "News" | "Price"
    source_tag:   str            # "#Twitter" etc.
    headline:     str            # first 300 chars of raw text
    oil_sentiment: str           # "BULLISH" | "BEARISH" | "NEUTRAL"
    urgency:      str            # "BREAKING" | "HIGH" | "NORMAL"
    keywords:     list[str]      = field(default_factory=list)
    username:     Optional[str]  = None
    published_at: datetime       = field(default_factory=lambda: datetime.now(timezone.utc))


class EventStore:
    def __init__(self, max_events: int = 500) -> None:
        self._buf: deque[NewsEvent] = deque(maxlen=max_events)
        os.makedirs(_DATA_DIR, exist_ok=True)
        self._load()

    # ── Public API ─────────────────────────────────────────────────────────────
    def append(self, event: NewsEvent) -> None:
        self._buf.append(event)
        # Persist every 20 appends
        if len(self._buf) % 20 == 0:
            self._save()

    def get_recent(self, hours: int = 12) -> list[NewsEvent]:
        now    = datetime.now(timezone.utc)
        cutoff = now.timestamp() - hours * 3600
        return [
            e for e in self._buf
            if e.published_at.replace(tzinfo=timezone.utc).timestamp() >= cutoff
        ]

    # ── Persistence ────────────────────────────────────────────────────────────
    def _save(self) -> None:
        try:
            records = []
            for e in self._buf:
                d = asdict(e)
                d["published_at"] = e.published_at.isoformat()
                records.append(d)
            with open(_PERSIST, "w", encoding="utf-8") as f:
                json.dump(records[-300:], f)
        except Exception as exc:
            logger.debug("EventStore save failed: %s", exc)

    def _load(self) -> None:
        try:
            with open(_PERSIST, "r", encoding="utf-8") as f:
                records = json.load(f)
            for r in records:
                r["published_at"] = datetime.fromisoformat(r["published_at"])
                if r["published_at"].tzinfo is None:
                    r["published_at"] = r["published_at"].replace(tzinfo=timezone.utc)
                self._buf.append(NewsEvent(**r))
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            pass


# Module-level singleton
event_store = EventStore()

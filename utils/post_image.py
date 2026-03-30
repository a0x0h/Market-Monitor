from __future__ import annotations

import textwrap
from datetime import datetime
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont


def _safe_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    # Fall back to default font if truetype fonts are not available on host.
    for name in ("arial.ttf", "segoeui.ttf", "tahoma.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_post_card(
    platform: str,
    username: str,
    text: str,
    posted_at: datetime,
) -> bytes:
    width, height = 1200, 675
    image = Image.new("RGB", (width, height), color=(248, 250, 252))
    draw = ImageDraw.Draw(image)

    title_font = _safe_font(44)
    meta_font = _safe_font(28)
    body_font = _safe_font(34)

    draw.rectangle((0, 0, width, 110), fill=(14, 37, 66))
    draw.text((36, 30), f"{platform} Post", font=title_font, fill=(255, 255, 255))

    meta_text = f"@{username}  |  {posted_at.strftime('%Y-%m-%d %H:%M UTC')}"
    draw.text((36, 145), meta_text, font=meta_font, fill=(21, 41, 65))

    wrapped = textwrap.wrap(text or "(no text)", width=50)
    visible_lines = wrapped[:10]

    y = 215
    for line in visible_lines:
        draw.text((36, y), line, font=body_font, fill=(15, 23, 42))
        y += 52

    if len(wrapped) > len(visible_lines):
        draw.text((36, y), "...", font=body_font, fill=(15, 23, 42))

    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()

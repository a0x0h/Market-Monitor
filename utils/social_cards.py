"""
social_cards.py — Pixel-accurate social media post card renderer.

  render_x_card(...)             → black X/Twitter style (matches the screenshot)
  render_truthsocial_card(...)   → dark-navy TruthSocial style

Both return PNG bytes.  Attached images are composited into the card in a
Twitter-style grid (1 / 2-side-by-side / 3-left+2-right / 2×2).

SVG icons (like · comment · retweet) are rendered from the real Twitter SVG
path data using a zero-dependency pure-Python cubic-Bézier rasteriser.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from io import BytesIO
from typing import Optional
import os

import aiohttp
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# ── Avatar cache ───────────────────────────────────────────────────────────────
_avatar_cache: dict[str, Optional[bytes]] = {}

# ── Twitter SVG icon paths (24 × 24 viewBox) ──────────────────────────────────
_ICON_LIKE = (
    "M16.697 5.5c-1.222-.06-2.679.51-3.89 2.16l-.805 1.09-.806-1.09"
    "C9.984 6.01 8.526 5.44 7.304 5.5c-1.243.07-2.349.78-2.91 1.91"
    "-.552 1.12-.633 2.78.479 4.82 1.074 1.97 3.257 4.27 7.129 6.61"
    " 3.87-2.34 6.052-4.64 7.126-6.61 1.111-2.04 1.03-3.7.477-4.82"
    "-.561-1.13-1.666-1.84-2.908-1.91z"
    "m4.187 7.69c-1.351 2.48-4.001 5.12-8.379 7.67l-.503.3-.504-.3"
    "c-4.379-2.55-7.029-5.19-8.382-7.67-1.36-2.5-1.41-4.86-.514-6.67"
    " .887-1.79 2.647-2.91 4.601-3.01 1.651-.09 3.368.56 4.798 2.01"
    " 1.429-1.45 3.146-2.1 4.796-2.01 1.954.1 3.714 1.22 4.601 3.01"
    " .896 1.81.846 4.17-.514 6.67z"
)
_ICON_COMMENT = (
    "M1.751 10c0-4.42 3.584-8 8.005-8h4.366c4.49 0 8.129 3.64 8.129 8.13"
    " 0 2.96-1.607 5.68-4.196 7.11l-8.054 4.46v-3.69h-.067"
    "c-4.49.1-8.183-3.51-8.183-8.01z"
    "m8.005-6c-3.317 0-6.005 2.69-6.005 6 0 3.37 2.77 6.08 6.138 6.01"
    "l.351-.01h1.761v2.3l5.087-2.81c1.951-1.08 3.163-3.13 3.163-5.36"
    " 0-3.39-2.744-6.13-6.129-6.13H9.756z"
)
_ICON_RETWEET = (
    "M4.5 3.88l4.432 4.14-1.364 1.46L5.5 7.55V16c0 1.1.896 2 2 2H13v2"
    "H7.5c-2.209 0-4-1.79-4-4V7.55L1.432 9.48.068 8.02 4.5 3.88z"
    "M16.5 6H11V4h5.5c2.209 0 4 1.79 4 4v8.45l2.068-1.93 1.364 1.46"
    "-4.432 4.14-4.432-4.14 1.364-1.46 2.068 1.93V8c0-1.1-.896-2-2-2z"
)

# ── Card geometry (2× retina — renders at 1840 px for crisp Telegram display) ──
CARD_W      = 1840
PAD_H       = 64
PAD_V       = 56
AV_SIZE     = 104
AV_GAP      = 28
NAME_COL    = PAD_H + AV_SIZE + AV_GAP
BODY_FONT   = 36
NAME_FONT   = 34
HANDLE_FONT = 28
TS_FONT     = 26      # timestamp line
STATS_FONT  = 26
BODY_LINE_H = 60
BODY_TOP    = 44      # gap: header → body
IMG_GAP     = 44      # gap: body → image grid
STATS_TOP   = 28
STATS_H     = 76
RADIUS      = 32
IMG_CORNER  = 20      # corner radius on image grid cells
IMG_MAX_H   = 640     # max height of the image composite
IMG_GRID_GAP= 6       # px gap between grid cells

# ── Palettes ───────────────────────────────────────────────────────────────────
_X = dict(bg=(0,0,0), border=(47,51,54), text=(231,233,234),
          secondary=(113,118,123), blue=(29,155,240))
_TS = dict(bg=(21,32,43), border=(56,68,77), text=(247,249,249),
           secondary=(110,118,125), blue=(29,155,240), red=(215,40,60))

# ── Font loader ────────────────────────────────────────────────────────────────
_FONT_CACHE: dict[tuple, ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}
_WFONTS = r"C:\Windows\Fonts"

def _font(bold: bool, size: int):
    key = (bold, size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    names = ["arialbd.ttf","segoeuib.ttf"] if bold else ["arial.ttf","segoeui.ttf"]
    for n in names:
        for p in (os.path.join(_WFONTS, n), n):
            try:
                f = ImageFont.truetype(p, size); _FONT_CACHE[key] = f; return f
            except OSError:
                pass
    f = ImageFont.load_default(); _FONT_CACHE[key] = f; return f

def _tw(draw, txt, font):
    bb = draw.textbbox((0,0), txt, font=font)
    return bb[2] - bb[0]

def _wrap(draw, text, font, max_px):
    words = (text or "").split()
    lines, cur = [], ""
    for w in words:
        cand = (cur + " " + w).strip()
        if _tw(draw, cand, font) <= max_px:
            cur = cand
        else:
            if cur: lines.append(cur)
            cur = w
    if cur: lines.append(cur)
    return lines or [""]

# ── SVG path rasteriser ────────────────────────────────────────────────────────
class _SVGRender:
    """
    Zero-dependency SVG path → list[list[tuple]] polygon rasteriser.
    Handles M m L l H h V v C c Z z with cubic-Bézier sampling.
    """
    def __init__(self):
        self._x = self._y = self._sx = self._sy = 0.0
        self._polys: list[list[tuple]] = []
        self._cur:   list[tuple]       = []

    def _flush(self):
        if self._cur:
            self._polys.append(self._cur)
            self._cur = []

    def _move(self, x, y):
        self._flush()
        self._x = self._sx = x; self._y = self._sy = y
        self._cur = [(x, y)]

    def _lineto(self, x, y):
        self._x, self._y = x, y; self._cur.append((x, y))

    def _cubic(self, c1x, c1y, c2x, c2y, ex, ey, n=20):
        x0, y0 = self._x, self._y
        for i in range(1, n + 1):
            t = i / n; mt = 1 - t
            px = mt**3*x0 + 3*mt**2*t*c1x + 3*mt*t**2*c2x + t**3*ex
            py = mt**3*y0 + 3*mt**2*t*c1y + 3*mt*t**2*c2y + t**3*ey
            self._cur.append((px, py))
        self._x, self._y = ex, ey

    def _close(self):
        if self._cur:
            self._cur.append((self._sx, self._sy)); self._flush()
        self._x, self._y = self._sx, self._sy

    def render(self, d: str) -> list[list[tuple]]:
        self._x = self._y = self._sx = self._sy = 0.0
        self._polys, self._cur = [], []
        toks = re.findall(
            r'[MmLlHhVvCcZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?', d)
        i, cmd = 0, 'M'
        while i < len(toks):
            t = toks[i]
            if t.isalpha():
                cmd = t; i += 1; continue
            try:
                if   cmd=='M': x,y=float(toks[i]),float(toks[i+1]); i+=2; self._move(x,y); cmd='L'
                elif cmd=='m': x,y=self._x+float(toks[i]),self._y+float(toks[i+1]); i+=2; self._move(x,y); cmd='l'
                elif cmd=='L': x,y=float(toks[i]),float(toks[i+1]); i+=2; self._lineto(x,y)
                elif cmd=='l': x,y=self._x+float(toks[i]),self._y+float(toks[i+1]); i+=2; self._lineto(x,y)
                elif cmd=='H': self._lineto(float(toks[i]),self._y); i+=1
                elif cmd=='h': self._lineto(self._x+float(toks[i]),self._y); i+=1
                elif cmd=='V': self._lineto(self._x,float(toks[i])); i+=1
                elif cmd=='v': self._lineto(self._x,self._y+float(toks[i])); i+=1
                elif cmd=='C':
                    c1x,c1y=float(toks[i]),float(toks[i+1]); c2x,c2y=float(toks[i+2]),float(toks[i+3]); ex,ey=float(toks[i+4]),float(toks[i+5]); i+=6
                    self._cubic(c1x,c1y,c2x,c2y,ex,ey)
                elif cmd=='c':
                    c1x=self._x+float(toks[i]); c1y=self._y+float(toks[i+1])
                    c2x=self._x+float(toks[i+2]); c2y=self._y+float(toks[i+3])
                    ex=self._x+float(toks[i+4]); ey=self._y+float(toks[i+5]); i+=6
                    self._cubic(c1x,c1y,c2x,c2y,ex,ey)
                elif cmd in ('Z','z'): self._close()
                else: i+=1
            except (IndexError, ValueError): break
        self._close()
        return self._polys


def _rasterize_icon(path_d: str, size: int, color: tuple,
                    bg: tuple = (0,0,0)) -> Image.Image:
    """Render a 24×24 SVG path to a PIL RGBA image at `size` px."""
    render_sz = 48                     # render 2× for anti-alias quality
    scale     = render_sz / 24.0

    img  = Image.new("RGB", (render_sz, render_sz), bg)
    draw = ImageDraw.Draw(img)

    polys = _SVGRender().render(path_d)
    scaled = [[(x*scale, y*scale) for x,y in poly] for poly in polys]

    if len(scaled) == 1:
        draw.polygon(scaled[0], fill=color)
    elif len(scaled) >= 2:
        # Two subpaths → outer filled, inner erased (outline / evenodd effect)
        draw.polygon(scaled[-1], fill=color)       # outer
        draw.polygon(scaled[0],  fill=bg)          # inner hole

    # Resize to final size with high quality
    img = img.resize((size, size), Image.LANCZOS)

    # Convert to RGBA with a simple luminance mask so icon is transparent on bg
    r, g, b = color
    br, bg_, bb = bg
    out = Image.new("RGBA", (size, size), (0,0,0,0))
    for px in range(size):
        for py in range(size):
            pr, pg_, pb_ = img.getpixel((px, py))
            # alpha proportional to difference from background
            diff = abs(pr - br) + abs(pg_ - bg_) + abs(pb_ - bb)
            alpha = min(255, diff * 2)
            out.putpixel((px, py), (r, g, b, alpha))
    return out


# Pre-render icon cache
_icon_cache: dict[tuple, dict] = {}
_IMG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "img")


def _load_png_icon(name: str, size: int, color: tuple) -> Optional[Image.Image]:
    """
    Load a white-on-transparent PNG from /img and tint it to `color`.
    Returns None if file is missing.
    """
    path = os.path.join(_IMG_DIR, f"{name}.png")
    try:
        img = Image.open(path).convert("RGBA").resize((size, size), Image.LANCZOS)
        r, g, b = color
        img.putdata([(r, g, b, a) for (_, _, _, a) in img.getdata()])
        return img
    except Exception as exc:
        logger.debug("PNG icon load failed %s: %s", name, exc)
        return None


def _icons(size: int = 18, color: tuple = (113,118,123),
           bg:    tuple = (0,0,0)) -> dict:
    key = (size, color, bg)
    if key not in _icon_cache:
        _icon_cache[key] = {
            "like":     _load_png_icon("like",     size, color) or _rasterize_icon(_ICON_LIKE,    size, color, bg),
            "comment":  _load_png_icon("comment",  size, color) or _rasterize_icon(_ICON_COMMENT, size, color, bg),
            "retweet":  _load_png_icon("retweet",  size, color) or _rasterize_icon(_ICON_RETWEET, size, color, bg),
            "bookmark": _load_png_icon("bookmark", size, color),
        }
    return _icon_cache[key]


# ── Image grid compositor ──────────────────────────────────────────────────────
def _fill_crop(img: Image.Image, w: int, h: int) -> Image.Image:
    sw, sh = img.size
    scale = max(w/sw, h/sh)
    nw, nh = int(sw*scale), int(sh*scale)
    img = img.resize((nw, nh), Image.LANCZOS)
    l, t = (nw-w)//2, (nh-h)//2
    return img.crop((l, t, l+w, t+h))

def _rounded_paste(canvas: Image.Image, cell: Image.Image, x: int, y: int,
                   radius: int) -> None:
    mask = Image.new("L", cell.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, cell.width-1, cell.height-1), radius=radius, fill=255)
    canvas.paste(cell, (x, y), mask)

def _compose_grid(images: list[bytes], total_w: int,
                  max_h: int) -> Optional[Image.Image]:
    imgs = []
    for b in images[:4]:
        try: imgs.append(Image.open(BytesIO(b)).convert("RGB"))
        except Exception: pass
    if not imgs: return None

    g = IMG_GRID_GAP
    n = len(imgs)

    if n == 1:
        img = _fill_crop(imgs[0], total_w,
                         min(max_h, int(imgs[0].height * total_w / max(imgs[0].width,1))))
        canvas = Image.new("RGB", (img.width, img.height), (0,0,0))
        canvas.paste(img, (0,0))
    elif n == 2:
        hw = (total_w - g) // 2
        canvas = Image.new("RGB", (total_w, max_h), (0,0,0))
        _rounded_paste(canvas, _fill_crop(imgs[0], hw, max_h), 0, 0, IMG_CORNER)
        _rounded_paste(canvas, _fill_crop(imgs[1], hw, max_h), hw+g, 0, IMG_CORNER)
    elif n == 3:
        lw = (total_w - g) // 2; rw = total_w - lw - g
        th = (max_h - g) // 2;   bh = max_h - th - g
        canvas = Image.new("RGB", (total_w, max_h), (0,0,0))
        _rounded_paste(canvas, _fill_crop(imgs[0], lw, max_h), 0, 0, IMG_CORNER)
        _rounded_paste(canvas, _fill_crop(imgs[1], rw, th), lw+g, 0, IMG_CORNER)
        _rounded_paste(canvas, _fill_crop(imgs[2], rw, bh), lw+g, th+g, IMG_CORNER)
    else:
        hw = (total_w - g) // 2; hh = (max_h - g) // 2
        canvas = Image.new("RGB", (total_w, max_h), (0,0,0))
        for idx, img in enumerate(imgs):
            col = idx % 2; row = idx // 2
            _rounded_paste(canvas, _fill_crop(img, hw, hh),
                           col*(hw+g), row*(hh+g), IMG_CORNER)

    return canvas


# ── Shared helpers ─────────────────────────────────────────────────────────────
def _make_circle(img_bytes: Optional[bytes], size: int,
                 bg: tuple) -> Image.Image:
    out  = Image.new("RGBA", (size,size), (0,0,0,0))
    mask = Image.new("L",    (size,size), 0)
    ImageDraw.Draw(mask).ellipse((0,0,size-1,size-1), fill=255)
    if img_bytes:
        try:
            src = Image.open(BytesIO(img_bytes)).convert("RGB").resize(
                  (size,size), Image.LANCZOS)
            out.paste(src, (0,0)); out.putalpha(mask); return out
        except Exception: pass
    ImageDraw.Draw(out).ellipse((0,0,size-1,size-1), fill=bg)
    out.putalpha(mask); return out

def _draw_verified(draw, x, y, sz=18):
    draw.ellipse((x,y,x+sz,y+sz), fill=(29,155,240))
    m = sz//2
    draw.line([(x+3,y+m),(x+m-1,y+m+4)], fill="white", width=2)
    draw.line([(x+m-1,y+m+4),(x+sz-3,y+m-3)], fill="white", width=2)

def _fmt_n(n: int) -> str:
    if n>=1_000_000: return f"{n/1_000_000:.1f}M"
    if n>=10_000:    return f"{n//1_000}K"
    if n>=1_000:     return f"{n/1_000:.1f}K"
    return str(n)

def _rel_time(dt: datetime) -> str:
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
    s = max(0, int((now-dt).total_seconds()))
    if s<60:    return f"{s}s"
    if s<3600:  return f"{s//60}m"
    if s<86400: return f"{s//3600}h"
    return f"{s//86400}d"

def _twitter_ts(dt: datetime, views: Optional[int] = None) -> str:
    """Format like: '1:38 AM · Mar 28, 2026 · 29.4K Views'"""
    hour   = str(dt.hour % 12 or 12)
    minute = dt.strftime("%M")
    ampm   = dt.strftime("%p")
    date   = f"{dt.strftime('%b')} {dt.day}, {dt.year}"
    ts = f"{hour}:{minute} {ampm} · {date}"
    if views:
        ts += f" · {_fmt_n(views)} Views"
    return ts


# ── X (Twitter) card ──────────────────────────────────────────────────────────
def render_x_card(
    username:        str,
    display_name:    str,
    avatar_bytes:    Optional[bytes],
    text:            str,
    posted_at:       datetime,
    verified:        bool             = True,
    stats:           Optional[dict]   = None,   # replies retweets likes views
    attached_images: list[bytes]      = [],
) -> bytes:
    """
    Render a black X-style card matching the screenshot.
    Returns PNG bytes.
    """
    c       = _X
    fn_name = _font(True,  NAME_FONT)
    fn_han  = _font(False, HANDLE_FONT)
    fn_body = _font(False, BODY_FONT)
    fn_ts   = _font(False, TS_FONT)
    fn_stat = _font(False, STATS_FONT)
    fn_logo = _font(True,  40)

    # Measure
    dummy = Image.new("RGB", (CARD_W, 100))
    dmeasure = ImageDraw.Draw(dummy)
    body_max_w = CARD_W - PAD_H * 2
    lines = _wrap(dmeasure, text, fn_body, body_max_w)
    if len(lines) > 16:
        lines = lines[:16]; lines[-1] = lines[-1][:60] + "…"
    body_h   = len(lines) * BODY_LINE_H
    header_h = max(AV_SIZE, NAME_FONT + 16 + HANDLE_FONT)

    # Image grid
    grid_img = None
    grid_h   = 0
    if attached_images:
        grid_img = _compose_grid(attached_images, body_max_w, IMG_MAX_H)
        if grid_img: grid_h = grid_img.height + IMG_GAP

    total_h = (PAD_V + header_h + BODY_TOP + body_h
               + grid_h + STATS_TOP + 36 + STATS_TOP + STATS_H + PAD_V)
    total_h = max(total_h, 360)

    card = Image.new("RGB", (CARD_W, total_h), c["bg"])
    draw = ImageDraw.Draw(card)
    draw.rounded_rectangle((0,0,CARD_W-1,total_h-1), radius=RADIUS,
                           outline=c["border"], width=2)

    # Avatar
    av = _make_circle(avatar_bytes, AV_SIZE, c["secondary"])
    card.paste(av, (PAD_H, PAD_V), av)

    # X logo
    draw.text((CARD_W-PAD_H-44, PAD_V-4), "X", font=fn_logo, fill=c["secondary"])

    # Name
    draw.text((NAME_COL, PAD_V), display_name, font=fn_name, fill=c["text"])
    nw = _tw(draw, display_name, fn_name)
    if verified:
        _draw_verified(draw, NAME_COL + nw + 12, PAD_V + 2, 34)

    # Handle
    draw.text((NAME_COL, PAD_V + NAME_FONT + 12),
              f"@{username}  ·  {_rel_time(posted_at)}",
              font=fn_han, fill=c["secondary"])

    # Body
    body_y = PAD_V + header_h + BODY_TOP
    for line in lines:
        draw.text((PAD_H, body_y), line, font=fn_body, fill=c["text"])
        body_y += BODY_LINE_H

    # Attached images grid
    if grid_img:
        card.paste(grid_img, (PAD_H, body_y + IMG_GAP // 2))
        body_y += grid_h

    # Timestamp line (Twitter format)
    ts_y = body_y + STATS_TOP
    views = stats.get("views") if stats else None
    draw.text((PAD_H, ts_y), _twitter_ts(posted_at, views),
              font=fn_ts, fill=c["secondary"])
    ts_y += 36

    # Separator
    sep_y = ts_y + STATS_TOP - 8
    draw.line([(PAD_H, sep_y), (CARD_W-PAD_H, sep_y)], fill=c["border"], width=2)

    # Stats row with real SVG icons
    st_y = sep_y + 20
    icos = _icons(size=32, color=c["secondary"], bg=c["bg"])
    sx   = PAD_H
    fn_s = fn_stat

    def _stat_item(icon_key: str, val: int):
        nonlocal sx
        ico = icos.get(icon_key)
        if ico:
            card.paste(ico, (sx, st_y + 2), ico)
            sx += ico.width + 10
        txt = _fmt_n(val)
        draw.text((sx, st_y), txt, font=fn_s, fill=c["secondary"])
        sx += _tw(draw, txt, fn_s) + 64

    if stats:
        _stat_item("comment", stats.get("replies", 0))
        _stat_item("retweet", stats.get("retweets", 0))
        _stat_item("like",    stats.get("likes", 0))
    # bookmark icon (PNG) + share text
    bm = icos.get("bookmark")
    if bm:
        card.paste(bm, (sx, st_y + 2), bm); sx += bm.width + 56
    draw.text((sx, st_y), "↗", font=fn_s, fill=c["secondary"])

    buf = BytesIO(); card.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ── TruthSocial card ──────────────────────────────────────────────────────────
def render_truthsocial_card(
    username:        str,
    display_name:    str,
    avatar_bytes:    Optional[bytes],
    text:            str,
    posted_at:       datetime,
    stats:           Optional[dict]   = None,
    attached_images: list[bytes]      = [],
) -> bytes:
    """
    Render a dark-navy TruthSocial-style card.
    Returns PNG bytes.
    """
    c       = _TS
    fn_name = _font(True,  NAME_FONT)
    fn_han  = _font(False, HANDLE_FONT)
    fn_body = _font(False, BODY_FONT)
    fn_ts   = _font(False, TS_FONT)
    fn_stat = _font(False, STATS_FONT)
    fn_bnd  = _font(True,  24)

    dummy = Image.new("RGB", (CARD_W, 100))
    dmeasure = ImageDraw.Draw(dummy)
    body_max_w = CARD_W - PAD_H * 2
    lines = _wrap(dmeasure, text, fn_body, body_max_w)
    if len(lines) > 16:
        lines = lines[:16]; lines[-1] = lines[-1][:60] + "…"
    body_h   = len(lines) * BODY_LINE_H
    header_h = max(AV_SIZE, NAME_FONT + 16 + HANDLE_FONT)

    grid_img = None; grid_h = 0
    if attached_images:
        grid_img = _compose_grid(attached_images, body_max_w, IMG_MAX_H)
        if grid_img: grid_h = grid_img.height + IMG_GAP

    total_h = (PAD_V + header_h + BODY_TOP + body_h
               + grid_h + STATS_TOP + 36 + STATS_TOP + STATS_H + PAD_V)
    total_h = max(total_h, 360)

    card = Image.new("RGB", (CARD_W, total_h), c["bg"])
    draw = ImageDraw.Draw(card)
    draw.rounded_rectangle((0,0,CARD_W-1,total_h-1), radius=RADIUS,
                           outline=c["border"], width=2)

    av = _make_circle(avatar_bytes, AV_SIZE, c["red"])
    card.paste(av, (PAD_H, PAD_V), av)
    draw.text((CARD_W-PAD_H-116, PAD_V+4), "TRUTH", font=fn_bnd, fill=c["red"])

    draw.text((NAME_COL, PAD_V), display_name, font=fn_name, fill=c["text"])
    nw = _tw(draw, display_name, fn_name)
    _draw_verified(draw, NAME_COL + nw + 12, PAD_V + 2, 34)

    draw.text((NAME_COL, PAD_V + NAME_FONT + 12),
              f"@{username}  ·  {_rel_time(posted_at)}",
              font=fn_han, fill=c["secondary"])

    body_y = PAD_V + header_h + BODY_TOP
    for line in lines:
        draw.text((PAD_H, body_y), line, font=fn_body, fill=c["text"])
        body_y += BODY_LINE_H

    if grid_img:
        card.paste(grid_img, (PAD_H, body_y + IMG_GAP // 2))
        body_y += grid_h

    ts_y = body_y + STATS_TOP
    draw.text((PAD_H, ts_y), _twitter_ts(posted_at), font=fn_ts, fill=c["secondary"])
    ts_y += 36

    sep_y = ts_y + STATS_TOP - 8
    draw.line([(PAD_H, sep_y), (CARD_W-PAD_H, sep_y)], fill=c["border"], width=2)

    st_y = sep_y + 20
    icos = _icons(size=32, color=c["secondary"], bg=c["bg"])
    sx   = PAD_H
    fn_s = fn_stat

    def _stat_item(icon_key: str, val: int):
        nonlocal sx
        ico = icos.get(icon_key)
        if ico: card.paste(ico, (sx, st_y+2), ico); sx += (ico.width+10 if ico else 0)
        txt = _fmt_n(val)
        draw.text((sx, st_y), txt, font=fn_s, fill=c["secondary"])
        sx += _tw(draw, txt, fn_s) + 64

    if stats:
        _stat_item("comment", stats.get("replies_count", 0))
        _stat_item("retweet", stats.get("reblogs_count", 0))
        _stat_item("like",    stats.get("favourites_count", 0))
    draw.text((sx, st_y), "⊡", font=fn_s, fill=c["secondary"]); sx += 60
    draw.text((sx, st_y), "↗", font=fn_s, fill=c["secondary"])

    buf = BytesIO(); card.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ── Avatar fetching (async, cached) ───────────────────────────────────────────
async def fetch_avatar(session: aiohttp.ClientSession,
                       url: str, cache_key: str) -> Optional[bytes]:
    if cache_key in _avatar_cache:
        return _avatar_cache[cache_key]
    if not url:
        _avatar_cache[cache_key] = None; return None
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status == 200:
                data = await r.read()
                _avatar_cache[cache_key] = data; return data
    except Exception as e:
        logger.debug("Avatar fetch failed (%s): %s", cache_key, e)
    _avatar_cache[cache_key] = None; return None

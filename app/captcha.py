"""Captcha generation, storage, and verification."""

import os
import io
import time
import secrets
import hashlib
import threading
from typing import Optional

from fastapi import HTTPException

from .config import JWT_SECRET_KEY, CAPTCHA_MAX_ATTEMPTS, CAPTCHA_TTL_SECONDS


class CaptchaStore:
    def __init__(self):
        self._items: dict[str, dict] = {}
        self._lock = threading.Lock()

    def put(self, captcha_id: str, answer: str, expires_at: float, image_bytes: bytes, image_content_type: str) -> None:
        hashed = hashlib.sha256((answer + JWT_SECRET_KEY).encode("utf-8")).hexdigest()
        with self._lock:
            self._items[captcha_id] = {
                "h": hashed,
                "e": float(expires_at),
                "a": 0,
                "b": bytes(image_bytes),
                "ct": str(image_content_type),
            }

    def get_image(self, captcha_id: str) -> tuple[Optional[bytes], Optional[str]]:
        now = time.time()
        with self._lock:
            item = self._items.get(captcha_id)
            if not item:
                return None, None
            if now > float(item.get("e") or 0):
                self._items.pop(captcha_id, None)
                return None, None
            raw = item.get("b")
            ct = item.get("ct")
            if not raw or not ct:
                return None, None
            return bytes(raw), str(ct)

    def verify(self, captcha_id: str, code: str) -> bool:
        now = time.time()
        with self._lock:
            item = self._items.get(captcha_id)
            if not item:
                return False
            if now > float(item.get("e") or 0):
                self._items.pop(captcha_id, None)
                return False
            attempts = int(item.get("a") or 0) + 1
            item["a"] = attempts
            if attempts > CAPTCHA_MAX_ATTEMPTS:
                self._items.pop(captcha_id, None)
                return False
            expected = str(item.get("h") or "")
            supplied = hashlib.sha256((str(code or "").strip().upper() + JWT_SECRET_KEY).encode("utf-8")).hexdigest()
            if supplied != expected:
                return False
            self._items.pop(captcha_id, None)
            return True


captcha_store = CaptchaStore()


def _captcha_alphabet() -> str:
    return "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_captcha_text(length: int) -> str:
    length = max(4, min(int(length), 8))
    alphabet = _captcha_alphabet()
    return "".join(secrets.choice(alphabet) for _ in range(length))


def captcha_image_bytes(text: str) -> tuple[str, bytes]:
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageFilter
    except Exception:
        svg = captcha_svg_fallback(text)
        return "image/svg+xml", svg.encode("utf-8")

    rnd = secrets.SystemRandom()
    font_size = 30
    char_step = 28
    pad = 18
    width = max(150, (len(text) * char_step) + (pad * 2))
    height = 56
    img = Image.new("RGB", (width, height), (248, 250, 252))
    draw = ImageDraw.Draw(img)
    for _ in range(6):
        x1 = rnd.randint(0, width)
        y1 = rnd.randint(0, height)
        x2 = rnd.randint(0, width)
        y2 = rnd.randint(0, height)
        color = (100, 116, 139)
        draw.line((x1, y1, x2, y2), fill=color, width=1)

    for _ in range(160):
        x = rnd.randint(0, width - 1)
        y = rnd.randint(0, height - 1)
        draw.point((x, y), fill=(226, 232, 240))

    font = None
    font_candidates = [
        os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "arial.ttf"),
        os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "segoeui.ttf"),
        "DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for fp in font_candidates:
        try:
            if fp and (fp.startswith("/") or fp.startswith("\\") or ":" in fp):
                if not os.path.exists(fp):
                    continue
            font = ImageFont.truetype(fp, font_size)
            break
        except Exception:
            font = None
    if font is None:
        font = ImageFont.load_default()

    start_x = (width - (len(text) * char_step)) // 2
    for idx, ch in enumerate(text):
        x = int(start_x + idx * char_step + rnd.randint(-1, 1))
        y = int((height - font_size) // 2 + rnd.randint(-2, 2))
        angle = rnd.randint(-16, 16)
        glyph = Image.new("RGBA", (char_step + 14, height), (0, 0, 0, 0))
        glyph_draw = ImageDraw.Draw(glyph)
        glyph_draw.text((7, y), ch, font=font, fill=(17, 24, 39, 255))
        glyph = glyph.rotate(angle, resample=Image.Resampling.BICUBIC, expand=1)
        img.paste(glyph, (x, 0), glyph)
    img = img.filter(ImageFilter.SMOOTH_MORE)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return "image/png", buf.getvalue()


def captcha_svg_fallback(text: str) -> str:
    rnd = secrets.SystemRandom()
    font_size = 24
    char_step = 26
    pad = 18
    width = max(150, (len(text) * char_step) + (pad * 2))
    height = 56
    start_x = (width - (len(text) * char_step)) // 2
    bg1 = "#f8fafc"
    bg2 = "#eef2ff"
    fg = "#111827"
    lines = []
    for _ in range(6):
        x1 = rnd.randint(0, width)
        y1 = rnd.randint(0, height)
        x2 = rnd.randint(0, width)
        y2 = rnd.randint(0, height)
        alpha = rnd.uniform(0.08, 0.18)
        lines.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#64748b" stroke-opacity="{alpha:.2f}" stroke-width="1"/>')
    chars = []
    for idx, ch in enumerate(text):
        x = int(start_x + idx * char_step + rnd.randint(-1, 1))
        y = 36 + rnd.randint(-2, 2)
        rot = rnd.randint(-16, 16)
        size = font_size + rnd.randint(-1, 2)
        chars.append(
            f'<text x="{x}" y="{y}" font-size="{size}" font-family="ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial" font-weight="700" fill="{fg}" transform="rotate({rot} {x} {y})">{ch}</text>'
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="{bg1}"/><stop offset="100%" stop-color="{bg2}"/></linearGradient></defs>'
        f'<rect width="{width}" height="{height}" rx="8" fill="url(#g)"/>'
        + "".join(lines)
        + "".join(chars)
        + "</svg>"
    )


def verify_captcha_or_raise(captcha_id: str, code: str) -> None:
    if not captcha_id or not code:
        raise HTTPException(status_code=400, detail="请先完成验证码验证")
    if not captcha_store.verify(captcha_id, code):
        raise HTTPException(status_code=400, detail="验证码错误或已过期")

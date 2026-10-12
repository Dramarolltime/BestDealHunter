#!/usr/bin/env python3
"""Render the original BestDealHunter logo (1080x1080 JPEG) used for the one-off brand post.

The mark is drawn from scratch with Pillow primitives: a price tag whose hole is a
crosshair (the "hunter"), in brand yellow on navy, with the BestDealHunter wordmark set in
Montserrat (SIL OFL, assets/fonts). No third-party artwork, logos or photos are used.

Output is deterministic for a given Pillow version; the committed file is the single source
of truth: the same bytes are uploaded to Cloudinary, posted via Buffer and shown on the site.
  python scripts/brand_logo.py            # writes images/brand/bestdealhunter-logo-1080.jpg
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
FONT = ROOT / "assets" / "fonts" / "Montserrat-Variable.ttf"
OUT = ROOT / "images" / "brand" / "bestdealhunter-logo-1080.jpg"
SIZE = 1080
NAVY, NAVY_DEEP = (16, 24, 48), (9, 14, 30)
YELLOW, WHITE, MUTED = (250, 204, 21), (255, 255, 255), (148, 163, 184)


def font(size, weight):
    face = ImageFont.truetype(str(FONT), size)
    face.set_variation_by_axes([weight])
    return face


def background():
    img = Image.new("RGB", (SIZE, SIZE), NAVY)
    px = img.load()
    cx, cy = SIZE / 2, SIZE * 0.38
    for y in range(SIZE):
        for x in range(SIZE):
            t = min(1.0, ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 / (SIZE * 0.78))
            px[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(NAVY, NAVY_DEEP))
    return img


def tag_layer(scale=4):
    """The tag drawn flat at `scale`x: rounded body, pointed left end, crosshair hole, '%'."""
    w, h, point = 600 * scale, 280 * scale, 150 * scale
    pad = 40 * scale
    layer = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x0, y0, x1, y1 = pad, pad, pad + w, pad + h
    r = 34 * scale
    d.rounded_rectangle((x0 + point, y0, x1, y1), radius=r, fill=YELLOW, corners=(False, True, True, False))
    d.polygon([(x0 + point, y0), (x0, (y0 + y1) / 2), (x0 + point, y1)], fill=YELLOW)
    cy = (y0 + y1) / 2
    hx, hr = x0 + 165 * scale, 66 * scale  # crosshair hole near the point
    d.ellipse((hx - hr, cy - hr, hx + hr, cy + hr), fill=NAVY)
    ring = 44 * scale
    d.ellipse((hx - ring, cy - ring, hx + ring, cy + ring), outline=YELLOW, width=10 * scale)
    arm, gap = hr - 10 * scale, 16 * scale
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        d.line((hx + dx * gap, cy + dy * gap, hx + dx * arm, cy + dy * arm), fill=YELLOW, width=9 * scale)
    d.ellipse((hx - 8 * scale, cy - 8 * scale, hx + 8 * scale, cy + 8 * scale), fill=YELLOW)
    d.text((x0 + 420 * scale, cy + 4 * scale), "%", font=font(170 * scale, 800), fill=NAVY, anchor="mm")
    return layer


def mark(img, scale=4):
    layer = tag_layer(scale).rotate(14, resample=Image.Resampling.BICUBIC, expand=True)
    layer = layer.resize((layer.width // scale, layer.height // scale), Image.Resampling.LANCZOS)
    x, y = (SIZE - layer.width) // 2, 395 - layer.height // 2
    alpha = layer.getchannel("A")
    glow = Image.new("L", img.size, 0)
    glow.paste(alpha, (x, y))
    img.paste(YELLOW, (0, 0), glow.filter(ImageFilter.GaussianBlur(45)).point(lambda v: v * 45 // 100))
    img.paste(layer, (x, y), layer)


def wordmark(img):
    draw = ImageDraw.Draw(img)
    face = font(118, 800)
    parts = [("Best", WHITE), ("Deal", YELLOW), ("Hunter", WHITE)]
    widths = [draw.textlength(t, font=face) for t, _ in parts]
    x = (SIZE - sum(widths)) / 2
    for (text, color), width in zip(parts, widths):
        draw.text((x, 760), text, font=face, fill=color, anchor="ls")
        x += width
    tagline = "VERIFIED DEALS  ·  LESS SEARCHING"
    tface = font(36, 600)
    draw.text((SIZE / 2, 845), tagline, font=tface, fill=MUTED, anchor="ms")
    draw.rounded_rectangle((SIZE / 2 - 70, 900, SIZE / 2 + 70, 908), radius=4, fill=YELLOW)


def render():
    img = background()
    mark(img)
    wordmark(img)
    return img


def main(out=OUT):
    out.parent.mkdir(parents=True, exist_ok=True)
    render().save(out, "JPEG", quality=92, optimize=True, progressive=False)
    print(out.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())

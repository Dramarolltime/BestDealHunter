#!/usr/bin/env python3
"""Generate consistent deal cards from explicitly authorized local product photos.

Input: deals.json entries with image_source (local images/source/...).
Output: images/generated/... and deal.image, without overwriting existing images.
No scraping, AI product fabrication, or unlicensed image downloads.
"""
import hashlib
import json
import re
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parents[1]
FEED = ROOT / "deals.json"
SOURCE = ROOT / "images" / "source"
OUTPUT = ROOT / "images" / "generated"
FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
REGULAR = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")

def font(size, bold=True):
    path = FONT if bold else REGULAR
    return ImageFont.truetype(str(path), size) if path.exists() else ImageFont.load_default()

def wrap(draw, text, face, max_width, max_lines=3):
    words = str(text).split()
    lines, current = [], ""
    for word in words:
        candidate = (current + " " + word).strip()
        if draw.textbbox((0, 0), candidate, font=face)[2] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while lines[-1] and draw.textbbox((0, 0), lines[-1] + "…", font=face)[2] > max_width:
            lines[-1] = lines[-1][:-1]
        lines[-1] += "…"
    return lines

def generate(deal, source_path, dest):
    with Image.open(source_path) as src:
        photo = ImageOps.exif_transpose(src).convert("RGB")
    canvas = Image.new("RGB", (1080, 1080), "#f5f7fb")
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, 1080, 122), fill="#101828")
    draw.text((55, 36), "BestDealHunter", fill="#ffffff", font=font(55))
    draw.rounded_rectangle((790, 34, 1024, 92), radius=18, fill="#fbbf24")
    draw.text((817, 49), "DEAL FIND", fill="#101828", font=font(28))
    photo.thumbnail((870, 570), Image.Resampling.LANCZOS)
    canvas.paste(photo, ((1080 - photo.width) // 2, 145 + (570 - photo.height) // 2))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((38, 745, 1042, 1040), radius=28, fill="#ffffff")
    title_font = font(39)
    for i, line in enumerate(wrap(draw, deal["title"], title_font, 925, 2)):
        draw.text((70, 765 + i * 49), line, fill="#101828", font=title_font)
    price = float(deal["price"])
    original = float(deal["original_price"])
    pct = round((original - price) * 100 / original)
    draw.text((70, 890), f"${price:,.2f}", fill="#101828", font=font(72))
    draw.text((470, 917), f"Was ${original:,.2f}", fill="#667085", font=font(29, False))
    draw.rounded_rectangle((772, 894, 1008, 982), radius=18, fill="#dc2626")
    draw.text((794, 918), f"{pct}% OFF", fill="#ffffff", font=font(34))
    draw.text((70, 1000), "Price and availability may change", fill="#667085", font=font(21, False))
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest, quality=90, optimize=True)

def main():
    deals = json.loads(FEED.read_text(encoding="utf-8"))
    if not isinstance(deals, list):
        raise ValueError("deals.json must be a list")
    made = 0
    for deal in deals:
        if not isinstance(deal, dict) or deal.get("image"):
            continue
        raw = deal.get("image_source")
        if not isinstance(raw, str) or not re.fullmatch(r"images/source/[A-Za-z0-9_.-]+\.(?:png|jpe?g|webp)", raw, re.I):
            print(f"Skipping missing/unauthorized photo: {deal.get('title', 'Untitled')}")
            continue
        source = ROOT / raw
        if not source.is_file() or not source.resolve().is_relative_to(SOURCE.resolve()):
            print(f"Source photo not found: {raw}")
            continue
        try:
            price, original = float(deal["price"]), float(deal["original_price"])
            if not (0 < price < original):
                continue
            key = hashlib.sha256((str(deal.get("url", "")) + raw).encode()).hexdigest()[:16]
            dest = OUTPUT / f"deal-{key}.jpg"
            generate(deal, source, dest)
            deal["image"] = str(dest.relative_to(ROOT))
            made += 1
        except (KeyError, TypeError, ValueError, OSError) as exc:
            print(f"Could not generate {deal.get('title', 'Untitled')}: {exc}")
    if made:
        FEED.write_text(json.dumps(deals, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Generated {made} new deal graphics; existing images preserved.")

if __name__ == "__main__":
    main()

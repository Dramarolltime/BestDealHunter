#!/usr/bin/env python3
"""AI product pictures for new deal cards, from OpenAI's image API (owner approved, 2026-10-14).

For each newly published deal that has no card yet, ask OpenAI for a studio-style picture of the
product (no text, prices, logos or brand names), save it as images/source/ai-<card id>.jpg, and point
the deal's `image_source` at it with `image_source_kind: "ai_illustration"`. The card generator then
lays out the bold card around the picture, labelled "ILLUSTRATION", and prints every number from the
verified deal itself; the AI never draws a price.

Dormant (no network, exit 0) unless OPENAI_API_KEY is set. Any failure leaves the deal on the standard
card. At most --max pictures per run; the owner's monthly budget limit in OpenAI caps the spend.

  OPENAI_API_KEY=... python scripts/ai_product_art.py [--max 3]
"""
import argparse
import base64
import io
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image

from deal_graphics import AI_KIND, card_id

ROOT = Path(__file__).resolve().parents[1]
FEED = ROOT / "deals.json"
SOURCE = ROOT / "images" / "source"
API = "https://api.openai.com/v1/images/generations"
DEFAULT_MODEL = "gpt-image-1"
PROMPT = ("Photorealistic studio product photo of: {title} ({category}). One product, centered, fully visible, "
          "on a dark navy blue background with a soft blue spotlight glow behind it, clean premium lighting. "
          "Absolutely no text, letters, numbers, prices, logos, brand names, labels or watermarks anywhere in "
          "the image.")


def http(request, timeout=120):
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return json.loads(resp.read())


def generate(deal, api_key, model=DEFAULT_MODEL, transport=http):
    """JPEG bytes of a picture of the deal's product."""
    body = {"model": model, "n": 1, "size": "1024x1024", "quality": "medium",
            "prompt": PROMPT.format(title=deal["title"], category=deal.get("category") or "product")}
    request = urllib.request.Request(API, data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {api_key}",
                                              "Content-Type": "application/json"})
    result = transport(request)
    item = (result.get("data") or [{}])[0]
    if not item.get("b64_json"):
        raise ValueError("no image in the response")
    with Image.open(io.BytesIO(base64.b64decode(item["b64_json"]))) as img:
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=90, optimize=True)
    return buf.getvalue()


def needs_art(deal):
    """Newly published deals still waiting for their first card, with no picture yet."""
    return (isinstance(deal, dict) and deal.get("publication_approved") is True and not deal.get("image")
            and not deal.get("image_source") and deal.get("store") != "Amazon")


def run(deals, api_key, limit, model=DEFAULT_MODEL, transport=http, log=print):
    made = 0
    for deal in deals:
        if not needs_art(deal):
            continue
        if made >= limit:
            log(f"LIMIT {deal.get('title')}: {limit} pictures per run; it keeps the standard card")
            continue
        path = SOURCE / f"ai-{card_id(deal)}.jpg"
        try:
            data = generate(deal, api_key, model, transport)
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            # never print the request (it carries the key); the HTTP status is enough
            reason = f"HTTP {exc.code}" if isinstance(exc, urllib.error.HTTPError) else type(exc).__name__
            log(f"FAILED {deal.get('title')}: {reason}; it keeps the standard card")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        deal["image_source"] = str(path.relative_to(ROOT))
        deal["image_source_kind"] = AI_KIND
        made += 1
        log(f"PICTURE {deal.get('title')}: {deal['image_source']}")
    return made


def main(argv=None, env=None, transport=http):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--max", type=int, default=3, help="pictures per run (default 3)")
    args = parser.parse_args(argv)
    env = os.environ if env is None else env
    key = env.get("OPENAI_API_KEY", "").strip()
    if not key:
        print("OPENAI_API_KEY not set: no AI pictures; deals use the standard card.")
        return 0
    deals = json.loads(FEED.read_text(encoding="utf-8"))
    made = run(deals, key, args.max, env.get("OPENAI_IMAGE_MODEL") or DEFAULT_MODEL, transport)
    if made:
        FEED.write_text(json.dumps(deals, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"AI pictures made: {made}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

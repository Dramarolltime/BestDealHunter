#!/usr/bin/env python3
"""AI product pictures for new deal cards, from OpenAI's image API (owner approved, 2026-10-14).

For each newly published deal that has no card yet, ask OpenAI for a studio-style picture of the
product (no text, prices, logos or brand names), save it as images/source/ai-<card id>.jpg, and point
the deal's `image_source` at it with `image_source_kind: "ai_scene"`. The card generator then
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

from deal_graphics import AI_SCENE, AI_SCENE_FROM_PHOTO, LISTING_PHOTO, card_id

ROOT = Path(__file__).resolve().parents[1]
FEED = ROOT / "deals.json"
SOURCE = ROOT / "images" / "source"
API = "https://api.openai.com/v1/images/generations"
DEFAULT_MODEL = "gpt-image-1"
# Alpha's look (owner, 2026-10-15): a realistic lifestyle photo with the product as the hero, filling the card.
PROMPT = ("Photorealistic lifestyle product photograph of: {title} ({category}). The product is the clear hero, "
          "sharp and fully visible in the upper-middle of the frame, shown in a bright, tasteful, real setting "
          "where it is normally used, soft natural window light, premium magazine style, shallow depth of field. "
          "Show the product exactly as that kind of product normally looks; do not add screens, displays or "
          "parts it would not have. Keep the bottom third of the frame calm and uncluttered. "
          "Absolutely no text, letters, numbers, prices, logos, brand names, labels or watermarks anywhere in "
          "the image.")


def http(request, timeout=120):
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return json.loads(resp.read())


EDIT_API = "https://api.openai.com/v1/images/edits"
SCENE_FROM_PHOTO_PROMPT = (
    "Place the exact product shown in this photo, completely unchanged (same shape, colors, materials, "
    "proportions, stitching and details), into a bright, tasteful, realistic lifestyle setting where it is "
    "normally used. The product is the clear hero, sharp and fully visible, about half the frame's height, "
    "centered slightly above the middle with generous space around it, soft "
    "natural window light, premium magazine style, shallow depth of field. Do not redesign, recolor or add to "
    "the product. Keep the bottom third of the frame calm. Add no text, prices, logos or watermarks.")


SCENE_FROM_SCREENSHOT_PROMPT = (
    "This is a screenshot of a store's product page. Take the exact product it shows for sale, completely "
    "unchanged (same shape, colors, materials, proportions and details), and photograph it in a bright, tasteful, "
    "realistic lifestyle setting where it is normally used. Show only the product in the scene: none of the web "
    "page, text, prices, buttons or logos. The product is the clear hero, about half the frame's height, centered "
    "slightly above the middle with generous space around it, soft natural window light, premium magazine style. "
    "Keep the bottom third of the frame calm. Add no text, prices, logos or watermarks.")


def multipart(fields, files):
    """multipart/form-data body for the image edit API."""
    boundary = "bdh" + base64.b16encode(os.urandom(8)).decode()
    out = io.BytesIO()
    for name, value in fields.items():
        out.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())
    for name, (filename, data, mime) in files.items():
        out.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{filename}\"\r\n"
                  f"Content-Type: {mime}\r\n\r\n".encode())
        out.write(data)
        out.write(b"\r\n")
    out.write(f"--{boundary}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


def scene_from_photo(photo_jpeg, api_key, model=DEFAULT_MODEL, transport=http, prompt=SCENE_FROM_PHOTO_PROMPT):
    """JPEG bytes of a lifestyle scene built from a real product photo or page screenshot (OpenAI image edit)."""
    with Image.open(io.BytesIO(photo_jpeg)) as img:  # screenshots may be PNG/WebP; the API gets a JPEG
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=92)
        photo_jpeg = buf.getvalue()
    body, content_type = multipart(
        {"model": model, "prompt": prompt, "size": "1024x1536", "quality": "medium",
         "input_fidelity": "high", "n": "1"},
        {"image": ("product.jpg", photo_jpeg, "image/jpeg")})
    request = urllib.request.Request(EDIT_API, data=body, method="POST",
                                     headers={"Authorization": f"Bearer {api_key}", "Content-Type": content_type})
    item = (transport(request, timeout=180).get("data") or [{}])[0]
    if not item.get("b64_json"):
        raise ValueError("no image in the response")
    with Image.open(io.BytesIO(base64.b64decode(item["b64_json"]))) as img:
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=90, optimize=True)
    return buf.getvalue()


def generate(deal, api_key, model=DEFAULT_MODEL, transport=http):
    """JPEG bytes of a picture of the deal's product."""
    body = {"model": model, "n": 1, "size": "1024x1536", "quality": "medium",
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
    """Newly published deals still waiting for their first card, with no picture and no listing photo."""
    return (isinstance(deal, dict) and deal.get("publication_approved") is True and not deal.get("image")
            and not deal.get("image_source") and not deal.get("listing_photo_url") and not deal.get("photo_reference")
            and deal.get("store") != "Amazon")


IMAGE_MAGIC = (b"\xff\xd8\xff", b"\x89PNG", b"RIFF")


def fetch_photo(url, opener=urllib.request.urlopen):
    """JPEG bytes of a listing photo from eBay's image host (re-encoded only; the picture is not edited)."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme != "https" or not (parsed.hostname or "").endswith("ebayimg.com"):
        raise ValueError("not an eBay image URL")
    with opener(urllib.request.Request(url, headers={"User-Agent": "BestDealHunter"}), timeout=30) as resp:
        data = resp.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024 or not data.startswith(IMAGE_MAGIC):
        raise ValueError("not an image")
    with Image.open(io.BytesIO(data)) as img:
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=92, optimize=True)
    return buf.getvalue()


def listing_photos(deals, limit=10, opener=urllib.request.urlopen, log=print):
    """Use the real listing photo for every deal that has one and isn't showing it yet (replacing AI pictures)."""
    made = 0
    for deal in deals:
        url = deal.get("listing_photo_url") if isinstance(deal, dict) else None
        if url and not deal.get("listing_photo_source") and deal.get("image_source_kind") == LISTING_PHOTO:
            deal["listing_photo_source"] = deal["image_source"]  # fetched before this field existed
        if not url or deal.get("listing_photo_source") or made >= limit:
            continue
        try:
            data = fetch_photo(url, opener)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            log(f"FAILED listing photo for {deal.get('title')}: {type(exc).__name__}; keeps its current card")
            continue
        path = SOURCE / f"listing-{card_id(deal)}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        deal["image_source"], deal["image_source_kind"] = str(path.relative_to(ROOT)), LISTING_PHOTO
        deal["listing_photo_source"] = deal["image_source"]
        if str(deal.get("image", "")).startswith("images/generated/deal-"):
            deal.pop("image")  # redraw the card with the real photo
        made += 1
        log(f"LISTING PHOTO {deal.get('title')}: {deal['image_source']}")
    return made


def scenes_from_photos(deals, api_key, limit, model=DEFAULT_MODEL, transport=http, log=print):
    """Owner's choice (2026-10-15, option B): a lifestyle scene built by AI from the real listing photo, on the
    website and Instagram. A deal keeps its real listing photo card when the AI call fails or the budget runs out."""
    made = 0
    for deal in deals:
        if not isinstance(deal, dict) or deal.get("card_source") == "submitted":
            continue
        if deal.get("image_source_kind") == LISTING_PHOTO:
            source, prompt = ROOT / str(deal.get("listing_photo_source") or deal.get("image_source")), \
                SCENE_FROM_PHOTO_PROMPT
        elif deal.get("photo_reference") and not deal.get("image_source"):
            source, prompt = ROOT / str(deal["photo_reference"]), SCENE_FROM_SCREENSHOT_PROMPT  # form deals
        else:
            continue
        if made >= limit:
            log(f"LIMIT {deal.get('title')}: {limit} scenes per run; keeps the listing photo for now")
            continue
        try:
            data = scene_from_photo(source.read_bytes(), api_key, model, transport, prompt)
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            reason = f"HTTP {exc.code}" if isinstance(exc, urllib.error.HTTPError) else type(exc).__name__
            log(f"FAILED scene for {deal.get('title')}: {reason}; keeps its current card")
            continue
        path = SOURCE / f"scene-{card_id(deal)}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        deal["image_source"], deal["image_source_kind"] = str(path.relative_to(ROOT)), AI_SCENE_FROM_PHOTO
        if str(deal.get("image", "")).startswith("images/generated/deal-"):
            deal.pop("image")  # redraw the card with the scene
        made += 1
        log(f"SCENE {deal.get('title')}: {deal['image_source']}")
    return made


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
        deal["image_source_kind"] = AI_SCENE
        made += 1
        log(f"PICTURE {deal.get('title')}: {deal['image_source']}")
    return made


def main(argv=None, env=None, transport=http, opener=urllib.request.urlopen):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--max", type=int, default=3, help="pictures per run (default 3)")
    args = parser.parse_args(argv)
    env = os.environ if env is None else env
    deals = json.loads(FEED.read_text(encoding="utf-8"))
    photos = listing_photos(deals, opener=opener)
    key = env.get("OPENAI_API_KEY", "").strip()
    made = scenes = 0
    if key:
        model = env.get("OPENAI_IMAGE_MODEL") or DEFAULT_MODEL
        scenes = scenes_from_photos(deals, key, args.max, model, transport)
        made = run(deals, key, args.max, model, transport)
    else:
        print("OPENAI_API_KEY not set: no AI pictures; deals show their listing photo or the standard card.")
    if photos or made or scenes or any(d.get("listing_photo_source") for d in deals if isinstance(d, dict)):
        FEED.write_text(json.dumps(deals, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Listing photos: {photos}; scenes from listing photos: {scenes}; AI pictures made: {made}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Give every published deal without an image a branded card from deal_graphics.

Input: deals.json entries with no `image`. A deal may name an authorized local photo in
`image_source` (images/source/...); otherwise the typographic card (no product picture)
is used. Every price and percentage comes from the validated deal record.
Output: images/generated/deal-<id>-feed.jpg plus a square variant; sets deal.image to the
feed card. Existing images are never overwritten. No scraping, AI product imagery or
unlicensed downloads, and nothing is posted anywhere.
"""
import json
from pathlib import Path

from deal_graphics import CardError, card_id, render_card, save
from validate_deals import validate

ROOT = Path(__file__).resolve().parents[1]
FEED = ROOT / "deals.json"
OUTPUT = ROOT / "images" / "generated"


def generate_missing(deals, out=OUTPUT, log=print):
    made = 0
    for deal in deals:
        if not isinstance(deal, dict) or deal.get("image"):
            continue
        try:
            validate([deal])
            feed = save(render_card(deal, "feed"), out / f"deal-{card_id(deal)}-feed.jpg")
            save(render_card(deal, "square"), out / f"deal-{card_id(deal)}-square.jpg")
        except (CardError, ValueError, OSError) as exc:
            log(f"Could not generate {deal.get('title', 'Untitled')}: {exc}")
            continue
        deal["image"] = str(feed.relative_to(ROOT)) if feed.is_relative_to(ROOT) else str(feed)
        made += 1
    return made


def main():
    deals = json.loads(FEED.read_text(encoding="utf-8"))
    if not isinstance(deals, list):
        raise ValueError("deals.json must be a list")
    made = generate_missing(deals)
    if made:
        FEED.write_text(json.dumps(deals, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Generated {made} new deal graphics; existing images preserved.")


if __name__ == "__main__":
    main()

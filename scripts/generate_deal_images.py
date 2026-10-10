#!/usr/bin/env python3
"""Give every published deal without an image a branded card from deal_graphics.

Input: deals.json entries with no `image`. A deal may name an authorized local photo in
`image_source` (images/source/...); otherwise the typographic card (no product picture)
is used. Every price and percentage comes from the validated deal record.
Output: images/generated/deal-<id>-square.jpg (shown on the site, which displays 1:1) plus
the 4:5 deal-<id>-feed.jpg for Instagram; sets deal.image to the square card.
Existing images are never overwritten, except with --replace-legacy (owner approved, #1):
deals that are verified and active *today* get their branded card in place of a legacy
non-card image (baked-price composites, illustrations). Expired deals keep theirs.
No scraping, AI product imagery or unlicensed downloads, and nothing is posted anywhere.
"""
import argparse
import json
import re
from pathlib import Path

from deal_graphics import CardError, card_id, render_card, save
from validate_deals import validate

ROOT = Path(__file__).resolve().parents[1]
FEED = ROOT / "deals.json"
OUTPUT = ROOT / "images" / "generated"
CARD = re.compile(r"images/generated/deal-[0-9a-f]{12}-(?:feed|square)\.jpg")


def generate_missing(deals, out=OUTPUT, log=print, replace_legacy=False):
    made = 0
    for deal in deals:
        if not isinstance(deal, dict):
            continue
        image = deal.get("image")
        if image and not (replace_legacy and not CARD.fullmatch(str(image))):
            continue
        try:
            validate([deal])
            save(render_card(deal, "feed"), out / f"deal-{card_id(deal)}-feed.jpg")
            square = save(render_card(deal, "square"), out / f"deal-{card_id(deal)}-square.jpg")
        except (CardError, ValueError, OSError) as exc:
            log(f"Could not generate {deal.get('title', 'Untitled')}: {exc}")
            continue
        if image:
            log(f"Replaced legacy image {image} for {deal.get('title', 'Untitled')}")
            deal.pop("image_disclosure", None)  # described the old illustration, not the card
        deal["image"] = str(square.relative_to(ROOT)) if square.is_relative_to(ROOT) else str(square)
        made += 1
    return made


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--replace-legacy", action="store_true",
                        help="also replace non-card images of deals that are verified and active today")
    args = parser.parse_args(argv)
    deals = json.loads(FEED.read_text(encoding="utf-8"))
    if not isinstance(deals, list):
        raise ValueError("deals.json must be a list")
    made = generate_missing(deals, replace_legacy=args.replace_legacy)
    if made:
        FEED.write_text(json.dumps(deals, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Generated {made} new deal graphics; existing images preserved.")


if __name__ == "__main__":
    main()

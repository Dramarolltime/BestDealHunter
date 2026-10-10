#!/usr/bin/env python3
"""Buffer -> Instagram publisher. DRY-RUN ONLY in this version.

What it does now:
  * decides which published deals are eligible to post and explains every skip;
  * builds the exact post (Cloudinary JPEG URL + caption with disclosures);
  * de-duplicates against posts.json so a deal/price is never queued twice;
  * writes the plan to stdout and the GitHub job summary.

What it cannot do yet: talk to Buffer. `BufferClient` is deliberately not implemented
until Alpha confirms the Buffer API schema and how GitHub Actions authenticates (#1).
Even then, a live run needs ALL of: repo variable INSTAGRAM_PUBLISH_ENABLED == "true",
the --live flag, a BUFFER_API_KEY secret, and a deal with `publication_approved: true`.
A post only counts as published when Buffer later reports it as `sent`.

Usage:
  python scripts/publish_buffer.py            # dry run (default)
"""
import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

from deal_graphics import CardError, card_facts
from validate_deals import validate

ROOT = Path(__file__).resolve().parents[1]
POSTS = ROOT / "posts.json"
CHANNEL = "best_dealhunter"  # existing Buffer-connected Instagram channel (per Alpha)
CARD = re.compile(r"images/generated/deal-([0-9a-f]{12})-(?:feed|square)\.jpg")
CAPTION_LIMIT, HASHTAG_LIMIT = 2200, 30
MAX_POSTS_PER_RUN = 1


def post_key(deal):
    """One post per deal URL and price: a later, different price may be posted again."""
    return hashlib.sha256(f"{deal.get('url', '')}|{deal.get('price')}".encode()).hexdigest()[:16]


def hosted_urls(mapping):
    return {m.get("image"): m.get("cloudinary_url") for m in mapping if isinstance(m, dict)}


def feed_card(deal):
    """The 4:5 Instagram card for a deal whose image is one of its branded cards (site shows the square)."""
    match = CARD.fullmatch(str(deal.get("image") or ""))
    return f"images/generated/deal-{match.group(1)}-feed.jpg" if match else None


def eligibility(deal, hosted, posted_keys, today):
    """Return (eligible, reason). Every rule must pass; the first failure is reported."""
    try:
        validate([deal], today=today)
    except ValueError as exc:
        return False, f"fails validation ({exc})"
    if deal.get("publication_approved") is not True:
        return False, "not approved for posting (needs publication_approved: true from Alpha/owner)"
    if deal.get("store") == "Amazon":
        return False, "Amazon prices need approved Amazon data access before social posting"
    image = feed_card(deal)
    if not image:
        return False, "image is not a generated branded card (images/generated/deal-<id>-square/feed.jpg)"
    url = hosted.get(image) or ""
    if not (url.startswith("https://res.cloudinary.com/") and url.lower().endswith((".jpg", ".jpeg"))):
        return False, "4:5 feed card is not hosted on Cloudinary as a JPEG"
    if post_key(deal) in posted_keys:
        return False, "already queued or posted at this price"
    return True, "eligible"


def caption(deal):
    facts = card_facts(deal)
    lines = [
        f"🔥 {facts['percent']}% OFF at {facts['store']}",
        "",
        facts["title"],
        "",
        f"💰 {facts['price']} ({facts['reference_label']} {facts['reference']}) · Save {facts['savings']}",
        f"✅ Price verified {facts['verified']}. Prices and availability may change; check the retailer before buying.",
        "",
        "🔗 Link in bio",
        "",
        "#ad #affiliate #deals #bestdealhunter",
        "BestDealHunter may earn a commission from qualifying purchases.",
    ]
    text = "\n".join(lines)
    if len(text) > CAPTION_LIMIT or text.count("#") > HASHTAG_LIMIT:
        raise CardError("caption exceeds Instagram limits")
    return text


def plan(deals, mapping, posts, today):
    hosted = hosted_urls(mapping)
    posted_keys = {p.get("key") for p in posts if isinstance(p, dict)}
    ready, skipped = [], []
    for deal in deals:
        if not isinstance(deal, dict):
            continue
        ok, reason = eligibility(deal, hosted, posted_keys, today)
        if not ok:
            skipped.append((deal.get("title", "Untitled"), reason))
            continue
        try:
            post = {"key": post_key(deal), "channel": CHANNEL, "deal_url": deal["url"], "title": deal["title"],
                    "price": deal["price"], "image_url": hosted[feed_card(deal)], "text": caption(deal)}
        except CardError as exc:
            skipped.append((deal.get("title", "Untitled"), str(exc)))
            continue
        if len(ready) >= MAX_POSTS_PER_RUN:
            skipped.append((deal["title"], f"run limit of {MAX_POSTS_PER_RUN} post reached; next run"))
            continue
        ready.append(post)
        posted_keys.add(post["key"])
    return ready, skipped


class BufferClient:
    """Placeholder. Implement only after the Buffer API schema and auth are confirmed (#1).

    Expected shape: create a queued Instagram post on channel `best_dealhunter` with one
    image URL and caption, returning Buffer's post id; and read a post's status so the
    log is updated to `sent` or `error` only from Buffer's own answer.
    """

    def __init__(self, api_key):
        raise NotImplementedError("Buffer client not enabled: API schema/auth not yet confirmed by Alpha")


def render_report(ready, skipped, live):
    mode = "LIVE" if live else "DRY RUN (nothing sent to Buffer)"
    out = [f"## Buffer publisher: {mode}", "", f"Channel: `{CHANNEL}` · Eligible this run: **{len(ready)}**", ""]
    for post in ready:
        out += [f"### Would post: {post['title']}", f"- Image: {post['image_url']}", f"- Dedupe key: `{post['key']}`",
                "", "```", post["text"], "```", ""]
    if skipped:
        out += ["### Not eligible", ""] + [f"- **{title}**: {reason}" for title, reason in skipped]
    return "\n".join(out)


def load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def main(argv=None):
    parser = argparse.ArgumentParser(description="Plan (and later publish) Instagram posts via Buffer")
    parser.add_argument("--live", action="store_true", help="attempt real posting (refused unless every gate passes)")
    args = parser.parse_args(argv)

    deals = load(ROOT / "deals.json", [])
    mapping = load(ROOT / "cloudinary-images.json", [])
    posts = load(POSTS, [])
    ready, skipped = plan(deals, mapping, posts, date.today())
    report = render_report(ready, skipped, live=False)
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(report + "\n")

    if not args.live:
        return 0
    if os.environ.get("INSTAGRAM_PUBLISH_ENABLED") != "true":
        print("Live posting refused: INSTAGRAM_PUBLISH_ENABLED is not 'true' (kill switch is on).")
        return 2
    if not os.environ.get("BUFFER_API_KEY"):
        print("Live posting refused: BUFFER_API_KEY secret is not configured.")
        return 2
    BufferClient(os.environ["BUFFER_API_KEY"])  # raises NotImplementedError until approved
    return 2


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Buffer -> Instagram publisher. Dry run by default; live posting stays locked.

A dry run decides which published deals are eligible, explains every skip, and builds the
exact post (Cloudinary 4:5 JPEG URL + caption with disclosures) without contacting Buffer.

Duplicate protection: posts.json is a ledger keyed by deal URL + price. A row is written
BEFORE Buffer is called (Buffer's createPost has no idempotency key), and any existing row
blocks that key, so the same deal at the same price can never be queued twice, even if a
run crashes mid-call.

Live (--live) needs ALL of: repo variable INSTAGRAM_PUBLISH_ENABLED == "true" (the owner's
on/off switch), the BUFFER_API_KEY secret, the BUFFER_CHANNEL_ID variable, and a deal with
`publication_approved: true`, which auto_publish.py sets for deals verified on the retailer's
own product page in the last 2 days (owner policy 2026-10-13: no manual approval). A post is recorded as `sent` only when Buffer says so.

--draft-check <card id> is a separate, fail-closed path (see draft_check()): it creates one
Buffer DRAFT with saveToDraft + schedulingType "notification", verifies Buffer's response
says status "draft" (anything else stops the run), and records it only in
buffer-draft-checks.json. It never reads publication_approved, never writes posts.json and
never calls publish().

Usage:
  python scripts/publish_buffer.py                                  # dry run
  python scripts/publish_buffer.py --approve-for-dry-run <card id>  # dry run one deal as if approved
  python scripts/publish_buffer.py --draft-check <card id>          # one verified Buffer draft (needs key + channel)
"""
import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

from deal_graphics import CardError, as_of_text, card_facts
from links import disclosure, is_affiliate_link
from validate_deals import validate

ROOT = Path(__file__).resolve().parents[1]
POSTS = ROOT / "posts.json"
DRAFT_CHECKS = ROOT / "buffer-draft-checks.json"  # test drafts only; never mixed with the live ledger
CARD_ID = re.compile(r"[0-9a-f]{12}")
CHANNEL = "best_dealhunter"  # existing Buffer-connected Instagram channel (per Alpha)
CARD = re.compile(r"images/generated/deal-([0-9a-f]{12})-(?:feed|square)\.jpg")
CAPTION_LIMIT, HASHTAG_LIMIT = 2200, 30
MAX_POSTS_PER_RUN = 1
# Pictures that show the real product: the listing photo, or an AI scene built from it / from the page screenshot.
REAL_PRODUCT_KINDS = ("listing_photo", "ai_scene_from_photo")
MAX_POSTS_PER_DAY = 12  # hourly runs: at most 12 Instagram posts in any 24 hours
MAX_POST_AGE_HOURS = 3  # an auto-published deal is posted only while its page check is this fresh
# Amazon Associates Program Policies (read 2026-10-13): "your Site may only show prices and availability if:
# (a) we serve the link in which that price and availability data are displayed, or (b) you obtain Product
# pricing and availability data via Creators API or PA API". A social media account counts as a "Site".
AMAZON_PRICE_SOURCES = {"amazon_creators_api", "amazon_pa_api"}
AMAZON_RULE = ("Amazon prices may only come from Amazon's Creators API / PA API (Associates Program Policies); "
               "this deal's price came from somewhere else")
ASIN = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})")
LOCK_PREFIX = "deal-post/"  # git tags on GitHub: a durable one-post-per-key lock across runs


def product_id(deal):
    """The product, not the link: an Amazon ASIN, else host + path without the query string."""
    from urllib.parse import urlparse
    url = str(deal.get("url", ""))
    asin = deal.get("asin") or (ASIN.search(url).group(1) if ASIN.search(url) else None)
    if asin:
        return f"amazon:{asin}"
    parsed = urlparse(url)
    return f"{(parsed.hostname or '').removeprefix('www.')}{parsed.path.rstrip('/').lower()}"


def post_key(deal):
    """One post per product and price: a later, different price may be posted again."""
    return hashlib.sha256(f"{product_id(deal)}|{deal.get('price')}".encode()).hexdigest()[:16]


def price_check_age_hours(deal, now):
    """Hours since the product page was checked, for auto-published deals; None when unknown."""
    from datetime import datetime
    stamp = deal.get("auto_published_at")
    if not stamp:
        return None
    try:
        checked = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (now - checked).total_seconds() / 3600


def hosted_urls(mapping):
    return {m.get("image"): m.get("cloudinary_url") for m in mapping if isinstance(m, dict)}


def feed_card(deal):
    """The 4:5 Instagram card for a deal whose image is one of its branded cards (site shows the square)."""
    match = CARD.fullmatch(str(deal.get("image") or ""))
    return f"images/generated/deal-{match.group(1)}-feed.jpg" if match else None


def eligibility(deal, hosted, posted_keys, today, now=None):
    """Return (eligible, reason). Every rule must pass; the first failure is reported."""
    try:
        validate([deal], today=today)
    except ValueError as exc:
        return False, f"fails validation ({exc})"
    if deal.get("publication_approved") is not True:
        # set automatically by auto_publish.py for deals verified on the retailer's product page
        return False, "not approved for posting (publication_approved is not true)"
    if deal.get("store") == "Amazon" and deal.get("evidence_method") not in AMAZON_PRICE_SOURCES:
        return False, AMAZON_RULE
    found = str(deal.get("approved_by", "")).startswith(("automatic", "owner deal form"))  # pipeline deals
    if found and deal.get("image_source_kind") not in REAL_PRODUCT_KINDS and deal.get("card_source") != "submitted":
        return False, "waiting for the real listing photo on its card (owner: the real product must show)"
    image = feed_card(deal)
    if not image:
        return False, "image is not a generated branded card (images/generated/deal-<id>-square/feed.jpg)"
    url = hosted.get(image) or ""
    if not (url.startswith("https://res.cloudinary.com/") and url.lower().endswith((".jpg", ".jpeg"))):
        return False, "4:5 feed card is not hosted on Cloudinary as a JPEG"
    if post_key(deal) in posted_keys:
        return False, "already queued or posted at this price"
    if now is not None and deal.get("approved_by", "").startswith("automatic"):
        age = price_check_age_hours(deal, now)
        if age is None or age > MAX_POST_AGE_HOURS:
            return False, (f"price checked {age:.1f} h ago" if age is not None else "no price-check time") + \
                f"; Instagram needs a check within {MAX_POST_AGE_HOURS} h (re-verify first)"
    return True, "eligible"


# Playbook v1.0 hashtag sets (5-8 per post, matched to the product, never borrowed from another category).
HASHTAGS = {
    "Tech": "#TechDeals #ElectronicsDeals #GadgetDeals #TechSavings #BestDealHunter #ShoppingDeals #DealFinder",
    "Home": "#HomeDeals #HomeSavings #HomeEssentials #HomeFinds #BudgetHome #BestDealHunter #DiscountDeals",
    "Kitchen": "#KitchenDeals #KitchenFinds #CookingEssentials #KitchenSavings #HomeCooking #BestDealHunter "
               "#ShoppingDeals",
    "Fashion": "#FashionDeals #StyleOnABudget #ClothingDeals #FashionFinds #WardrobeDeals #BestDealHunter #DealAlerts",
    "Sports": "#SportsDeals #FitnessDeals #OutdoorDeals #ActiveLifestyle #SportsGear #BestDealHunter #DiscountFinds",
    "Beauty": "#BeautyDeals #BeautyFinds #SkincareDeals #MakeupDeals #BeautySavings #BestDealHunter #ShoppingFinds",
    "Other": "#DealAlerts #ShoppingDeals #DealFinder #DiscountDeals #BestDealHunter",
}
KITCHEN = re.compile(r"kitchen|cook|bak(e|ing)|blender|mixer|air fryer|coffee|espresso|knife|knives|pan\b|pans\b|"
                     r"pot\b|pots\b|skillet|toaster|kettle|cutlery|utensil|dinnerware|oven mitt|mitt\b", re.I)
# Varied openings so a day of posts doesn't read identically; the verified numbers never change.
OPENERS = ("🔥 {percent}% OFF — {title}", "🔥 {percent}% OFF: {title}", "🔥 Deal alert: {percent}% OFF — {title}")


def hashtags(deal):
    category = deal.get("category") or "Other"
    if category == "Electronics":
        category = "Tech"
    elif category == "Home" and KITCHEN.search(str(deal.get("title", ""))):
        category = "Kitchen"
    return HASHTAGS.get(category, HASHTAGS["Other"])


def checked_at(deal):
    """'Oct 13, 2026, 10:30 PM PT' from the price-check time (or the verification day)."""
    try:
        return as_of_text(deal)
    except CardError:
        return None


def caption(deal):
    """Playbook v1.0 primary caption; '#ad' and the commission statement only for affiliate links."""
    facts = card_facts(deal)
    opener = OPENERS[int(post_key(deal), 16) % len(OPENERS)]
    lines = [
        opener.format(percent=facts["percent"], title=facts["title"]),
        f"🏬 Store: {facts['store']}",
        f"💰 Now: {facts['price']}",
        f"🏷️ {facts['reference_label']}: {facts['reference']}",
        f"💵 Savings: {facts['savings']}",
        "🛒 Find the deal at bestdealhunters.com",
        "🔗 Link in bio",
        f"⏰ Price as of {checked_at(deal) or 'the verification time'}",
        f"⚠️ Prices may change. Availability may vary. The price on {facts['store']} at checkout applies.",
        "",
        hashtags(deal),
    ]
    if is_affiliate_link(deal.get("url")):
        lines += ["", *disclosure(deal)[1]]
    text = "\n".join(lines)
    if len(text) > CAPTION_LIMIT or text.count("#") > HASHTAG_LIMIT:
        raise CardError("caption exceeds Instagram limits")
    return text


def plan(deals, mapping, posts, today, now=None):
    hosted = hosted_urls(mapping)
    posted_keys = {p.get("key") for p in posts if isinstance(p, dict)}
    ready, skipped = [], []
    if now is not None:
        from datetime import datetime, timedelta
        def stamp(p):
            try:
                return datetime.fromisoformat(str(p.get("reserved_at", "")).replace("Z", "+00:00"))
            except ValueError:
                return None
        recent = [p for p in posts if isinstance(p, dict) and stamp(p) and now - stamp(p) < timedelta(hours=24)]
        if len(recent) >= MAX_POSTS_PER_DAY:
            return [], [(d.get("title", "Untitled"), f"daily limit of {MAX_POSTS_PER_DAY} posts reached")
                        for d in deals if isinstance(d, dict)]
    for deal in deals:
        if not isinstance(deal, dict):
            continue
        ok, reason = eligibility(deal, hosted, posted_keys, today, now)
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


BUFFER_API = "https://api.buffer.com"
# Shapes confirmed by read-only introspection of api.buffer.com (2026-10-10, PR #15):
# CreatePostInput requires mode: ShareMode! and schedulingType: SchedulingType!; createPost
# returns the PostActionPayload union; every error member implements MutationError { message };
# PostStatus is draft | error | needs_approval | scheduled | sending | sent.
# Instagram channels also require metadata.instagram {type: PostType!, shouldShareToFeed:
# Boolean!} (Buffer rejected a draft without it: "Instagram posts require a type").
INSTAGRAM_METADATA = {"instagram": {"type": "post", "shouldShareToFeed": True}}
RESULT_FIELDS = """
    __typename
    ... on PostActionSuccess { post { id status channelId schedulingType shareMode sharedNow sentAt dueAt } }
    ... on MutationError { message }"""
CREATE_POST = """
mutation CreatePost($input: CreatePostInput!) {
  createPost(input: $input) {""" + RESULT_FIELDS + """
  }
}"""
POST_STATUS = """
query PostStatus($input: PostInput!) { post(input: $input) { id status sentAt error { message } } }"""


class BufferError(Exception):
    pass


class DraftCheckFailed(BufferError):
    """Buffer's answer did not prove a harmless draft was created: stop, never continue."""


def draft_input(channel_id, text, image_url):
    """createPost input for a test draft. mode and schedulingType are required by Buffer's
    schema, so they are set to the least risky values: schedulingType "notification" means
    Buffer's workers never auto-publish it (a person would have to post by hand), on top of
    saveToDraft, which keeps it out of the queue entirely."""
    return {"text": text, "channelId": channel_id, "saveToDraft": True, "schedulingType": "notification",
            "mode": "addToQueue", "assets": [{"image": {"url": image_url}}], "metadata": INSTAGRAM_METADATA}


def verify_draft(result, channel_id):
    """Accept only an unambiguous draft; anything else raises DraftCheckFailed."""
    if not isinstance(result, dict):
        raise DraftCheckFailed("Buffer returned no createPost result")
    kind = result.get("__typename")
    if kind != "PostActionSuccess":
        raise DraftCheckFailed(f"Buffer returned {kind or 'an untyped result'}: {result.get('message', 'no message')}")
    post = result.get("post") if isinstance(result.get("post"), dict) else {}
    expected = {"status": "draft", "channelId": channel_id, "schedulingType": "notification",
                "sharedNow": False, "sentAt": None}
    problems = [f"{field}={post.get(field)!r} (expected {want!r})"
                for field, want in expected.items() if post.get(field, "<missing>") != want]
    if not post.get("id"):
        problems.insert(0, "no post id")
    if problems:
        where = f"post {post['id']}" if post.get("id") else "the post"
        raise DraftCheckFailed(f"response is not a confirmed draft ({'; '.join(problems)}). "
                               f"Check Buffer and delete {where} by hand.")
    return post


class BufferClient:
    """Buffer GraphQL client (api.buffer.com, personal API key as Bearer token).

    Schema confirmed by introspection (see RESULT_FIELDS). Errors arrive as HTTP 200 inside
    the PostActionPayload union, so __typename is always checked: only PostActionSuccess
    counts as success.
    """

    def __init__(self, api_key, transport=None):
        if not api_key:
            raise BufferError("BUFFER_API_KEY is not configured")
        self.api_key, self.transport = api_key, transport or self._http

    def _http(self, payload):
        from urllib import request
        req = request.Request(BUFFER_API, data=json.dumps(payload).encode(), method="POST", headers={
            "Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
            "User-Agent": "BestDealHunter/1.0"})
        with request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())

    def _call(self, query, variables):
        data = self.transport({"query": query, "variables": variables})
        if data.get("errors"):
            raise BufferError("; ".join(str(e.get("message", e)) for e in data["errors"]))
        return data.get("data") or {}

    def create_post(self, channel_id, text, image_url):
        """Live post, shared now (only reached from publish(), behind every live gate)."""
        # shareNow: published at once, while the price check is minutes old (a queue slot could be hours later)
        payload = {"text": text, "channelId": channel_id, "schedulingType": "automatic", "mode": "shareNow",
                   "assets": [{"image": {"url": image_url}}], "metadata": INSTAGRAM_METADATA}
        result = self._call(CREATE_POST, {"input": payload}).get("createPost") or {}
        if result.get("__typename") != "PostActionSuccess":
            raise BufferError(f"Buffer rejected the post ({result.get('__typename')}): {result.get('message', 'no message')}")
        post = result.get("post") or {}
        if not post.get("id"):
            raise BufferError("Buffer response had no post id")
        return post

    def create_draft(self, channel_id, text, image_url):
        """Test draft only: fixed draft input, and the response must prove it is a draft."""
        result = self._call(CREATE_POST, {"input": draft_input(channel_id, text, image_url)}).get("createPost")
        return verify_draft(result, channel_id)

    def post_status(self, post_id):
        return (self._call(POST_STATUS, {"input": {"id": post_id}}).get("post") or {}).get("status")


def publish(ready, posts, client, channel_id, now, log=print, lock=lambda key: True):
    """Send planned posts with reserve-before-send duplicate protection.

    A ledger row is written to `posts` BEFORE calling Buffer, because createPost has no
    idempotency key: if the run dies mid-call, the row (status "reserved") still blocks
    any retry until a person checks Buffer and clears it. Every existing row blocks its key.
    lock(key) must then succeed before Buffer is called: in live runs it creates the git tag
    deal-post/<key> on GitHub without force, so even if posts.json is never pushed (crash, push
    race) a later run cannot post the same product at the same price again.
    """
    taken = {p.get("key") for p in posts if isinstance(p, dict)}
    results = []
    for post in ready:
        if post["key"] in taken:
            log(f"Duplicate blocked: {post['title']} ({post['key']})")
            continue
        row = {"key": post["key"], "deal_url": post["deal_url"], "title": post["title"], "price": post["price"],
               "image_url": post["image_url"], "status": "reserved", "reserved_at": now}
        posts.append(row)
        taken.add(post["key"])
        if not lock(post["key"]):
            row.update(status="locked", error="durable lock already taken: posted or attempted before")
            log(f"Duplicate blocked by lock tag {LOCK_PREFIX}{post['key']}: {post['title']}")
            results.append(row)
            continue
        try:
            created = client.create_post(channel_id, post["text"], post["image_url"])
        except Exception as exc:  # keep the reservation: a timeout may still have created the post
            row.update(status="error", error=str(exc)[:300])
            log(f"Buffer error for {post['title']}: {exc}. Reservation kept; check Buffer before retrying.")
            results.append(row)
            continue
        row.update(status="queued", buffer_post_id=created["id"])
        log(f"Sent to Buffer (share now): {post['title']} (id {created['id']})")
        results.append(row)
    return results


def git_tag_lock(key, run=None):
    """Create refs/tags/deal-post/<key> on GitHub, never forced. False if it exists or can't be made."""
    import subprocess
    run = run or (lambda *a: subprocess.run(["git", *a], capture_output=True, text=True, cwd=ROOT))
    name = f"{LOCK_PREFIX}{key}"
    if run("ls-remote", "--tags", "origin", f"refs/tags/{name}").stdout.strip():
        return False
    if run("tag", name, "HEAD").returncode != 0 and run("rev-parse", "-q", "--verify", f"refs/tags/{name}").returncode != 0:
        return False
    return run("push", "origin", f"refs/tags/{name}:refs/tags/{name}").returncode == 0


def refresh_statuses(posts, client):
    """Record `sent` only when Buffer itself reports it."""
    changed = 0
    for row in posts:
        if row.get("status") == "queued" and row.get("buffer_post_id"):
            status = client.post_status(row["buffer_post_id"])
            if status and status != row.get("buffer_status"):
                row["buffer_status"] = status
                if str(status).lower() == "sent":
                    row["status"] = "sent"
                changed += 1
    return changed


def draft_check(card, deals, mapping, drafts, client, channel_id, now, today, log=print):
    """Create ONE verified Buffer draft for the deal with this card id (test approval only).

    Separate from live posting: the test approval exists only in memory for this deal, the
    record goes to `drafts` (buffer-draft-checks.json), and publish()/create_post() are never
    used. A reservation is written before the call; any failure or ambiguous response marks it
    "error" and raises, so the run stops and the same deal is not drafted again automatically.
    """
    from deal_graphics import card_id
    if not CARD_ID.fullmatch(str(card)):
        raise DraftCheckFailed(f"invalid card id {card!r}")
    if not any(isinstance(d, dict) and card_id(d) == card for d in deals):
        raise DraftCheckFailed(f"no deal with card id {card}")
    test_approved = apply_dry_run_approval(deals, {card})
    candidates = [d for d in test_approved if isinstance(d, dict) and card_id(d) == card]
    ready, skipped = plan(candidates, mapping, drafts, today)
    if not ready:
        raise DraftCheckFailed(f"deal not draftable: {skipped[0][1] if skipped else 'not planned'}")
    post = ready[0]
    row = {"card_id": card, "key": post["key"], "deal_url": post["deal_url"], "title": post["title"],
           "image_url": post["image_url"], "channel_id": channel_id, "status": "reserved", "reserved_at": now}
    drafts.append(row)
    try:
        created = client.create_draft(channel_id, post["text"], post["image_url"])
    except Exception as exc:
        row.update(status="error", error=str(exc)[:300])
        log(f"Draft check FAILED for {post['title']}: {exc}")
        raise DraftCheckFailed(str(exc)) from exc
    row.update(status="draft", buffer_post_id=created["id"], buffer_status=created["status"])
    log(f"Verified Buffer draft for {post['title']} (id {created['id']}, status draft, not scheduled). "
        "Delete it in Buffer after checking.")
    return row


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


def apply_dry_run_approval(deals, card_ids):
    """Dry-run only: treat the named deals (by card id) as approved without changing data."""
    from deal_graphics import card_id
    out = []
    for deal in deals:
        if isinstance(deal, dict) and card_id(deal) in card_ids:
            deal = dict(deal, publication_approved=True)
        out.append(deal)
    return out


def run_draft_check(card, env, log=print):
    """--draft-check entry point. Never reads INSTAGRAM_PUBLISH_ENABLED, never touches posts.json."""
    channel = env.get("BUFFER_CHANNEL_ID", "")
    if not env.get("BUFFER_API_KEY") or not channel:
        log("Refused: BUFFER_API_KEY secret and BUFFER_CHANNEL_ID variable must both be configured.")
        return 2
    from datetime import datetime, timezone
    deals = load(ROOT / "deals.json", [])
    mapping = load(ROOT / "cloudinary-images.json", [])
    drafts = load(DRAFT_CHECKS, [])
    before = len(drafts)
    try:
        draft_check(card, deals, mapping, drafts, BufferClient(env["BUFFER_API_KEY"]), channel,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"), date.today(), log=log)
        return 0
    except DraftCheckFailed as exc:
        log(f"Draft check stopped: {exc}")
        return 1
    finally:
        if len(drafts) != before:
            DRAFT_CHECKS.write_text(json.dumps(drafts, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Plan (and, behind every gate, publish) Instagram posts via Buffer")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="queue real posts (refused unless every gate passes)")
    mode.add_argument("--draft-check", metavar="CARD_ID",
                      help="create ONE verified Buffer DRAFT for this deal (never queued or published)")
    parser.add_argument("--approve-for-dry-run", action="append", default=[], metavar="CARD_ID",
                        help="dry run only: treat this deal as approved without editing deals.json")
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 2
    if args.approve_for_dry_run and (args.live or args.draft_check):
        print("--approve-for-dry-run is only allowed in dry runs.")
        return 2
    if args.draft_check:  # separate path: returns before any live-posting code below
        return run_draft_check(args.draft_check, os.environ)

    deals = load(ROOT / "deals.json", [])
    if args.approve_for_dry_run:
        deals = apply_dry_run_approval(deals, set(args.approve_for_dry_run))
    mapping = load(ROOT / "cloudinary-images.json", [])
    posts = load(POSTS, [])
    from datetime import datetime, timezone
    ready, skipped = plan(deals, mapping, posts, date.today(), now=datetime.now(timezone.utc))
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
    channel = os.environ.get("BUFFER_CHANNEL_ID", "")
    if not os.environ.get("BUFFER_API_KEY") or not channel:
        print("Refused: BUFFER_API_KEY secret and BUFFER_CHANNEL_ID variable must both be configured.")
        return 2
    client = BufferClient(os.environ["BUFFER_API_KEY"])
    from datetime import datetime, timezone
    publish(ready, posts, client, channel, datetime.now(timezone.utc).isoformat(timespec="seconds"), lock=git_tag_lock)
    refresh_statuses(posts, client)
    POSTS.write_text(json.dumps(posts, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

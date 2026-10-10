#!/usr/bin/env python3
"""Run ONE deal through the whole BestDealHunter pipeline and prove each step.

  deal → validate price/discount → branded card → Cloudinary → website → Buffer post (dry run)

Nothing is published. The Buffer step builds the exact post, then exercises the real
publish() code against an in-memory fake Buffer and an in-memory copy of the ledger to
prove duplicate protection; the repository's posts.json is never written.

  python scripts/e2e_one_deal.py                       # first active non-Amazon deal with a card
  python scripts/e2e_one_deal.py --deal 9ace7211bab6   # a specific deal (card id)
  python scripts/e2e_one_deal.py --verify-urls         # also fetch the Cloudinary URLs (CI)
"""
import argparse
import copy
import hashlib
import io
import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from urllib import error, request

from cloudinary_upload import load_mapping
from deal_graphics import card_facts, card_id, render_card
from publish_buffer import BufferClient, apply_dry_run_approval, caption, feed_card, plan, publish
from validate_deals import RULES, deal_status, validate

ROOT = Path(__file__).resolve().parents[1]


class StepFailed(Exception):
    pass


def jpeg(card):
    buf = io.BytesIO()
    card.save(buf, "JPEG", quality=92, optimize=True, progressive=True)
    return buf.getvalue()


def pick_deal(deals, wanted, today):
    for deal in deals:
        if not isinstance(deal, dict):
            continue
        if wanted and card_id(deal) != wanted:
            continue
        if not wanted and (deal.get("store") == "Amazon" or deal_status(deal, today) != "active"
                           or not str(deal.get("image", "")).startswith("images/generated/deal-")):
            continue
        return deal
    raise StepFailed(f"no deal found for {wanted!r}" if wanted else "no active non-Amazon deal with a branded card")


def step_validate(deal, today):
    validate([deal], today)
    price, original = float(deal["price"]), float(deal["original_price"])
    percent = int((original - price) * 100 // original)
    if percent < RULES["min_discount_percent"]:
        raise StepFailed(f"only {percent}% off")
    return f"${price:,.2f} vs ${original:,.2f} = {percent}% off (≥{RULES['min_discount_percent']}%), verified {deal['verified_date']}, active"


def step_card(deal):
    facts = card_facts(deal)
    cid = card_id(deal)
    for kind in ("square", "feed"):
        path = ROOT / f"images/generated/deal-{cid}-{kind}.jpg"
        if not path.is_file():
            raise StepFailed(f"missing {kind} card {path.name}")
        if path.read_bytes() != jpeg(render_card(deal, kind)):
            raise StepFailed(f"{kind} card is out of date with the deal data")
    if deal.get("image") != f"images/generated/deal-{cid}-square.jpg":
        raise StepFailed("site image is not the deal's square card")
    return f"square + 4:5 cards match the deal byte-for-byte ({facts['percent']}% OFF, {facts['price']}, {facts['reference_label']} {facts['reference']})"


def head_ok(url):
    req = request.Request(url, method="HEAD", headers={"User-Agent": "BestDealHunter-e2e/1.0"})
    try:
        with request.urlopen(req, timeout=20) as resp:
            return resp.status, resp.headers.get("Content-Type", "")
    except error.HTTPError as exc:
        return exc.code, ""


def step_cloudinary(deal, mapping, verify_urls):
    hosted = {m["image"]: m for m in mapping if isinstance(m, dict)}
    urls = {}
    for image in (deal["image"], feed_card(deal)):
        entry = hosted.get(image)
        if not entry:
            raise StepFailed(f"{image} not hosted (run the Cloudinary upload)")
        if entry.get("sha256") != hashlib.sha256((ROOT / image).read_bytes()).hexdigest():
            raise StepFailed(f"hosted copy of {image} is stale (sha256 mismatch)")
        url = entry["cloudinary_url"]
        if verify_urls:
            status, ctype = head_ok(url)
            if status != 200 or not ctype.startswith("image/jpeg"):
                raise StepFailed(f"{url} not publicly served as JPEG (HTTP {status}, {ctype or 'no type'})")
        urls[image] = url
    note = "publicly verified HTTP 200 image/jpeg" if verify_urls else "mapping + sha256 verified (use --verify-urls to fetch)"
    return urls, f"square → {urls[deal['image']]} · 4:5 → {urls[feed_card(deal)]} · {note}"


def step_website(deal, mapping, today):
    """Mirror index.html's own rules (read from the file) for where and how the deal shows."""
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    stores = json.loads(re.search(r"const allowedStores=(\[[^\]]*\])", html).group(1))
    max_age = int(re.search(r"const maxVerificationAgeDays=(\d+);", html).group(1))
    prefix = re.search(r'x\.cloudinary_url\.startsWith\("([^"]+)"\)', html).group(1)
    if max_age != RULES["max_verification_age_days"] or deal["store"] not in stores:
        raise StepFailed("site rules disagree with deal_rules.json or the store is not shown")
    hosted = {m["image"]: m["cloudinary_url"] for m in mapping if str(m.get("cloudinary_url", "")).startswith(prefix)}
    src = hosted.get(deal["image"])
    if not src:
        raise StepFailed("site would fall back to the repo-hosted image, not Cloudinary")
    if deal_status(deal, today) != "active":
        raise StepFailed("deal would appear under Past Deals, not Explore Deals")
    pct = int((deal["original_price"] - deal["price"]) * 100 // deal["original_price"])
    return f"shown in Explore Deals (50%+ tier) with a {pct}% OFF badge; card served from Cloudinary ({src})"


class _FakeBuffer:
    """In-memory stand-in for api.buffer.com used only to exercise publish(); nothing leaves the process."""

    def __init__(self):
        self.creates = 0

    def __call__(self, payload):
        if "createPost" in payload["query"]:
            self.creates += 1
            return {"data": {"createPost": {"__typename": "PostActionSuccess",
                                            "post": {"id": f"simulated-{self.creates}", "status": "scheduled"}}}}
        return {"data": {"post": {"status": "scheduled"}}}


def step_buffer(deal, deals, mapping, posts, today):
    cid = card_id(deal)
    approved = apply_dry_run_approval(deals, {cid})
    ready, skipped = plan(approved, mapping, posts, today)
    mine = [p for p in ready if p["deal_url"] == deal["url"]]
    if not mine:
        reason = next((r for t, r in skipped if t == deal["title"]), "not planned")
        raise StepFailed(f"not ready to post: {reason}")
    post = mine[0]
    hosted = {m["image"]: m["cloudinary_url"] for m in mapping}
    if post["image_url"] != hosted[feed_card(deal)]:
        raise StepFailed("post would not use the 4:5 Cloudinary card")
    for must in ("#ad", "may earn a commission", "Prices and availability may change", card_facts(deal)["price"]):
        if must not in post["text"]:
            raise StepFailed(f"caption missing {must!r}")
    # Duplicate protection, using the real publish() against a fake Buffer and a COPY of the ledger.
    fake, ledger = _FakeBuffer(), copy.deepcopy(posts)
    client = BufferClient("simulated-key", transport=fake)
    publish([post], ledger, client, "simulated-channel", "simulated", log=lambda *_: None)
    again, why = plan(approved, mapping, ledger, today)
    publish([post], ledger, client, "simulated-channel", "simulated-replay", log=lambda *_: None)
    if any(p["deal_url"] == deal["url"] for p in again) or fake.creates != 1:
        raise StepFailed("duplicate protection failed: the deal could be queued twice")
    blocked = next(r for t, r in why if t == deal["title"])
    switch = "ON" if os.environ.get("INSTAGRAM_PUBLISH_ENABLED") == "true" else "OFF"
    return post, (f"ready to post (dry run, kill switch {switch}); simulated queue → re-plan blocked "
                  f"('{blocked}'), stale replay blocked before any API call (1 create total)")


def run(wanted=None, verify_urls=False, today=None):
    today = today or date.today()
    deals = json.loads((ROOT / "deals.json").read_text(encoding="utf-8"))
    mapping = load_mapping()
    posts = json.loads((ROOT / "posts.json").read_text(encoding="utf-8"))
    rows, post = [], None
    try:
        deal = pick_deal(deals, wanted, today)
    except StepFailed as exc:
        return [("Pick deal", False, str(exc))], None, None
    steps = [
        ("1. Validate price/discount", lambda: step_validate(deal, today)),
        ("2. Branded card", lambda: step_card(deal)),
        ("3. Cloudinary", lambda: step_cloudinary(deal, mapping, verify_urls)[1]),
        ("4. Website", lambda: step_website(deal, mapping, today)),
        ("5. Buffer/Instagram (dry run)", lambda: step_buffer(deal, deals, mapping, posts, today)),
    ]
    for name, fn in steps:
        try:
            result = fn()
            if isinstance(result, tuple):
                post, result = result
            rows.append((name, True, result))
        except Exception as exc:  # report every step, never stop at the first failure
            rows.append((name, False, f"{type(exc).__name__}: {exc}"))
    return rows, deal, post


def report(rows, deal, post):
    lines = ["## One-deal end-to-end run", ""]
    if deal:
        lines += [f"**Deal:** {deal['store']}: {deal['title']} (card id `{card_id(deal)}`)", ""]
    lines += ["| Step | Result | Detail |", "|---|---|---|"]
    lines += [f"| {n} | {'✅ pass' if ok else '❌ fail'} | {d} |" for n, ok, d in rows]
    if post:
        lines += ["", "### Post that would be queued (not sent)", f"- Image: {post['image_url']}",
                  f"- Dedupe key: `{post['key']}`", "", "```", post["text"], "```"]
    lines += ["", "Nothing was published; posts.json was not modified."]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--deal", help="card id of the deal (default: first eligible test deal)")
    parser.add_argument("--verify-urls", action="store_true", help="fetch the Cloudinary URLs (needs network)")
    args = parser.parse_args(argv)
    rows, deal, post = run(args.deal, args.verify_urls)
    text = report(rows, deal, post)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    return 0 if all(ok for _, ok, _ in rows) else 1


if __name__ == "__main__":
    sys.exit(main())

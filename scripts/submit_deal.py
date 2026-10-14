#!/usr/bin/env python3
"""Publish a deal a person spotted, from the "Submit a deal" GitHub issue form.

Some stores (Target, Walmart, Home Depot, Dick's, Macy's) don't allow bots to read their pages, so a
person looks at the deal and submits it on the form with a screenshot (owner + Alpha plan, 2026-10-14).
This script turns that issue into a deals.json entry, then the workflow makes the branded card, hosts it
on Cloudinary, lists it on the website and posts it to Instagram through Buffer.

Only accepted when every check passes:
- the issue was opened by the repository owner or a collaborator (checked by the workflow, and here);
- the store is a listed store other than Amazon (Associates rules: Amazon prices only from Amazon's API);
- the link is the store's own https product page, with tracking stripped; affiliate redirects are refused;
- sale < was price and the discount, computed here and rounded down, is >= 50%; 80%+ is held for a recheck;
- the person confirmed it is in stock online, sold by the store itself, and was seen within the last hour;
- a screenshot is attached; it is saved under evidence/ as the price evidence;
- the product is not already listed, and strict validation passes.

  python scripts/submit_deal.py --event "$GITHUB_EVENT_PATH" [--apply] [--summary result.md]
"""
import argparse
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from add_verified_deals import Rejected, check
from links import direct_url
from publish_buffer import product_id

ROOT = Path(__file__).resolve().parents[1]
FEED = ROOT / "deals.json"
EVIDENCE = ROOT / "evidence"
TRUSTED = {"OWNER", "MEMBER", "COLLABORATOR"}
HOLD_AT_PERCENT = 80
SCREENSHOT = re.compile(r"https://(?:github\.com/user-attachments/assets/[\w-]+|"
                        r"(?:private-)?user-images\.githubusercontent\.com/[\w./%-]+)")
IMAGE_TYPES = {b"\x89PNG": ".png", b"\xff\xd8\xff": ".jpg", b"RIFF": ".webp"}
MAX_SCREENSHOT_BYTES = 10 * 1024 * 1024
LABELS = {"Was": "Was", "Reg.": "Reg.", "Orig.": "Orig.", "List price": "List price",
          "Comparable value": "Comparable value"}
REQUIRED_CHECKS = ("in stock", "sold by", "last hour")


def fields(body):
    """Issue form body ('### Label' then the answer) -> {label: answer}."""
    out, label = {}, None
    for line in str(body or "").splitlines():
        if line.startswith("### "):
            label = line[4:].strip()
            out[label] = []
        elif label is not None:
            out[label].append(line)
    return {k: "\n".join(v).strip() for k, v in out.items()}


def money(text, name):
    raw = re.sub(r"[$,\s]", "", str(text or ""))
    if not re.fullmatch(r"\d+(\.\d{1,2})?", raw):
        raise Rejected(f"{name} must be a price like 34.99 (got {text!r})")
    return float(raw)


def record(issue, now):
    """The add_verified_deals record for this submission, or Rejected with the reason."""
    if issue.get("author_association") not in TRUSTED:
        raise Rejected("only the repository owner or a collaborator can submit deals")
    f = fields(issue.get("body"))
    store = f.get("Store", "")
    if store == "Amazon":
        raise Rejected("Amazon deals can't be posted: Amazon's Associates rules allow Amazon prices only from "
                       "Amazon's own API")
    ticked = [line.lower() for line in f.get("Checks", "").splitlines() if line.lower().startswith("- [x]")]
    missing = [c for c in REQUIRED_CHECKS if not any(c in line for line in ticked)]
    if missing:
        raise Rejected("tick all three boxes (in stock online, sold by the store itself, seen in the last hour)")
    url = direct_url(f.get("Product link", "").strip())
    if not url:
        raise Rejected("use the store's own product link, not an affiliate or redirect link")
    price, original = money(f.get("Sale price"), "Sale price"), money(f.get("Was price"), "Was price")
    if original > 0 and price < original and int((original - price) * 100 // original) >= HOLD_AT_PERCENT:
        raise Rejected(f"{HOLD_AT_PERCENT}% off or more is held: double-check the was-price is the store's own "
                       "crossed-out price, then post it in a comment for the owner")
    shot = SCREENSHOT.search(f.get("Screenshot", ""))
    if not shot:
        raise Rejected("attach a screenshot of the store page showing the sale price and the was price")
    created = datetime.fromisoformat(str(issue.get("created_at", "")).replace("Z", "+00:00"))
    if created > now:
        raise Rejected("issue time is in the future")
    return {
        "store": store, "title": f.get("Product name", "").strip(), "url": url,
        "category": f.get("Category", "Other").strip(), "seller": store,
        "price": price, "original_price": original,
        "reference_label": LABELS.get(f.get("Was price label", "Was").strip(), "Was"),
        "availability": "In stock online (seen on the store's page)",
        "evidence": {"method": "human_browser_check", "checked_at": created.isoformat(),
                     "checked_by": f"{issue.get('user', {}).get('login', '?')} via {issue.get('html_url', 'issue')}",
                     "source": None},
    }, shot.group(0)


def fetch_screenshot(url, number, opener=urllib.request.urlopen):
    """Download the attached screenshot to evidence/deal-issue-<n>.<ext>; must be a PNG, JPEG or WebP."""
    with opener(urllib.request.Request(url, headers={"User-Agent": "BestDealHunter deal form"}), timeout=30) as resp:
        data = resp.read(MAX_SCREENSHOT_BYTES + 1)
    if len(data) > MAX_SCREENSHOT_BYTES:
        raise Rejected("screenshot is larger than 10 MB")
    ext = next((e for magic, e in IMAGE_TYPES.items() if data.startswith(magic)), None)
    if not ext:
        raise Rejected("screenshot must be a PNG, JPEG or WebP image")
    path = EVIDENCE / f"deal-issue-{number}{ext}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def submit(issue, deals, now, fetch=fetch_screenshot):
    """Return the new deals.json entry for the issue, or raise Rejected."""
    rec, shot = record(issue, now)
    products = {product_id(d) for d in deals if isinstance(d, dict)}
    if product_id(rec) in products:
        raise Rejected("this product is already listed")
    path = fetch(shot, issue.get("number"))
    rec["evidence"]["source"] = str(path.relative_to(ROOT))
    try:
        entry = check(rec, now.date(), {d.get("url") for d in deals if isinstance(d, dict)})
    except (Rejected, ValueError):
        path.unlink(missing_ok=True)
        raise
    entry.update(publication_approved=True, approved_by=f"owner deal form: {issue.get('html_url')}",
                 price_checked_at=rec["evidence"]["checked_at"])
    return entry


def main(argv=None, now=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--event", required=True, help="GitHub issues event JSON")
    parser.add_argument("--apply", action="store_true", help="write deals.json")
    parser.add_argument("--summary", help="write a Markdown result for the issue comment")
    args = parser.parse_args(argv)
    issue = json.loads(Path(args.event).read_text(encoding="utf-8"))["issue"]
    deals = json.loads(FEED.read_text(encoding="utf-8"))
    try:
        entry = submit(issue, deals, now or datetime.now(timezone.utc))
    except (Rejected, ValueError, OSError) as exc:
        message = f"❌ Not published: {exc}"
        ok = False
    else:
        pct = int((entry["original_price"] - entry["price"]) * 100 // entry["original_price"])
        message = (f"✅ Accepted: {entry['store']} · {entry['title']} · ${entry['price']:.2f} "
                   f"({entry['reference_price_label']} ${entry['original_price']:.2f}) · {pct}% off. "
                   "Card, website and Instagram follow in this run.")
        ok = True
        if args.apply:
            FEED.write_text(json.dumps(deals + [entry], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(message)
    if args.summary:
        Path(args.summary).write_text(message + "\n", encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

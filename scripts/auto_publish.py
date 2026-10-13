#!/usr/bin/env python3
"""Automatic publishing of deals verified on the retailer's own product page.

Policy (owner + Alpha, 2026-10-13): no checkout and no manual approval. A deal is published
when verify_listings.py's verdict is "verified", i.e. the retailer's product page itself shows
the current price, a higher reference price giving >= 50% off (floored), and that it can be
bought now. Blocked, failed, unconfirmed, mismatched, under-50% or unavailable pages are never
published, and a page claiming 80%+ off is held (likely an inflated reference price).

Each verified page becomes a deals.json entry through add_verified_deals.check() (evidence
method "retailer_page_check", the GitHub Actions run URL as evidence source, strict validation,
no duplicates) with publication_approved true, so the card generator, Cloudinary upload and the
Buffer publisher pick it up in the same workflow run. Append-only; at most --max new deals a run.

  python scripts/auto_publish.py verify-results.json amazon-candidates.json ... --run-url URL [--apply]
"""
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

from add_verified_deals import Rejected, check
from publish_buffer import product_id

ROOT = Path(__file__).resolve().parents[1]
APPROVED_BY = "automatic: retailer product page check (owner policy 2026-10-13)"
# A page claiming 80%+ off is held for a person instead of auto-published: in run 38452512276 these
# were inflated list prices (e.g. a smoke detector "List Price $269.99", sold at $35.98).
HOLD_AT_PERCENT = 80
CATEGORY_HINTS = (("Electronics", r"electronic|computer|cell phone|camera|video game|headphone|tv\b|television"),
                  ("Fashion", r"clothing|shoes|jewelry|fashion|watches|luggage"),
                  ("Beauty", r"beauty|personal care|health"),
                  ("Sports", r"sports|outdoors|fitness"),
                  ("Home", r"home|kitchen|tools|garden|patio|furniture|appliance|bedding|bath"))


def clean_title(title):
    """'Amazon.com: KitchenAid Mitt, Grey : Home & Kitchen' -> 'KitchenAid Mitt, Grey'."""
    t = re.sub(r"^\s*Amazon\.com\s*:\s*", "", str(title or ""))
    t = re.sub(r"\s+:\s+[^:]+$", "", t)                       # trailing ' : Department'
    t = re.sub(r"\s+at Amazon .*$", "", t)                    # '... at Amazon Men's Clothing store'
    t = re.sub(r"^\s*Amazon\.com\s*[-:]\s*", "", t)            # 'Amazon.com - Cordless Vacuum ...'
    t = re.split(r"\s+[|\[(]", t)[0]                          # drop '| feature list', '[3 Stand Ways]', '(...)'
    t = re.sub(r"\s+", " ", t).strip(" ,-")
    if len(t) > 90:                                           # keep whole words, end on a clause
        t = t[:90].rsplit(",", 1)[0] if "," in t[:90] else t[:90].rsplit(" ", 1)[0]
    return t


def category(candidate, page_title):
    if candidate.get("category") and candidate["category"] != "Other":
        return candidate["category"]
    dept = (str(page_title or "").rsplit(" : ", 1)[-1] + " " + str(page_title or "")).lower()
    return next((name for name, rx in CATEGORY_HINTS if re.search(rx, dept)), "Other")


def record(row, candidate, run_url):
    """An add_verified_deals record built only from what the product page showed."""
    page = row.get("page") or {}
    # Discovered candidates only carry deals-page link text (sometimes just a brand): use the product page's title.
    title = clean_title(row.get("title")) if candidate.get("asin") or not candidate.get("title") else candidate["title"]
    return {
        "store": row.get("store") or candidate.get("store"),
        "title": title, "url": candidate.get("url") or row["url"],
        "category": category(candidate, row.get("title")),
        # the page check does not establish who sells it (Amazon listings are often third-party sellers)
        "seller": page.get("seller") or "unverified",
        "price": page.get("price"), "original_price": page.get("reference"),
        "reference_label": page.get("reference_label") or "Reference price",
        "availability": "In stock (retailer product page)",
        "evidence": {"method": "retailer_page_check", "checked_at": row["checked_at"], "source": run_url,
                     "checked_by": "verify_listings.py"},
    }


def select(results, candidates, deals, run_url, today, limit, log=print):
    """Return the new deals.json entries for every verified page (up to limit), logging every skip."""
    by_url = {c["url"]: c for c in candidates if isinstance(c, dict) and c.get("url")}
    urls = {d.get("url") for d in deals if isinstance(d, dict)}
    products = {product_id(d) for d in deals if isinstance(d, dict)}  # same ASIN under another link
    new = []
    for row in results:
        if row.get("verdict") != "verified":
            continue
        candidate = by_url.get(row["url"], {})
        try:
            entry = check(record(row, candidate, run_url), today, urls)
        except (Rejected, ValueError) as exc:
            log(f"SKIP {row['url']}: {exc}")
            continue
        pct = int((entry["original_price"] - entry["price"]) * 100 // entry["original_price"])
        if pct >= HOLD_AT_PERCENT:
            log(f"HOLD {entry['title']}: {pct}% off is {HOLD_AT_PERCENT}%+; questionable reference price, not auto-published")
            continue
        if product_id(entry) in products:
            log(f"SKIP {row['url']}: product already listed ({product_id(entry)})")
            continue
        entry.update(publication_approved=True, approved_by=APPROVED_BY, auto_published_at=row["checked_at"])
        if candidate.get("asin"):
            entry["asin"] = candidate["asin"]
        if len(new) >= limit:
            log(f"LIMIT {entry['title']}: run limit of {limit} new deals reached; next run")
            continue
        new.append(entry)
        urls.add(entry["url"])
        products.add(product_id(entry))
        log(f"PUBLISH {entry['store']}: {entry['title']} ${entry['price']:.2f} vs {entry['reference_price_label']} "
            f"${entry['original_price']:.2f} ({int((entry['original_price'] - entry['price']) * 100 // entry['original_price'])}% off) "
            f"{entry['url']}")
    return new


def main(argv=None, today=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("results")
    parser.add_argument("candidates", nargs="*")
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--max", type=int, default=5)
    parser.add_argument("--apply", action="store_true", help="append the verified deals to deals.json")
    args = parser.parse_args(argv)
    if not args.run_url.startswith("https://"):
        raise SystemExit("--run-url must be the https GitHub Actions run URL (it is the evidence source)")
    today = today or date.today()
    results = json.loads(Path(args.results).read_text(encoding="utf-8"))
    candidates = []
    for path in args.candidates:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        candidates += data if isinstance(data, list) else []
    feed = ROOT / "deals.json"
    deals = json.loads(feed.read_text(encoding="utf-8"))
    new = select(results, candidates, deals, args.run_url, today, args.max)
    print(f"{len(new)} page-verified deal(s) ready; verdicts: "
          + json.dumps({v: sum(r.get('verdict') == v for r in results) for v in sorted({r.get('verdict') for r in results})}))
    if new and args.apply:
        feed.write_text(json.dumps(deals + new, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Published {len(new)} deal(s) to deals.json (publication_approved true).")
    elif new:
        print("Dry run: deals.json unchanged (use --apply).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

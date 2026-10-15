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

  python scripts/auto_publish.py verify-results.json pending_deals.json ... --run-url URL [--apply]
"""
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

from add_verified_deals import Rejected, check
from links import direct_url
from publish_buffer import product_id
from validate_deals import RULES

ROOT = Path(__file__).resolve().parents[1]
APPROVED_BY = "automatic: retailer product page check (owner policy 2026-10-13)"
# A page claiming 80%+ off is held for a person instead of auto-published: in run 38452512276 these
# were inflated list prices (e.g. a smoke detector "List Price $269.99", sold at $35.98).
HOLD_AT_PERCENT = 80
# Amazon Associates Program Policies (read 2026-10-13): prices may be shown only from an Amazon-served link or
# Creators API / PA API data, and the License excludes "data mining, robots, or similar data gathering and
# extraction tools". So an Amazon product-page check is never a publishable source.
AMAZON_RULE = ("Amazon Associates Program Policies: Amazon prices may be shown only from an Amazon-served link "
               "or Creators API / PA API data, not from a product-page check")
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


def category(candidate, page_title, hint=""):
    if candidate.get("category") and candidate["category"] != "Other":
        return candidate["category"]
    dept = (str(hint or "") + " " + str(page_title or "").rsplit(" : ", 1)[-1] + " " + str(page_title or "")).lower()
    return next((name for name, rx in CATEGORY_HINTS if re.search(rx, dept)), "Other")


def record(row, candidate, run_url):
    """An add_verified_deals record built only from what the product page showed."""
    page = row.get("page") or {}
    # Discovered candidates only carry deals-page link text (sometimes just a brand): use the product page's title.
    discovered = candidate.get("asin") or candidate.get("discovered_from")
    title = clean_title(row.get("title")) if discovered or not candidate.get("title") else candidate["title"]
    method = row.get("evidence_method") or "retailer_page_check"
    return {
        "store": row.get("store") or candidate.get("store"),
        "title": title, "url": direct_url(candidate.get("url") or row["url"]),
        "category": category(candidate, row.get("title"), row.get("category_hint")),
        # the page check does not establish who sells it (Amazon listings are often third-party sellers)
        "seller": page.get("seller") or "unverified",
        "price": page.get("price"), "original_price": page.get("reference"),
        "reference_label": page.get("reference_label") or "Reference price",
        "availability": "In stock online (retailer API)" if method == "retailer_api" else "In stock (retailer product page)",
        "evidence": {"method": method, "checked_at": row["checked_at"], "source": run_url,
                     "checked_by": row.get("evidence_detail") or "verify_listings.py"},
    }


def page_check_allowed(url):
    """Only retailers whose terms we have reviewed and that permit automated page reading (deal_rules.json)."""
    from urllib.parse import urlparse
    host = (urlparse(str(url)).hostname or "").lower().removeprefix("www.")
    return any(host == h or host.endswith("." + h) for h in RULES.get("page_check_allowed_hosts", []))


def refresh(existing, entry, checked_at):
    """The retailer re-confirmed an auto-published deal at the same prices: record the new check time, so it
    stays current on the site and can still be posted to Instagram (posts need a check under 3 hours old)."""
    if not str(existing.get("approved_by", "")).startswith("automatic"):
        return False
    try:
        same = abs(float(existing["price"]) - float(entry["price"])) <= 0.005 and \
            abs(float(existing["original_price"]) - float(entry["original_price"])) <= 0.005
    except (KeyError, TypeError, ValueError):
        return False
    if not same:
        return False  # a changed price is not silently re-dated; the old one ages into "Price not re-verified"
    existing["auto_published_at"] = checked_at
    existing["verified_date"] = checked_at[:10]
    if str(existing.get("image", "")).startswith("images/generated/deal-"):
        existing.pop("image")  # the card prints "Price as of"; generate_deal_images redraws it with the new time
    return True


def select(results, candidates, deals, run_url, today, limit, log=print, refreshed=None):
    """Return the new deals.json entries for every verified page (up to limit), logging every skip."""
    by_url = {c["url"]: c for c in candidates if isinstance(c, dict) and c.get("url")}
    urls = {d.get("url") for d in deals if isinstance(d, dict)}
    products = {product_id(d) for d in deals if isinstance(d, dict)}  # same ASIN under another link
    new = []
    for row in results:
        if row.get("verdict") != "verified":
            continue
        candidate = by_url.get(row["url"], {})
        if (row.get("store") or candidate.get("store")) == "Amazon":
            log(f"SKIP {row['url']}: {AMAZON_RULE}")
            continue
        if not direct_url(candidate.get("url") or row["url"]):
            log(f"SKIP {row['url']}: affiliate redirect link; publish the retailer's direct product URL")
            continue
        if (row.get("evidence_method") or "retailer_page_check") == "retailer_page_check" and \
                not page_check_allowed(candidate.get("url") or row["url"]):
            log(f"SKIP {row['url']}: automated page reading is not cleared for this retailer's terms")
            continue
        rec = record(row, candidate, run_url)
        existing = next((d for d in deals if isinstance(d, dict) and product_id(d) == product_id(rec)), None)
        if existing is not None:
            if row.get("photo_url") and not existing.get("listing_photo_url") and existing.get("store") == "eBay":
                existing["listing_photo_url"] = row["photo_url"]  # the real product photo replaces any AI picture
            if refreshed is not None and refresh(existing, rec, row["checked_at"]):
                refreshed.append(existing.get("title"))
                log(f"REFRESH {existing.get('title')}: same price re-confirmed at {row['checked_at']}")
            else:
                log(f"SKIP {row['url']}: product already listed ({product_id(rec)})")
            continue
        try:
            entry = check(rec, today, urls)
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
        if row.get("photo_url"):
            entry["listing_photo_url"] = row["photo_url"]
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
    parser.add_argument("--also-results", action="append", default=[], help="more result files (e.g. retailer APIs)")
    parser.add_argument("candidates", nargs="*")
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--max", type=int, default=5)
    parser.add_argument("--apply", action="store_true", help="append the verified deals to deals.json")
    args = parser.parse_args(argv)
    if not args.run_url.startswith("https://"):
        raise SystemExit("--run-url must be the https GitHub Actions run URL (it is the evidence source)")
    today = today or date.today()
    results = json.loads(Path(args.results).read_text(encoding="utf-8"))
    for extra in args.also_results:
        if Path(extra).is_file():
            results += json.loads(Path(extra).read_text(encoding="utf-8"))
    candidates = []
    for path in args.candidates:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        candidates += data if isinstance(data, list) else []
    feed = ROOT / "deals.json"
    deals = json.loads(feed.read_text(encoding="utf-8"))
    refreshed = []
    new = select(results, candidates, deals, args.run_url, today, args.max, refreshed=refreshed)
    print(f"{len(new)} page-verified deal(s) ready; verdicts: "
          + json.dumps({v: sum(r.get('verdict') == v for r in results) for v in sorted({r.get('verdict') for r in results})}))
    if (new or refreshed) and args.apply:
        feed.write_text(json.dumps(deals + new, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Published {len(new)} deal(s) to deals.json (publication_approved true); "
              f"re-confirmed {len(refreshed)} listed deal(s).")
    elif new or refreshed:
        print("Dry run: deals.json unchanged (use --apply).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

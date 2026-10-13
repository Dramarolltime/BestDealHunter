#!/usr/bin/env python3
"""Add independently verified deals to deals.json, only with evidence. Never deletes anything.

Input: a JSON list of verification records (see docs/adding-verified-deals.md), e.g.

  {"store": "Target", "title": "...", "variant": "Queen, Gray", "seller": "Target",
   "url": "https://www.target.com/p/...", "category": "Home",
   "price": 29.99, "original_price": 79.99, "reference_label": "Reg.",
   "availability": "in stock (shipping)", "expires_date": null,
   "evidence": {"method": "retailer_page_check", "checked_at": "2026-10-13T10:12:00Z",
                "source": "https://github.com/.../actions/runs/123", "checked_by": "verify_listings.py"}}

Every record must pass, or nothing is written:
- evidence.method is one of METHODS; a human browser check must point at a screenshot committed
  under evidence/; an automated check must cite its run URL;
- checked within max_verification_age_days, not in the future;
- the seller is named, availability says it can be bought now, the link is on the store's domain;
- the discount, computed here from price and original_price and rounded down, is >= 50%;
- strict validate() passes for the new deal and it isn't already listed.

New deals are added with publication_approved false: cards, Cloudinary and Buffer drafts follow,
and a person approves publication. Dry run by default; --apply writes deals.json.

  python scripts/add_verified_deals.py verified-deals.json          # check only
  python scripts/add_verified_deals.py verified-deals.json --apply  # append to deals.json
"""
import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from validate_deals import RULES, validate, validate_feed

ROOT = Path(__file__).resolve().parents[1]
METHODS = {"retailer_page_check", "retailer_api", "affiliate_catalog", "human_browser_check"}
STORE_DOMAINS = {"Target": "target.com", "Walmart": "walmart.com", "Best Buy": "bestbuy.com", "Macy's": "macys.com",
                 "Home Depot": "homedepot.com", "Lowe's": "lowes.com", "eBay": "ebay.com", "Amazon": "amazon.com"}
BUYABLE = ("in stock", "available", "ships", "shipping", "pickup", "add to cart")
NOT_BUYABLE = ("out of stock", "sold out", "unavailable", "not available", "discontinued")


class Rejected(ValueError):
    pass


def discount(price, original):
    return int((original - price) * 100 // original)


def check(record, today, existing_urls):
    """Return the deals.json entry for a record, or raise Rejected with every problem found."""
    problems = []
    for field in ("store", "title", "url", "category", "seller", "availability", "reference_label"):
        if not isinstance(record.get(field), str) or not record[field].strip():
            problems.append(f"missing {field}")
    price, original = record.get("price"), record.get("original_price")
    if type(price) not in (int, float) or type(original) not in (int, float) or not 0 < price < original:
        problems.append("price and original_price must be numbers with 0 < price < original_price")
        pct = None
    else:
        pct = discount(price, original)
        if pct < RULES["min_discount_percent"]:
            problems.append(f"only {pct}% off (needs {RULES['min_discount_percent']}%)")
    store, url = record.get("store"), str(record.get("url", ""))
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    domain = STORE_DOMAINS.get(store)
    if not domain or not (host == domain or host.endswith("." + domain)):
        problems.append(f"link host {host or '?'} is not {store}'s site")
    avail = str(record.get("availability", "")).lower()
    if any(w in avail for w in NOT_BUYABLE) or not any(w in avail for w in BUYABLE):
        problems.append(f"availability {record.get('availability')!r} does not show it can be bought now")
    ev = record.get("evidence") if isinstance(record.get("evidence"), dict) else {}
    method = ev.get("method")
    if method not in METHODS:
        problems.append(f"evidence.method must be one of {sorted(METHODS)}")
    try:
        checked = datetime.fromisoformat(str(ev.get("checked_at", "")).replace("Z", "+00:00")).date()
        if checked > today:
            problems.append("evidence.checked_at is in the future")
        elif checked < today - timedelta(days=RULES["max_verification_age_days"]):
            problems.append("evidence is too old; re-check the price")
    except ValueError:
        checked = None
        problems.append("evidence.checked_at must be an ISO timestamp")
    source = str(ev.get("source", ""))
    if method == "human_browser_check":
        if not ev.get("checked_by"):
            problems.append("human check needs evidence.checked_by")
        if not source.startswith("evidence/") or not (ROOT / source).is_file():
            problems.append("human check needs a screenshot committed under evidence/ (evidence.source)")
    elif method in METHODS and not source.startswith("https://"):
        problems.append("automated check needs evidence.source = the run or API URL")
    if url in existing_urls:
        problems.append("already listed")
    if problems:
        raise Rejected(f"{record.get('title', 'untitled')}: " + "; ".join(problems))
    title = record["title"].strip() + (f" — {record['variant'].strip()}" if record.get("variant") else "")
    deal = {
        "store": store, "title": title, "category": record["category"], "url": url,
        "price": float(price), "original_price": float(original),
        "verified_date": checked.isoformat(),
        "source": f"{store} product page ({record['seller'].strip()} seller)",
        "price_evidence": (f"{method} {ev['checked_at']}: {store} showed ${price:.2f}, "
                           f"{record['reference_label'].strip()} ${original:.2f}, {record['availability'].strip()}; {source}"),
        "seller": record["seller"].strip(), "reference_label": record["reference_label"].strip(),
        "publication_approved": False,
    }
    if record.get("expires_date"):
        deal["expires_date"] = record["expires_date"]
    validate([deal], today)  # strict: fresh, >= 50%, known store/category, valid URL
    return deal


def run(records, deals, today):
    """Return (accepted entries, rejection messages). All-or-nothing is enforced by main()."""
    urls = {d.get("url") for d in deals if isinstance(d, dict)}
    accepted, rejected = [], []
    for record in records:
        try:
            entry = check(record, today, urls)
            accepted.append(entry)
            urls.add(entry["url"])
        except (Rejected, ValueError) as exc:
            rejected.append(str(exc))
    return accepted, rejected


def main(argv=None, today=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("records")
    parser.add_argument("--apply", action="store_true", help="append the accepted deals to deals.json")
    args = parser.parse_args(argv)
    today = today or date.today()
    records = json.loads(Path(args.records).read_text(encoding="utf-8"))
    feed_path = ROOT / "deals.json"
    deals = json.loads(feed_path.read_text(encoding="utf-8"))
    accepted, rejected = run(records if isinstance(records, list) else [], deals, today)
    for msg in rejected:
        print(f"REJECTED {msg}")
    for d in accepted:
        print(f"OK {d['store']}: {d['title']} ${d['price']:.2f} vs ${d['original_price']:.2f} "
              f"= {discount(d['price'], d['original_price'])}% off")
    if rejected or not accepted:
        print("Nothing written." if rejected else "No records.")
        return 1 if rejected else 0
    if not args.apply:
        print(f"Dry run: {len(accepted)} deal(s) would be added (publication_approved false). Use --apply.")
        return 0
    updated = deals + accepted  # append only; existing history is never touched
    validate_feed(updated, today)
    feed_path.write_text(json.dumps(updated, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Added {len(accepted)} deal(s); deals.json now has {len(updated)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

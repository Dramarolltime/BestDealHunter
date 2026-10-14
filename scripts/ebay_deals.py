#!/usr/bin/env python3
"""eBay deals from the official eBay Browse API (free developer keyset; no page reading).

Compliant source: eBay publishes the Browse API for developers. Pages are never opened, seller
images are not used (cards are our own), and links are plain ebay.com item URLs without tracking.
Writes rows in verify_listings.py's format, so auto_publish.py treats them like any other check.
A row is "verified" only when eBay itself reports, for the listing:
  * a marketingPrice with an originalPrice above the current price, >= 50% off (floored, in cents),
    shown by eBay as a strikethrough / list price (priceTreatment STRIKETHROUGH or LIST_PRICE);
  * condition New, fixed price (Buy It Now), located in and shipping to the US;
  * an established seller (feedback >= 98% and score >= 1000), so the "was" price is credible.

  EBAY_CLIENT_ID=... EBAY_CLIENT_SECRET=... python scripts/ebay_deals.py --out ebay-results.json
Without both secrets it writes an empty list and says so (dormant).
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
SCOPE = "https://api.ebay.com/oauth/api_scope"
# Broad, deal-heavy eBay US categories: Consumer Electronics, Home & Garden, Clothing/Shoes, Health & Beauty,
# Sporting Goods, Toys & Hobbies, Computers/Tablets.
CATEGORIES = {"293": "Electronics", "11700": "Home", "11450": "Fashion", "26395": "Beauty", "888": "Sports",
              "220": "Other", "58058": "Electronics"}
FILTER = ("buyingOptions:{FIXED_PRICE},conditions:{NEW},itemLocationCountry:US,deliveryCountry:US,"
          "price:[20..],priceCurrency:USD")
MIN_DISCOUNT, MIN_FEEDBACK_PCT, MIN_FEEDBACK_SCORE = 50, 98.0, 1000
SHOWN_AS_REFERENCE = {"STRIKETHROUGH", "LIST_PRICE"}
PAGE_SIZE, PAGES_PER_CATEGORY = 200, 3


def cents(amount):
    try:
        return round(float((amount or {}).get("value")) * 100)
    except (TypeError, ValueError):
        return None


def item_url(item):
    """https://www.ebay.com/itm/<legacy id>, no tracking or hash parameters."""
    legacy = item.get("legacyItemId")
    if not legacy and str(item.get("itemId", "")).count("|") >= 1:  # "v1|<legacy id>|0"
        legacy = str(item["itemId"]).split("|")[1]
    if legacy and str(legacy).isdigit():
        return f"https://www.ebay.com/itm/{legacy}"
    return str(item.get("itemWebUrl") or "").split("?")[0]


def row_for(item, checked_at, category=""):
    price = cents(item.get("price"))
    marketing = item.get("marketingPrice") or {}
    original = cents(marketing.get("originalPrice"))
    seller = item.get("seller") or {}
    try:
        feedback_pct = float(seller.get("feedbackPercentage") or 0)
        feedback_score = int(seller.get("feedbackScore") or 0)
    except (TypeError, ValueError):
        feedback_pct, feedback_score = 0.0, 0
    pct = (original - price) * 100 // original if price and original and original > price else None
    url = item_url(item)
    trusted = feedback_pct >= MIN_FEEDBACK_PCT and feedback_score >= MIN_FEEDBACK_SCORE
    new = str(item.get("condition") or "").lower() == "new" or str(item.get("conditionId")) == "1000"
    if not url.startswith("https://www.ebay.com/itm/") or pct is None:
        verdict = "unconfirmed"
    elif marketing.get("priceTreatment") not in SHOWN_AS_REFERENCE:
        verdict = "unconfirmed"  # eBay does not present the original price as a reference for this listing
    elif not new or not trusted:
        verdict = "unconfirmed"
    elif pct < MIN_DISCOUNT:
        verdict = "below_50"
    else:
        verdict = "verified"
    label = "Was" if marketing.get("priceTreatment") == "STRIKETHROUGH" else "List price"
    return {
        "url": url, "store": "eBay", "verdict": verdict, "checked_at": checked_at,
        "title": str(item.get("title") or "").strip(), "evidence_method": "retailer_api",
        "evidence_detail": (f"eBay Browse API item {item.get('itemId')} ({marketing.get('priceTreatment')}), "
                            f"seller {seller.get('username')} {feedback_pct}% / {feedback_score}"),
        "category_hint": category,
        "page": {"price": price / 100 if price else None, "reference": original / 100 if original else None,
                 "reference_label": label, "discount": pct, "buyable": True,
                 "seller": f"eBay seller {seller.get('username')}" if seller.get("username") else None},
    }


def token(client_id, secret, opener=urllib.request.urlopen):
    auth = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
    body = urllib.parse.urlencode({"grant_type": "client_credentials", "scope": SCOPE}).encode()
    req = urllib.request.Request(TOKEN_URL, data=body, method="POST", headers={
        "Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"})
    with opener(req, timeout=30) as resp:
        return json.load(resp)["access_token"]


def search(access_token, category, offset, opener=urllib.request.urlopen):
    params = urllib.parse.urlencode({"category_ids": category, "filter": FILTER, "limit": PAGE_SIZE, "offset": offset})
    req = urllib.request.Request(f"{SEARCH_URL}?{params}", headers={
        "Authorization": f"Bearer {access_token}", "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
        "User-Agent": "BestDealHunter/1.0 (eBay Browse API client)"})
    with opener(req, timeout=30) as resp:
        return json.load(resp)


def collect(access_token, opener=urllib.request.urlopen, sleep=time.sleep):
    items = []
    for category, name in CATEGORIES.items():
        for page in range(PAGES_PER_CATEGORY):
            data = search(access_token, category, page * PAGE_SIZE, opener)
            found = data.get("itemSummaries") or []
            items += [(i, name) for i in found if isinstance(i, dict) and i.get("marketingPrice")]
            if len(found) < PAGE_SIZE:
                break
            sleep(0.2)
    return items


def main(argv=None, env=os.environ, opener=urllib.request.urlopen, sleep=time.sleep):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="ebay-results.json")
    args = parser.parse_args(argv)
    client_id, secret = env.get("EBAY_CLIENT_ID"), env.get("EBAY_CLIENT_SECRET")
    rows = []
    if not (client_id and secret):
        print("eBay Browse API: EBAY_CLIENT_ID / EBAY_CLIENT_SECRET not set; source dormant "
              "(free keyset at developer.ebay.com).")
    else:
        checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            items = collect(token(client_id, secret, opener), opener, sleep)
        except urllib.error.HTTPError as exc:
            print(f"eBay API returned HTTP {exc.code}; no rows written.")
            items = []
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as exc:
            print(f"eBay API request failed ({type(exc).__name__}); no rows written.")
            items = []
        seen, rows = set(), []
        for item, category in items:
            row = row_for(item, checked_at, category)
            if row["url"] not in seen:
                seen.add(row["url"])
                rows.append(row)
        counts = {}
        for r in rows:
            counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
        print(f"eBay Browse API: {len(rows)} discounted listings; verdicts {json.dumps(counts)}")
        for r in rows:
            if r["verdict"] == "verified":
                p = r["page"]
                print(f"  verified: {r['title'][:80]} ${p['price']:.2f} vs {p['reference_label']} "
                      f"${p['reference']:.2f} ({p['discount']}% off) {r['url']}")
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=2, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())

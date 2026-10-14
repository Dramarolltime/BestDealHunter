#!/usr/bin/env python3
"""Best Buy deals from the official Best Buy Products API (free key; no page reading).

Compliant source: Best Buy publishes this API for developers. Pages are never opened, no product
images are used (cards are our own), and the link is the plain bestbuy.com product URL with no
affiliate tracking. Writes rows in verify_listings.py's format so auto_publish.py treats an API
result exactly like a page check: verdict "verified" only when Best Buy itself reports
  * a current sale price below its regular price, >= 50% off (floored, computed here in cents);
  * online availability true, sold by Best Buy (marketplace listings excluded).

  BEST_BUY_API_KEY=... python scripts/bestbuy_deals.py --out bestbuy-results.json
Without the key it writes an empty list and says so (dormant).
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API = "https://api.bestbuy.com/v1/products({query})"
QUERY = "onSale=true&onlineAvailability=true&percentSavings>=50"
FIELDS = ("sku,name,url,salePrice,regularPrice,percentSavings,onlineAvailability,marketplace,"
          "categoryPath.name,priceUpdateDate")
MIN_DISCOUNT = 50
PAGE_SIZE = 50


def cents(value):
    try:
        return round(float(value) * 100)
    except (TypeError, ValueError):
        return None


def row_for(product, checked_at):
    """A verify_listings-style row built only from Best Buy's own API fields."""
    price, regular = cents(product.get("salePrice")), cents(product.get("regularPrice"))
    url = str(product.get("url") or "").split("?")[0]  # plain product URL, no tracking parameters
    if not url and product.get("sku"):
        url = f"https://www.bestbuy.com/site/{product['sku']}.p"
    pct = (regular - price) * 100 // regular if price and regular and regular > price else None
    buyable = product.get("onlineAvailability") is True and product.get("marketplace") is not True
    if not url.startswith("https://www.bestbuy.com/") or pct is None:
        verdict = "unconfirmed"
    elif not buyable:
        verdict = "unavailable"
    elif pct < MIN_DISCOUNT:
        verdict = "below_50"
    else:
        verdict = "verified"
    categories = [c.get("name") for c in product.get("categoryPath") or [] if isinstance(c, dict)]
    return {
        "url": url, "store": "Best Buy", "verdict": verdict, "checked_at": checked_at,
        "title": str(product.get("name") or "").strip(), "evidence_method": "retailer_api",
        "evidence_detail": f"Best Buy Products API sku {product.get('sku')}, priceUpdateDate {product.get('priceUpdateDate')}",
        "category_hint": " / ".join(c for c in categories if c),
        "page": {"price": price / 100 if price else None, "reference": regular / 100 if regular else None,
                 "reference_label": "Regular price", "discount": pct, "buyable": buyable, "seller": "Best Buy"},
    }


def fetch(key, query=QUERY, pages=4, opener=urllib.request.urlopen, sleep=time.sleep):
    products = []
    for page in range(1, pages + 1):
        params = urllib.parse.urlencode({"apiKey": key, "format": "json", "show": FIELDS, "pageSize": PAGE_SIZE,
                                         "page": page, "sort": "percentSavings.dsc"})
        url = API.format(query=urllib.parse.quote(query, safe="=&<>")) + "?" + params
        with opener(urllib.request.Request(url, headers={"User-Agent": "BestDealHunter/1.0 (Best Buy API client)"}),
                    timeout=30) as resp:
            data = json.load(resp)
        products += data.get("products") or []
        if page >= int(data.get("totalPages") or 1):
            break
        sleep(0.3)  # stay well under the API's per-second limit
    return products


def main(argv=None, env=os.environ, opener=urllib.request.urlopen):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="bestbuy-results.json")
    args = parser.parse_args(argv)
    key = env.get("BEST_BUY_API_KEY") or env.get("BESTBUY_API_KEY")
    rows = []
    if not key:
        print("Best Buy Products API: no BEST_BUY_API_KEY secret; source dormant (free key at developer.bestbuy.com).")
    else:
        checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            try:
                products = fetch(key, opener=opener)
            except urllib.error.HTTPError as exc:
                if exc.code != 400:
                    raise
                # the percentSavings filter isn't accepted: ask for on-sale items and filter here
                products = fetch(key, query="onSale=true&onlineAvailability=true", pages=8, opener=opener)
        except urllib.error.HTTPError as exc:
            print(f"Best Buy Products API returned HTTP {exc.code}; no rows written.")
            products = []
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            print(f"Best Buy Products API request failed ({type(exc).__name__}); no rows written.")
            products = []
        rows = [row_for(p, checked_at) for p in products if isinstance(p, dict)]
        counts = {}
        for r in rows:
            counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
        print(f"Best Buy Products API: {len(products)} products; verdicts {json.dumps(counts)}")
        for r in rows:
            if r["verdict"] == "verified":
                p = r["page"]
                print(f"  verified: {r['title'][:80]} ${p['price']:.2f} vs regular ${p['reference']:.2f} "
                      f"({p['discount']}% off) {r['url']}")
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=2, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())

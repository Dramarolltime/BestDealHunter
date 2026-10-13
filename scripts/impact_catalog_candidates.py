#!/usr/bin/env python3
"""Impact product-catalog connector for Target and Walmart. INACTIVE until authorized.

Reads product catalogs that Target or Walmart have shared with our Impact account and writes
verification records (evidence.method "affiliate_catalog") for scripts/add_verified_deals.py.
It never edits deals.json or pending_deals.json, never posts anything and never prints the
account SID, token, request URLs or raw catalog rows.

Off unless the repository variable IMPACT_CATALOGS_ENABLED is "true" (set it only after Impact
shows an approved Target/Walmart partnership with a shared catalog). Kept rows must:
  * come from a catalog whose advertiser is Target or Walmart;
  * have a numeric current price below a numeric original price, at least 50% off (floored);
  * be in stock (StockAvailability InStock / LimitedAvailability);
  * link to the retailer's own domain (Impact tracking links such as goto.target.com qualify);
  * not be sold by a marketplace seller other than the retailer, when the row names a seller.

  python scripts/impact_catalog_candidates.py [--out impact-candidates.json]
"""
import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlparse

API = "https://api.impact.com"
RETAILERS = {"Target": "target.com", "Walmart": "walmart.com"}
IN_STOCK = {"instock", "limitedavailability"}
MIN_DISCOUNT = 50
MAX_PAGES = 50  # per catalog, PageSize 100: at most 5,000 rows read per catalog
CATEGORIES = (("Electronics", r"electronic|computer|tv|television|audio|camera|phone|video game|tablet"),
              ("Fashion", r"apparel|clothing|shoe|fashion|jewelry|accessor|bag"),
              ("Beauty", r"beauty|cosmetic|skin|hair|fragrance|personal care"),
              ("Sports", r"sport|fitness|outdoor|exercise|bike"),
              ("Home", r"home|kitchen|furniture|bed|bath|decor|garden|appliance|patio"))


def rows(payload, plural, singular):
    """Impact list payloads appear as {"Items": [...]} or {"Items": {"Item": [...]}}."""
    value = payload.get(plural, [])
    if isinstance(value, dict):
        value = value.get(singular, [])
    if isinstance(value, dict):
        value = [value]
    return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []


def retailer_for(catalog):
    name = str(catalog.get("AdvertiserName") or catalog.get("CampaignName") or catalog.get("Name") or "").lower()
    return next((store for store in RETAILERS if store.lower() in name), None)


def number(value):
    try:
        n = float(str(value).replace("$", "").replace(",", ""))
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def category(item):
    text = " ".join(str(item.get(k) or "") for k in ("Category", "SubCategory", "Labels")).lower()
    return next((name for name, pattern in CATEGORIES if re.search(pattern, text)), "Other")


def record(item, store, checked_at, run_url):
    """A record for add_verified_deals.py, or (None, reason) when the row does not qualify."""
    price, original = number(item.get("CurrentPrice")), number(item.get("OriginalPrice"))
    if price is None or original is None or price >= original:
        return None, "no valid current/original price"
    pct = int((original - price) * 100 // original)
    if pct < MIN_DISCOUNT:
        return None, "under 50% off"
    stock = re.sub(r"[^a-z]", "", str(item.get("StockAvailability") or "").lower())
    if stock not in IN_STOCK:
        return None, "not in stock"
    url = str(item.get("Url") or "")
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if urlparse(url).scheme != "https" or not (host == RETAILERS[store] or host.endswith("." + RETAILERS[store])):
        return None, "link is not on the retailer's domain"
    seller = str(item.get("SellerName") or item.get("Seller") or "").strip()
    if seller and store.lower() not in seller.lower():
        return None, "marketplace seller"
    name = str(item.get("Name") or "").strip()
    if not name:
        return None, "no product name"
    variant = ", ".join(str(item[k]).strip() for k in ("Size", "Colors", "Color") if str(item.get(k) or "").strip())
    return {
        "store": store, "title": name, "variant": variant or None, "seller": store, "url": url,
        "category": category(item), "price": price, "original_price": original,
        "reference_label": "Original price (retailer catalog)",
        "availability": "In stock (retailer catalog)",
        "expires_date": None,
        "catalog_item": f"{item.get('CatalogId', '?')}/{item.get('CatalogItemId') or item.get('Id', '?')}",
        "evidence": {"method": "affiliate_catalog", "checked_at": checked_at, "source": run_url,
                     "checked_by": "impact_catalog_candidates.py"},
    }, "ok"


class Impact:
    def __init__(self, sid, token, opener=urllib.request.urlopen):
        self.sid, self.opener = sid, opener
        self.auth = "Basic " + base64.b64encode(f"{sid}:{token}".encode()).decode()

    def get(self, path):
        """GET an API path (or an @nextpageuri, which Impact returns relative to the host)."""
        if not path.startswith("/Mediapartners/"):
            raise ValueError("unexpected Impact path")
        req = urllib.request.Request(API + path, headers={
            "Authorization": self.auth, "Accept": "application/json",
            "User-Agent": "BestDealHunter-catalog-connector/1.0"})
        with self.opener(req, timeout=30) as response:
            return json.load(response)

    def pages(self, path, plural, singular, limit=MAX_PAGES):
        for _ in range(limit):
            payload = self.get(path)
            yield from rows(payload, plural, singular)
            path = payload.get("@nextpageuri") or ""
            if not path:
                return


def collect(client, checked_at, run_url):
    """Return (records, report). The report holds counts only."""
    records, report, seen = [], {"catalogs": 0, "retailer_catalogs": {}, "rows_read": 0, "skipped": {}}, set()
    catalogs = list(client.pages(f"/Mediapartners/{client.sid}/Catalogs?PageSize=100", "Catalogs", "Catalog", 10))
    report["catalogs"] = len(catalogs)
    for catalog in catalogs:
        store = retailer_for(catalog)
        if not store or not str(catalog.get("Id") or "").isalnum():
            continue
        report["retailer_catalogs"][store] = report["retailer_catalogs"].get(store, 0) + 1
        path = f"/Mediapartners/{client.sid}/Catalogs/{catalog['Id']}/Items?PageSize=100"
        for item in client.pages(path, "Items", "Item"):
            report["rows_read"] += 1
            rec, reason = record(item, store, checked_at, run_url)
            if rec and rec["url"] in seen:
                rec, reason = None, "duplicate"
            if not rec:
                report["skipped"][reason] = report["skipped"].get(reason, 0) + 1
                continue
            seen.add(rec["url"])
            records.append(rec)
    return records, report


def main(argv=None, opener=urllib.request.urlopen, env=os.environ):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="impact-candidates.json")
    args = parser.parse_args(argv)
    if env.get("IMPACT_CATALOGS_ENABLED") != "true":
        print("Impact catalog connector is inactive (IMPACT_CATALOGS_ENABLED is not 'true'). Nothing read.")
        return 0
    sid, token = env.get("IMPACT_ACCOUNT_SID"), env.get("IMPACT_AUTH_TOKEN")
    if not sid or not token:
        print("Missing Impact secret(s); nothing read.")
        return 2
    run_url = env.get("RUN_URL", "")
    if not run_url.startswith("https://"):
        print("RUN_URL (the GitHub Actions run URL used as evidence) is required.")
        return 2
    checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        records, report = collect(Impact(sid, token, opener), checked_at, run_url)
    except urllib.error.HTTPError as exc:
        print(f"Impact returned HTTP {exc.code}; no candidates written.")
        return 1
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        print(f"Impact request failed ({type(exc).__name__}); no candidates written.")
        return 1
    print(f"Catalogs shared with the account: {report['catalogs']}; Target/Walmart: {report['retailer_catalogs'] or 'none'}")
    print(f"Rows read: {report['rows_read']}; kept (>=50% off, in stock, retailer link): {len(records)}; "
          f"skipped: {report['skipped']}")
    if not report["retailer_catalogs"]:
        print("No Target or Walmart catalog is shared yet. Apply to their programs in Impact first.")
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(records, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(f"Wrote {len(records)} record(s) to {args.out}. Next: python scripts/add_verified_deals.py {args.out} "
          "(dry run), then --apply to stage for approval. Nothing was listed or posted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""eBay Browse API discovery adapter: finds candidates for the REVIEW queue only.

Findings that shape this design (issue #1):
* eBay's Buy APIs (incl. Browse) are for approved partners; production access needs an
  application / Application Growth Check. The sandbox is open. No live credentials are
  used until the owner is approved and stores them as GitHub secrets.
* Browse's strikethrough "list price" (`marketingPrice.originalPrice`, priceTreatment
  LIST_PRICE / STP) is the price the *seller* recently listed or sold the item for. It is
  seller-supplied and not independently verified by eBay. So an eBay "50% off" is never
  treated as verified: every candidate lands in pending_deals.json with
  review_status "needs_review" and publication_eligible false, labelled "Seller's list price".
* Applications earning affiliate revenue must send affiliate tracking: the EPN campaign
  id goes in X-EBAY-C-ENDUSERCTX and the item's affiliate URL is used when returned.

We compute the discount ourselves from price and originalPrice (never trusting
discountPercentage alone) and keep only new-condition, fixed-price, USD items at >= 50% off.
Credentials are read from env only and never printed.
"""
import base64
import json
import re
from datetime import datetime, timezone
from urllib import error, parse, request

from validate_deals import RULES

API = {"production": "https://api.ebay.com", "sandbox": "https://api.sandbox.ebay.com"}
SCOPE = "https://api.ebay.com/oauth/api_scope"
MARKETPLACE = "EBAY_US"
ITEM_HOST = re.compile(r"^https://(www\.)?ebay\.com/", re.I)


class SourceError(Exception):
    pass


def http(method, url, body=None, headers=None, timeout=30):
    req = request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except error.HTTPError as exc:
        return exc.code, exc.read()


def app_token(client_id, client_secret, env="production", transport=http):
    """Client-credentials application token (no user data, read-only browse scope)."""
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    body = parse.urlencode({"grant_type": "client_credentials", "scope": SCOPE}).encode()
    status, raw = transport("POST", f"{API[env]}/identity/v1/oauth2/token", body, {
        "Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"})
    if status != 200:
        raise SourceError(f"eBay token request failed: HTTP {status}")
    token = json.loads(raw or b"{}").get("access_token")
    if not token:
        raise SourceError("eBay token response had no access_token")
    return token


def search(token, query, campaign_id=None, env="production", limit=50, transport=http):
    params = {
        "q": query["q"],
        "filter": "buyingOptions:{FIXED_PRICE},conditions:{NEW},priceCurrency:USD",
        "limit": str(min(int(query.get("limit", limit)), 200)),
    }
    if query.get("category_ids"):
        params["category_ids"] = query["category_ids"]
    headers = {"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE}
    if campaign_id:
        headers["X-EBAY-C-ENDUSERCTX"] = f"affiliateCampaignId={campaign_id}"
    status, raw = transport("GET", f"{API[env]}/buy/browse/v1/item_summary/search?{parse.urlencode(params)}", None, headers)
    if status != 200:
        raise SourceError(f"eBay search failed for {query['q']!r}: HTTP {status}")
    return json.loads(raw or b"{}").get("itemSummaries", [])


def money(value):
    if not isinstance(value, dict) or value.get("currency") != "USD":
        return None
    try:
        amount = round(float(value["value"]), 2)
    except (KeyError, TypeError, ValueError):
        return None
    return amount if amount > 0 else None


def candidate(item, category, now):
    """Return (candidate, None) or (None, reason). Never invents a reference price."""
    price = money(item.get("price"))
    marketing = item.get("marketingPrice") or {}
    original = money(marketing.get("originalPrice"))
    if price is None:
        return None, "no USD price"
    if original is None:
        return None, "no seller reference price (marketingPrice.originalPrice)"
    if not price < original:
        return None, "reference price not above price"
    if str(item.get("condition", "")).lower() != "new":
        return None, "not new condition"
    if "FIXED_PRICE" not in (item.get("buyingOptions") or []):
        return None, "not a fixed-price listing"
    percent = int((original - price) * 100 // original)
    reported = marketing.get("discountPercentage")
    if reported is not None and abs(float(reported) - (original - price) * 100 / original) > 1.5:
        return None, "eBay discountPercentage disagrees with price/originalPrice"
    if percent < RULES["min_discount_percent"]:
        return None, f"only {percent}% off"
    url = item.get("itemAffiliateWebUrl") or item.get("itemWebUrl") or ""
    if not ITEM_HOST.match(url):
        return None, "missing eBay item URL"
    title = str(item.get("title") or "").strip()
    if not title:
        return None, "missing title"
    stamp = now.strftime("%Y-%m-%d %H:%M UTC")
    treatment = marketing.get("priceTreatment", "unspecified")
    return {
        "store": "eBay",
        "title": title[:200],
        "category": category,
        "url": url,
        "price": price,
        "original_price": original,
        "reference_price_label": "Seller's list price",
        "reference_price_type": "eBay seller-provided strikethrough list price (not independently verified)",
        "source": f"eBay Browse API item_summary/search, item {item.get('itemId', 'unknown')}",
        "price_evidence": (f"eBay Browse API at {stamp}: price ${price:,.2f}, marketingPrice.originalPrice "
                           f"${original:,.2f} (priceTreatment {treatment}). The reference is the seller's own "
                           "recent list/sale price per eBay docs, not an independent price history."),
        "observed_date": now.date().isoformat(),
        "verified_date": now.date().isoformat(),
        "review_status": "needs_review",
        "publication_eligible": False,
        "review_note": "Seller-provided reference price. Confirm listing, seller, stock and price history before approval.",
        "ebay_item_id": item.get("itemId"),
    }, None


def collect(queries, token, campaign_id=None, env="production", transport=http, now=None, log=print):
    now = now or datetime.now(timezone.utc)
    found, seen = [], set()
    for query in queries:
        category = query.get("category", "Other")
        if category not in RULES["categories"]:
            raise SourceError(f"query {query.get('q')!r} has unknown category {category!r}")
        for item in search(token, query, campaign_id, env, transport=transport):
            item_id = item.get("itemId")
            if item_id in seen:
                continue
            seen.add(item_id)
            cand, reason = candidate(item, category, now)
            if cand:
                found.append(cand)
            else:
                log(f"skip {item_id}: {reason}")
    return found

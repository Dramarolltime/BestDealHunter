"""Validate the public feed. Fails closed; rules live in deal_rules.json.

Main-section deals must be at least 50% off. Affiliate-section deals
(`section: "affiliate"` or `affiliate_section: true`) may be 20-49% off, but
still need an `affiliate_disclosure` and the same evidence and freshness.

Archive policy (#1): published deals are never deleted. A deal is *archived* once its
verification is older than `max_verification_age_days`, its `expires_date` has passed,
or it has `status: "archived"`. The site keeps archived deals in "Past Deals" labelled
"Expired · price not verified", with the price shown as the price at time of posting.

* `validate(deals)` is strict: every deal must be active. Use it for anything new or
  outgoing (approvals, refreshes, card generation, social posts).
* `validate_feed(deals)` checks the published feed: active and archived deals need the
  same structure and evidence, but archived deals may be stale. Verification dates are
  never refreshed to make a deal look active.
"""
import json
import re
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse, parse_qs

RULES = json.loads((Path(__file__).resolve().parent / "deal_rules.json").read_text(encoding="utf-8"))
ALLOWED = set(RULES["stores"])
CATEGORIES = set(RULES["categories"])
DOMAINS = tuple(RULES["retailer_domains"])
IMAGE = re.compile(r"images/[\w./-]+\.(?:jpg|jpeg|png|webp)", re.I)

def is_affiliate(deal):
    return deal.get("section") == "affiliate" or deal.get("affiliate_section") is True

def known_domain(host):
    host = host.lower().removeprefix("www.")
    return any(host == d or host.endswith("." + d) for d in DOMAINS)

def deal_status(deal, today=None):
    """'active' or 'archived'. Unparseable dates count as archived (validation reports them)."""
    today = today or date.today()
    if deal.get("status") == "archived":
        return "archived"
    expires = deal.get("expires_date")
    if isinstance(expires, str) and expires < today.isoformat():
        return "archived"
    try:
        checked = date.fromisoformat(deal["verified_date"])
    except (KeyError, TypeError, ValueError):
        return "archived"
    return "archived" if checked < today - timedelta(days=RULES["max_verification_age_days"]) else "active"

def validate_feed(data, today=None):
    """Validate the published feed, allowing archived (stale/expired) deals. Returns (active, archived)."""
    today = today or date.today()
    if not isinstance(data, list):
        raise ValueError("Feed must be an array")
    validate(data, today, allow_archived=True)
    archived = sum(1 for d in data if deal_status(d, today) == "archived")
    return len(data) - archived, archived

def validate(data, today=None, allow_archived=False):
    if not isinstance(data, list):
        raise ValueError("Feed must be an array")
    today = today or date.today()
    seen = set()
    for i, deal in enumerate(data):
        if not isinstance(deal, dict):
            raise ValueError(f"Deal {i}: expected object")
        if deal.get("store") not in ALLOWED:
            raise ValueError(f"Deal {i}: unknown store")
        for field in ("title", "category", "source", "price_evidence"):
            if not isinstance(deal.get(field), str) or not deal[field].strip():
                raise ValueError(f"Deal {i}: missing {field}")
        if deal["category"] not in CATEGORIES:
            raise ValueError(f"Deal {i}: unknown category {deal['category']!r}")
        url = deal.get("url", "")
        parsed = urlparse(url) if isinstance(url, str) else None
        if not parsed or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError(f"Deal {i}: invalid HTTPS URL")
        if not known_domain(parsed.hostname) and not deal.get("affiliate_disclosure"):
            raise ValueError(f"Deal {i}: unrecognized link domain requires affiliate_disclosure")
        key = (parsed.hostname.lower().removeprefix("www."), parsed.path.lower(), tuple(parse_qs(parsed.query).get("ID", [])))
        if key in seen:
            raise ValueError(f"Deal {i}: duplicate product URL")
        seen.add(key)
        price, original = deal.get("price"), deal.get("original_price")
        if type(price) not in (int, float) or type(original) not in (int, float) or not (0 < price < original):
            raise ValueError(f"Deal {i}: invalid prices")
        minimum = RULES["min_affiliate_discount_percent"] if is_affiliate(deal) else RULES["min_discount_percent"]
        if price > original * (1 - minimum / 100):
            raise ValueError(f"Deal {i}: discount must be at least {minimum}%")
        if is_affiliate(deal) and not (isinstance(deal.get("affiliate_disclosure"), str) and deal["affiliate_disclosure"].strip()):
            raise ValueError(f"Deal {i}: affiliate-section deal requires affiliate_disclosure")
        try:
            checked = date.fromisoformat(deal["verified_date"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Deal {i}: invalid verified_date") from exc
        if checked > today:
            raise ValueError(f"Deal {i}: verified_date is in the future")
        expires = deal.get("expires_date")
        if expires is not None:
            try:
                date.fromisoformat(expires)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Deal {i}: invalid expires_date") from exc
        if deal.get("status") not in (None, "active", "archived"):
            raise ValueError(f"Deal {i}: status must be 'active' or 'archived'")
        if not allow_archived and deal_status(deal, today) == "archived":
            raise ValueError(f"Deal {i}: verification is stale or the deal is archived")
        if deal.get("promo_code") and not deal.get("promo_note"):
            raise ValueError(f"Deal {i}: promo_code requires promo_note with terms")
        image = deal.get("image")
        if image is not None and not (isinstance(image, str) and ((IMAGE.fullmatch(image) and ".." not in image) or image.startswith("https://"))):
            raise ValueError(f"Deal {i}: invalid image path")
    return len(data)

if __name__ == "__main__":
    active, archived = validate_feed(json.loads(Path("deals.json").read_text(encoding="utf-8")))
    print(f"Validated {active + archived} public deal(s): {active} active, {archived} archived (shown as expired).")

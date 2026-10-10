"""Validate the public feed. Fails closed; rules live in deal_rules.json.

Main-section deals must be at least 50% off; the affiliate section
(`section: "affiliate"` or `affiliate_section: true`) only needs a real discount.
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

def validate(data, today=None):
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
        if not is_affiliate(deal) and price > original * (1 - RULES["min_discount_percent"] / 100):
            raise ValueError(f"Deal {i}: discount must be at least {RULES['min_discount_percent']}%")
        try:
            checked = date.fromisoformat(deal["verified_date"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Deal {i}: invalid verified_date") from exc
        if not today - timedelta(days=RULES["max_verification_age_days"]) <= checked <= today:
            raise ValueError(f"Deal {i}: verification is stale")
        if deal.get("promo_code") and not deal.get("promo_note"):
            raise ValueError(f"Deal {i}: promo_code requires promo_note with terms")
        image = deal.get("image")
        if image is not None and not (isinstance(image, str) and ((IMAGE.fullmatch(image) and ".." not in image) or image.startswith("https://"))):
            raise ValueError(f"Deal {i}: invalid image path")
    return len(data)

if __name__ == "__main__":
    count = validate(json.loads(Path("deals.json").read_text(encoding="utf-8")))
    print(f"Validated {count} public deal(s).")

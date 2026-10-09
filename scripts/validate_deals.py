"""Validate the public feed. Only verified 50%+ offers are eligible."""
import json
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse

ALLOWED = {"Amazon", "eBay", "Walmart", "Target", "Best Buy", "Home Depot", "Lowe's"}

def validate(data):
    if not isinstance(data, list):
        raise ValueError("Feed must be an array")
    for i, deal in enumerate(data):
        if not isinstance(deal, dict):
            raise ValueError(f"Deal {i}: expected object")
        if deal.get("store") not in ALLOWED:
            raise ValueError(f"Deal {i}: unknown store")
        for field in ("title", "category", "source", "price_evidence"):
            if not isinstance(deal.get(field), str) or not deal[field].strip():
                raise ValueError(f"Deal {i}: missing {field}")
        url = deal.get("url", "")
        parsed = urlparse(url) if isinstance(url, str) else None
        if not parsed or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError(f"Deal {i}: invalid HTTPS URL")
        price, original = deal.get("price"), deal.get("original_price")
        if type(price) not in (int, float) or type(original) not in (int, float) or not (0 < price <= original * 0.5):
            raise ValueError(f"Deal {i}: discount must be at least 50%")
        try:
            checked = date.fromisoformat(deal["verified_date"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Deal {i}: invalid verified_date") from exc
        if not date.today() - timedelta(days=2) <= checked <= date.today():
            raise ValueError(f"Deal {i}: verification is stale")
    return len(data)

if __name__ == "__main__":
    count = validate(json.loads(Path("deals.json").read_text(encoding="utf-8")))
    print(f"Validated {count} public deal(s).")

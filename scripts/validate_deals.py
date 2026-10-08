import json
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse

ALLOWED = {"Amazon", "eBay", "Walmart", "Target", "Best Buy"}
data = json.loads(Path("deals.json").read_text(encoding="utf-8"))
assert isinstance(data, list), "Feed must be an array"
for i, deal in enumerate(data):
    assert isinstance(deal, dict), f"Deal {i}: expected object"
    assert deal.get("store") in ALLOWED, f"Deal {i}: unknown store"
    assert isinstance(deal.get("title"), str) and deal["title"].strip(), f"Deal {i}: missing title"
    assert isinstance(deal.get("category"), str) and deal["category"].strip(), f"Deal {i}: missing category"
    url = deal.get("url", "")
    assert isinstance(url, str) and urlparse(url).scheme == "https" and urlparse(url).hostname, f"Deal {i}: invalid URL"
    price, original = deal.get("price"), deal.get("original_price")
    assert type(price) in (int, float) and type(original) in (int, float) and 0 < price <= original * 0.5, f"Deal {i}: not 50% off"
    checked = date.fromisoformat(deal["verified_date"])
    assert date.today() - timedelta(days=2) <= checked <= date.today(), f"Deal {i}: stale date"
    assert deal.get("source") and deal.get("price_evidence"), f"Deal {i}: missing verification evidence"
print(f"Validated {len(data)} deal(s).")

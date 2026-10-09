#!/usr/bin/env python3
"""Merge approved, verified deal updates without deleting manually published deals."""
import json
import datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / "deals-source.json"
destination = ROOT / "deals.json"
allowed = {"Amazon", "Walmart", "Target", "Best Buy", "eBay", "Home Depot", "Lowe's"}

incoming = json.loads(source.read_text(encoding="utf-8"))
existing = json.loads(destination.read_text(encoding="utf-8"))
if not isinstance(incoming, list) or not isinstance(existing, list):
    raise ValueError("Both deal feeds must be arrays")

def valid(item):
    if not isinstance(item, dict):
        return False
    try:
        store, title, url = item["store"], item["title"], item["url"]
        price, original = float(item["price"]), float(item["original_price"])
        checked = datetime.date.fromisoformat(item["verified_date"])
        parsed = urlparse(url)
        return (store in allowed and isinstance(title, str) and bool(title.strip())
                and parsed.scheme == "https" and bool(parsed.hostname)
                and not parsed.username and not parsed.password
                and 0 < price <= original * 0.5
                and 0 <= (datetime.date.today() - checked).days <= 2)
    except (KeyError, TypeError, ValueError, OverflowError):
        return False

def key(item):
    return (str(item.get("store", "")).casefold(), str(item.get("url", "")).strip())

# Keep manually curated listings untouched. Refresh only when a newer, validated
# entry has the same retailer URL; preserve image and other editorial metadata.
merged = [dict(item) for item in existing if isinstance(item, dict)]
positions = {key(item): index for index, item in enumerate(merged)}
updates = 0
for item in incoming:
    if not valid(item):
        continue
    ident = key(item)
    if ident in positions:
        idx = positions[ident]
        old = merged[idx]
        if item["verified_date"] < str(old.get("verified_date", "")):
            continue
        merged[idx] = {**old, **item}
    else:
        positions[ident] = len(merged)
        merged.append(dict(item))
    updates += 1

# An empty feed must never clear published deals.
if merged != existing:
    destination.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"Accepted {updates} verified source entries; retained {len(merged)} total published deals.")

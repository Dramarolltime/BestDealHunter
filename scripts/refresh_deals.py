#!/usr/bin/env python3
"""Merge approved, verified deal updates without deleting manually published deals."""
import json
from pathlib import Path
from validate_deals import validate

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "deals-source.json"
DESTINATION = ROOT / "deals.json"

def valid(item):
    # Same rules as the public feed validator (deal_rules.json), so refreshes
    # can never admit a deal the hourly check would reject.
    try:
        validate([item])
        return True
    except ValueError:
        return False

def key(item):
    return (str(item.get("store", "")).casefold(), str(item.get("url", "")).strip())

def merge(existing, incoming):
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
    return merged, updates

def main():
    incoming = json.loads(SOURCE.read_text(encoding="utf-8"))
    existing = json.loads(DESTINATION.read_text(encoding="utf-8"))
    if not isinstance(incoming, list) or not isinstance(existing, list):
        raise ValueError("Both deal feeds must be arrays")
    merged, updates = merge(existing, incoming)
    # An empty feed must never clear published deals.
    if merged != existing:
        DESTINATION.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Accepted {updates} verified source entries; retained {len(merged)} total published deals.")

if __name__ == "__main__":
    main()

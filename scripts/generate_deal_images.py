#!/usr/bin/env python3
"""Preserve licensed product images. Never fabricate product photography."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
feed = ROOT / "deals.json"
deals = json.loads(feed.read_text(encoding="utf-8"))
if not isinstance(deals, list):
    raise ValueError("deals.json must contain a list")
missing = [d.get("title", "Untitled") for d in deals if isinstance(d, dict) and not d.get("image")]
print(f"Checked {len(deals)} deals; {len(missing)} missing an authorized product image.")
for title in missing:
    print(f"Image needed (no fake image generated): {title}")
# An approved affiliate feed adapter or manual upload must provide image.
# Do not overwrite deals.json or generate misleading text-only graphics.

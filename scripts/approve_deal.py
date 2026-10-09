"""Move one manually approved, verified deal into the public feed."""
import json
import sys
from pathlib import Path
from validate_deals import validate

PENDING = Path("pending_deals.json")
PUBLIC = Path("deals.json")

def approve(deal_id):
    pending = json.loads(PENDING.read_text(encoding="utf-8"))
    public = json.loads(PUBLIC.read_text(encoding="utf-8"))
    if not isinstance(pending, list) or not isinstance(public, list):
        raise ValueError("Deal files must contain arrays")
    matches = [d for d in pending if isinstance(d, dict) and d.get("id") == deal_id]
    if len(matches) != 1:
        raise ValueError("Expected exactly one pending deal with that ID")
    candidate = dict(matches[0])
    candidate.pop("id", None)
    validate([candidate])
    if any(d.get("url") == candidate["url"] for d in public):
        raise ValueError("Deal URL already published")
    validate(public + [candidate])
    PUBLIC.write_text(json.dumps(public + [candidate], indent=2) + "\n", encoding="utf-8")
    PENDING.write_text(json.dumps([d for d in pending if d.get("id") != deal_id], indent=2) + "\n", encoding="utf-8")
    print("Approved deal; commit both updated files to publish. Buffer is not updated automatically.")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/approve_deal.py DEAL_ID")
    approve(sys.argv[1])

"""Move one manually approved, verified deal into the public feed.

Only deals staged by add_verified_deals.py (they carry evidence_method) can be approved, and the
strict validator re-checks that the price check is still fresh. Listing a deal on the website does
not approve it for Instagram: publication_approved stays as staged (false).
"""
import json
import sys
from pathlib import Path
from validate_deals import validate, validate_feed

PENDING = Path("pending_deals.json")
PUBLIC = Path("deals.json")
STAGING_KEYS = ("id", "review_status")

def approve(deal_id, pending_path=PENDING, public_path=PUBLIC, today=None):
    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    public = json.loads(public_path.read_text(encoding="utf-8"))
    if not isinstance(pending, list) or not isinstance(public, list):
        raise ValueError("Deal files must contain arrays")
    matches = [d for d in pending if isinstance(d, dict) and d.get("id") == deal_id]
    if len(matches) != 1:
        raise ValueError("Expected exactly one pending deal with that ID")
    candidate = {k: v for k, v in matches[0].items() if k not in STAGING_KEYS}
    if not candidate.get("evidence_method"):
        raise ValueError("Deal has no verification evidence; stage it with add_verified_deals.py")
    validate([candidate], today)  # fails if the price check is now older than 2 days
    if any(d.get("url") == candidate["url"] for d in public):
        raise ValueError("Deal URL already published")
    validate_feed(public + [candidate], today)  # existing entries may be archived
    public_path.write_text(json.dumps(public + [candidate], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    pending_path.write_text(json.dumps([d for d in pending if d.get("id") != deal_id], indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")
    print("Approved deal for the website; commit both files in a reviewed PR. "
          "Instagram stays off until publication_approved is set to true.")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/approve_deal.py DEAL_ID")
    approve(sys.argv[1])

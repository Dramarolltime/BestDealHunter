#!/usr/bin/env python3
"""Add eBay Browse API candidates to pending_deals.json for human review.

Dormant (exit 0, no network) until the owner stores EBAY_CLIENT_ID and EBAY_CLIENT_SECRET
as GitHub secrets after eBay approves Buy API access. EBAY_CAMPAIGN_ID (EPN) enables
affiliate links. EBAY_ENV=sandbox runs against eBay's sandbox and is ALWAYS a dry run,
because sandbox listings are test data and must never enter the review queue.

Nothing here publishes: candidates get review_status "needs_review" and publication_eligible
false. Queries live in discovery/ebay_queries.json.
"""
import argparse
import json
import os
import sys
from pathlib import Path
from urllib import error

from ebay_browse import SourceError, app_token, collect

ROOT = Path(__file__).resolve().parents[1]
QUEUE = ROOT / "pending_deals.json"
PUBLIC = ROOT / "deals.json"
QUERIES = ROOT / "discovery" / "ebay_queries.json"
MAX_NEW_PER_RUN = 25


def known_ids(*feeds):
    ids, urls = set(), set()
    for feed in feeds:
        for deal in feed:
            if isinstance(deal, dict):
                if deal.get("ebay_item_id"):
                    ids.add(deal["ebay_item_id"])
                urls.add(str(deal.get("url", "")).split("?")[0])
    return ids, urls


def merge_candidates(queue, public, found, limit=MAX_NEW_PER_RUN):
    ids, urls = known_ids(queue, public)
    added = []
    for cand in found:
        if cand["ebay_item_id"] in ids or cand["url"].split("?")[0] in urls:
            continue
        if len(added) >= limit:
            break
        added.append(cand)
        ids.add(cand["ebay_item_id"])
    return queue + added, added


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="print candidates; never write the queue")
    args = parser.parse_args(argv)

    client_id, secret = os.environ.get("EBAY_CLIENT_ID", ""), os.environ.get("EBAY_CLIENT_SECRET", "")
    if not (client_id and secret):
        print("eBay credentials not configured (needs approved Buy API access); nothing collected.")
        return 0
    env = os.environ.get("EBAY_ENV", "production")
    if env not in ("production", "sandbox"):
        raise SystemExit("EBAY_ENV must be 'production' or 'sandbox'")
    dry_run = args.dry_run or env == "sandbox"
    queries = json.loads(QUERIES.read_text(encoding="utf-8"))
    try:
        token = app_token(client_id, secret, env)
        found = collect(queries, token, os.environ.get("EBAY_CAMPAIGN_ID") or None, env)
    except (SourceError, error.URLError, ValueError) as exc:
        print(f"eBay collection failed: {exc}")
        return 1
    queue = json.loads(QUEUE.read_text(encoding="utf-8"))
    public = json.loads(PUBLIC.read_text(encoding="utf-8"))
    merged, added = merge_candidates(queue, public, found)
    for cand in added:
        print(f"{'Would add' if dry_run else 'Added'} for review: {cand['title']} "
              f"${cand['price']:,.2f} vs seller list ${cand['original_price']:,.2f}")
    if not dry_run and added:
        QUEUE.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(found)} qualifying listing(s), {len(added)} new for review"
          f"{' (dry run: queue unchanged)' if dry_run else ''}. Nothing published.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

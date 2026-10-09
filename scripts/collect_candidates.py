"""Import candidates from a retailer-authorized JSON feed into the review queue.

This is discovery, not checkout verification or publication.
Set DEAL_FEED_URL to an HTTPS JSON endpoint you are authorized to use.
"""
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlparse

QUEUE = Path("pending_deals.json")
MAX_ITEMS = 500

def main():
    url = os.environ.get("DEAL_FEED_URL", "").strip()
    if not url:
        print("No DEAL_FEED_URL configured; no candidates imported.")
        return
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("DEAL_FEED_URL must be a valid HTTPS URL")
    request = Request(url, headers={"User-Agent": "BestDealHunter/1.0", "Accept": "application/json"})
    with urlopen(request, timeout=20) as response:
        if int(response.headers.get("Content-Length", "0")) > 2_000_000:
            raise ValueError("Feed too large")
        payload = response.read(2_000_001)
    if len(payload) > 2_000_000:
        raise ValueError("Feed too large")
    items = json.loads(payload)
    if not isinstance(items, list):
        raise ValueError("Expected a JSON array")
    queue = json.loads(QUEUE.read_text(encoding="utf-8"))
    if not isinstance(queue, list):
        raise ValueError("Invalid pending queue")
    seen = {str(d.get("url", "")).split("?")[0] for d in queue if isinstance(d, dict)}
    added = 0
    for item in items[:MAX_ITEMS]:
        if not isinstance(item, dict):
            continue
        link = item.get("url", "")
        if not isinstance(link, str) or urlparse(link).scheme != "https":
            continue
        key = link.split("?")[0]
        if key in seen:
            continue
        candidate = {k: item[k] for k in ("store", "title", "url", "price", "original_price", "promo_code", "promo_note", "source") if k in item}
        candidate["review_status"] = "unverified"
        candidate["review_note"] = "Requires retailer, stock, checkout, reference price, promo conditions and expiration verification before publishing."
        queue.append(candidate)
        seen.add(key)
        added += 1
    QUEUE.write_text(json.dumps(queue, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Imported {added} unverified candidate(s). Public deals unchanged.")

if __name__ == "__main__":
    main()

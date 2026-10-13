#!/usr/bin/env python3
"""Read-only retailer listing check: what does the live product page say right now?

For each URL, loads the page in headless Chrome (Playwright) and records the HTTP status,
final URL, page title, the schema.org Product/Offer data the retailer publishes
(name, sku, price, currency, availability, seller) and any "was / reg / orig" reference
price text. Nothing is added to a cart and no account is used. Results go to stdout and
verify-results.json so a person can compare them with the candidate's claimed numbers.

  python scripts/verify_listings.py deal-review-queue.json deals.json
"""
import json
import re
import sys
from datetime import datetime, timezone

REF = re.compile(r"(?:Orig\.?|Reg\.?|Regular|Was|List(?: Price)?|Comp\. Value)\s*:?\s*\$\s?([0-9][0-9,]*\.?[0-9]{0,2})", re.I)
NOW = re.compile(r"(?:Now|Sale|Your Price|Price)\s*:?\s*\$\s?([0-9][0-9,]*\.?[0-9]{0,2})", re.I)


def items(path):
    data = json.load(open(path, encoding="utf-8"))
    data = data if isinstance(data, list) else data.get("deals", [])
    for d in data:
        if isinstance(d, dict) and str(d.get("url", "")).startswith("https://"):
            yield d


def offers_from_ldjson(blocks):
    found = []

    def walk(node):
        if isinstance(node, list):
            for n in node:
                walk(n)
        elif isinstance(node, dict):
            types = node.get("@type")
            types = types if isinstance(types, list) else [types]
            if "Product" in types:
                offers = node.get("offers") or {}
                offers = offers if isinstance(offers, list) else [offers]
                for o in offers:
                    if isinstance(o, dict):
                        seller = o.get("seller")
                        found.append({
                            "name": node.get("name"), "sku": node.get("sku") or node.get("productID"),
                            "price": o.get("price") or o.get("lowPrice"), "high_price": o.get("highPrice"),
                            "currency": o.get("priceCurrency"),
                            "availability": str(o.get("availability", "")).rsplit("/", 1)[-1] or None,
                            "seller": seller.get("name") if isinstance(seller, dict) else seller,
                            "price_valid_until": o.get("priceValidUntil"),
                        })
            for v in node.values():
                walk(v)

    for raw in blocks:
        try:
            walk(json.loads(raw))
        except ValueError:
            continue
    return found


def check(ctx, url):
    """One fresh tab per URL (a redirect can't spill into the next check); one retry."""
    for attempt in (1, 2):
        page = ctx.new_page()
        row = _check(page, url)
        page.close()
        if "error" not in row:
            break
        row["attempts"] = attempt
    return row


def _check(page, url):
    row = {"url": url}
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:  # retailer pages keep polling; settle for what has loaded
            pass
        row["http"] = resp.status if resp else None
        row["final_url"] = page.url
        row["title"] = page.title()
        blocks = page.eval_on_selector_all('script[type="application/ld+json"]', "els => els.map(e => e.textContent)")
        row["offers"] = offers_from_ldjson(blocks)
        text = page.inner_text("body")[:200000]
        row["reference_text"] = sorted({m.group(0).strip() for m in REF.finditer(text)})[:8]
        row["price_text"] = sorted({m.group(0).strip() for m in NOW.finditer(text)})[:8]
        low = text.lower()
        row["signals"] = {k: (k in low) for k in ("add to bag", "add to cart", "out of stock", "sold out",
                                                    "no longer available", "access denied", "final sale")}
    except Exception as exc:  # report, never crash the batch
        row["error"] = f"{type(exc).__name__}: {exc}"[:300]
    return row


def main(paths):
    from playwright.sync_api import sync_playwright
    seen, rows = set(), []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        ctx = browser.new_context(locale="en-US", timezone_id="America/New_York", viewport={"width": 1366, "height": 900},
                                  user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                             "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
        for path in paths:
            for d in items(path):
                if d["url"] in seen:
                    continue
                seen.add(d["url"])
                row = check(ctx, d["url"])
                row.update(source_file=path, store=d.get("store"), claimed_title=d.get("title"),
                           claimed_price=d.get("price", d.get("listed_price")),
                           claimed_reference=d.get("original_price", d.get("retailer_previous_price") or d.get("retailer_comparable_value")),
                           checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
                rows.append(row)
                print(json.dumps(row, ensure_ascii=False))
        browser.close()
    json.dump(rows, open("verify-results.json", "w", encoding="utf-8"), indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main(sys.argv[1:] or ["deal-review-queue.json", "deals.json"])

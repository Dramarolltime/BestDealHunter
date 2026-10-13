#!/usr/bin/env python3
"""Find current Amazon deal candidates from Amazon's own deals pages (robots.txt respected).

Discovery only: writes amazon-candidates.json (url = https://www.amazon.com/dp/<ASIN> with our
Associates tag). Nothing is verified here; verify_listings.py then checks each product page for
price, its reference price, >= 50% off and stock.

  python scripts/discover_amazon_deals.py [--max 40]
"""
import argparse
import json
import re
import sys
from urllib import robotparser

DEAL_PAGES = ("https://www.amazon.com/deals", "https://www.amazon.com/gp/goldbox")
ASIN = re.compile(r"/dp/([A-Z0-9]{10})")
TAG = "bestdealhun0b-20"  # the Associates tag already used by our Amazon links in deals.json


def robots_for(ctx):
    resp = ctx.request.get("https://www.amazon.com/robots.txt", timeout=30000)
    rp = robotparser.RobotFileParser()
    if resp.status != 200:
        return None
    rp.parse(resp.text().splitlines())
    return rp


def collect(page):
    """(asin, title) pairs from deal cards: any link to a product page, deduplicated in page order."""
    links = page.eval_on_selector_all(
        "a[href*='/dp/']", "els => els.map(e => [e.href, (e.getAttribute('aria-label') || e.innerText || '').trim()])")
    seen, out = set(), []
    for href, text in links:
        m = ASIN.search(href)
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        out.append((m.group(1), re.sub(r"\s+", " ", text)[:200]))
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--max", type=int, default=40)
    parser.add_argument("--out", default="amazon-candidates.json")
    args = parser.parse_args(argv)
    from playwright.sync_api import sync_playwright
    found = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        ctx = browser.new_context(locale="en-US", timezone_id="America/New_York", viewport={"width": 1366, "height": 900})
        rp = robots_for(ctx)
        if rp is None:
            print("amazon.com/robots.txt could not be read; not crawling (fail closed).")
        for url in DEAL_PAGES if rp else ():
            if not rp.can_fetch("*", url):
                print(f"robots.txt disallows {url}; skipped")
                continue
            page = ctx.new_page()
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
                try:
                    page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass
                for _ in range(4):  # deal grids load as you scroll
                    page.mouse.wheel(0, 2500)
                    page.wait_for_timeout(1200)
                pairs = collect(page)
                print(f"{url}: HTTP {resp.status if resp else '?'} · title {page.title()!r} · {len(pairs)} product links")
                for asin, text in pairs:
                    product = f"https://www.amazon.com/dp/{asin}"
                    if not rp.can_fetch("*", product):
                        continue
                    if all(f["asin"] != asin for f in found):
                        found.append({"store": "Amazon", "asin": asin, "title": text or None,
                                      "url": f"{product}?tag={TAG}", "category": "Other", "discovered_from": url})
            except Exception as exc:
                print(f"{url}: {type(exc).__name__}: {str(exc)[:200]}")
            finally:
                page.close()
            if len(found) >= args.max:
                break
        browser.close()
    found = found[:args.max]
    json.dump(found, open(args.out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"Wrote {len(found)} Amazon candidate(s) to {args.out} (discovery only; not verified).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

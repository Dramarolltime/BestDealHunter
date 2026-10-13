#!/usr/bin/env python3
"""Diagnostic: which allowed retailers serve their public deal pages to an automated, read-only check?

For each retailer deals page: obey robots.txt (skip if disallowed for "*"), load it once in
headless Chrome, and report HTTP status, page title, block-page signals, how many product
links it shows and whether product pages carry schema.org Offer data. No cart, no login.
"""
import json
import re
import sys
from urllib import robotparser
from urllib.parse import urljoin, urlparse

START = {
    "Walmart": ("https://www.walmart.com/shop/deals/clearance", r"/ip/[^\"'?#]+/\d+"),
    "Target": ("https://www.target.com/c/clearance/-/N-5q0ga", r"/p/[^\"'?#]+/-/A-\d+"),
    "Home Depot": ("https://www.homedepot.com/b/Special-Values/N-5yc1vZ1z11adf", r"/p/[^\"'?#]+/\d{9}"),
    "Lowe's": ("https://www.lowes.com/l/shop/clearance", r"/pd/[^\"'?#]+/\d+"),
    "eBay": ("https://www.ebay.com/deals", r"/itm/(?:[^\"'?#/]+/)?\d{9,}"),
    "Best Buy": ("https://www.bestbuy.com/site/misc/deal-of-the-day/pcmcat248000050016.c", r"/site/[^\"'?#]+/\d{7}\.p"),
    "Macy's": ("https://www.macys.com/shop/sale/clearance-closeout?id=54698", r"/shop/product/[^\"'#]+\?ID=\d+"),
}
BLOCK = re.compile(r"access denied|robot or human|are you a human|captcha|blocked|unusual traffic|pardon our interruption", re.I)


def allowed(url):
    rp = robotparser.RobotFileParser(urljoin(url, "/robots.txt"))
    try:
        rp.read()
    except Exception as exc:
        return None, f"robots.txt unreadable: {exc}"
    return rp.can_fetch("*", url), "robots.txt read"


def main():
    from playwright.sync_api import sync_playwright
    out = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        ctx = browser.new_context(locale="en-US", viewport={"width": 1366, "height": 900})
        for store, (url, pattern) in START.items():
            row = {"store": store, "url": url}
            ok, note = allowed(url)
            row["robots_allows"], row["robots_note"] = ok, note
            if ok is False:
                row["result"] = "skipped: robots.txt disallows this page"
                out.append(row); print(json.dumps(row)); continue
            page = ctx.new_page()
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
                try:
                    page.wait_for_load_state("networkidle", timeout=12000)
                except Exception:
                    pass
                html = page.content()
                title = page.title()
                links = sorted({urljoin(url, m) for m in re.findall(pattern, html)})
                row.update(http=resp.status if resp else None, title=title[:120], blocked=bool(BLOCK.search(title)) or (resp and resp.status in (403, 429)),
                           product_links=len(links), sample_links=links[:5])
                if links and not row["blocked"]:
                    pp = ctx.new_page()
                    r2 = pp.goto(links[0], wait_until="domcontentloaded", timeout=45000)
                    ld = pp.eval_on_selector_all('script[type="application/ld+json"]', "els => els.map(e => e.textContent)")
                    row["sample_product"] = {"http": r2.status if r2 else None, "title": pp.title()[:120],
                                             "has_offer_jsonld": any('"Offer"' in b or '"offers"' in b for b in ld),
                                             "blocked": bool(BLOCK.search(pp.title()))}
                    pp.close()
                row["result"] = "blocked" if row["blocked"] else ("usable" if links else "loaded, no product links found")
            except Exception as exc:
                row["result"] = f"error: {type(exc).__name__}: {str(exc)[:160]}"
            page.close()
            out.append(row); print(json.dumps(row))
        browser.close()
    json.dump(out, open("probe-results.json", "w"), indent=2)


if __name__ == "__main__":
    main()

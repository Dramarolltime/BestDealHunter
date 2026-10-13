#!/usr/bin/env python3
"""Diagnostic: can Target's public clearance and product pages be read for price evidence?

robots.txt is checked first. Loads the clearance page, scrolls to let product tiles render,
collects product links, then opens up to 8 product pages and records Target's own schema.org
offer data and the visible current/regular price text. Read-only; no cart.
"""
import json
import re
from urllib import robotparser

CLEARANCE = "https://www.target.com/c/clearance/-/N-5q0ga"
PRODUCT = re.compile(r"https://www\.target\.com/p/[^\"'?#\s]+/-/A-\d+")
REG = re.compile(r"(?:reg|regular|was|comp\. value)\s*\$\s?([0-9][0-9,]*\.\d{2})", re.I)
SALE = re.compile(r"(?:clearance|sale|current price)?\s*\$\s?([0-9][0-9,]*\.\d{2})", re.I)


def main():
    from playwright.sync_api import sync_playwright
    rp = robotparser.RobotFileParser("https://www.target.com/robots.txt")
    rp.read()
    out = {"robots": {u: rp.can_fetch("*", u) for u in (CLEARANCE, "https://www.target.com/p/x/-/A-1")}}
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context(locale="en-US", viewport={"width": 1366, "height": 900})
        page = ctx.new_page()
        resp = page.goto(CLEARANCE, wait_until="domcontentloaded", timeout=60000)
        for _ in range(8):
            page.mouse.wheel(0, 2500)
            page.wait_for_timeout(1500)
        html = page.content()
        links = []
        for m in PRODUCT.findall(html.replace("\\u002F", "/")):
            if m not in links:
                links.append(m)
        rel = sorted(set(re.findall(r'href="(/p/[^"?#]+/-/A-\d+)', html)))
        links += ["https://www.target.com" + r for r in rel if "https://www.target.com" + r not in links]
        out["clearance"] = {"http": resp.status if resp else None, "title": page.title(), "product_links": len(links), "sample": links[:8]}
        out["products"] = []
        for url in links[:8]:
            if not rp.can_fetch("*", url):
                out["products"].append({"url": url, "skipped": "robots.txt"}); continue
            pp = ctx.new_page()
            try:
                r = pp.goto(url, wait_until="domcontentloaded", timeout=45000)
                pp.wait_for_timeout(5000)
                ld = pp.eval_on_selector_all('script[type="application/ld+json"]', "els => els.map(e => e.textContent)")
                offers = []
                for raw in ld:
                    try:
                        data = json.loads(raw)
                    except ValueError:
                        continue
                    for node in (data if isinstance(data, list) else data.get("@graph", [data])):
                        if isinstance(node, dict) and node.get("@type") == "Product":
                            o = node.get("offers") or {}
                            o = o[0] if isinstance(o, list) and o else o
                            offers.append({"name": node.get("name"), "sku": node.get("sku"), "price": o.get("price"),
                                           "availability": str(o.get("availability", "")).rsplit("/", 1)[-1],
                                           "seller": (o.get("seller") or {}).get("name") if isinstance(o.get("seller"), dict) else o.get("seller")})
                text = pp.inner_text("body")[:60000]
                price_block = pp.eval_on_selector_all('[data-test="product-price"], [data-test="product-regular-price"], [data-test="product-savings"]',
                                                      "els => els.map(e => e.getAttribute('data-test') + ': ' + e.innerText)")
                out["products"].append({"url": url, "http": r.status if r else None, "title": pp.title()[:120], "offers": offers,
                                        "price_block": price_block, "reg_text": REG.findall(text)[:5],
                                        "shipping_or_stock": [s for s in ("Ship it", "Out of stock", "Sold out", "Not available", "Add to cart") if s.lower() in text.lower()]})
            except Exception as exc:
                out["products"].append({"url": url, "error": str(exc)[:200]})
            pp.close()
        b.close()
    print(json.dumps(out, indent=1))
    json.dump(out, open("probe-target.json", "w"), indent=2)


if __name__ == "__main__":
    main()

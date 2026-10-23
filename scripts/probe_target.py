#!/usr/bin/env python3
"""Diagnostic: does Target's own product page give reliable price, original price and availability?

1. Collect target.com/p/... product links from Target's home-clearance pages and from public
   deal posts that cite Target clearance (links only; their prices are never used as evidence).
2. Open each Target product page (robots.txt permitting) like a normal visitor and record what
   TARGET shows: the price data the page itself loads (its own JSON responses), the visible
   "reg $X" text, and stock/shipping signals. Read-only: no cart, no login.
"""
import json
import re
from urllib import robotparser

SOURCES = [
    "https://www.target.com/c/home-clearance/-/N-5tg3v",
    "https://www.target.com/c/home-decor/clearance/-/N-5xtubZ5tdv1",
    "https://www.livingrichwithcoupons.com/2026/10/hot-up-to-70-off-home-clearance-at-target-in-store-or-online.html",
    "https://allthingstarget.com/target-clearance-oct-2026/",
    "https://hip2save.com/deals/target-clearance/",
    "https://www.dealnews.com/Target-Home-Clearance-Deals-Up-to-70-off-free-shipping-w-35/22242730.html",
]
PRODUCT = re.compile(r"https?://(?:www\.)?target\.com/p/[A-Za-z0-9\-%]+/-/A-(\d{6,9})")
REL_PRODUCT = re.compile(r'href="(/p/[A-Za-z0-9\-%]+/-/A-(\d{6,9}))')
MAX_PRODUCTS = 25


def walk_prices(obj, found):
    """Collect Target price/availability fields from the page's own JSON responses."""
    if isinstance(obj, dict):
        if "current_retail" in obj or "reg_retail" in obj or "formatted_current_price" in obj:
            found.append({k: obj.get(k) for k in ("current_retail", "reg_retail", "formatted_current_price",
                                                   "formatted_comparison_price", "formatted_current_price_type",
                                                   "formatted_comparison_price_type", "save_percent", "save_dollar") if k in obj})
        for k in ("availability_status", "is_out_of_stock_in_all_store_locations", "shipping_options"):
            if k in obj and not isinstance(obj[k], (dict, list)):
                found.append({k: obj[k]})
        for v in obj.values():
            walk_prices(v, found)
    elif isinstance(obj, list):
        for v in obj:
            walk_prices(v, found)


def main():
    from playwright.sync_api import sync_playwright
    rp = robotparser.RobotFileParser("https://www.target.com/robots.txt")
    rp.read()
    out = {"sources": [], "products": []}
    links = {}
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context(locale="en-US", timezone_id="America/Chicago", viewport={"width": 1366, "height": 900})
        for src in SOURCES:
            page = ctx.new_page()
            row = {"url": src}
            try:
                r = page.goto(src, wait_until="domcontentloaded", timeout=45000)
                for _ in range(6):
                    page.mouse.wheel(0, 2500)
                    page.wait_for_timeout(1200)
                html = page.content().replace("\\u002F", "/").replace("\\/", "/")
                found = {m.group(1): m.group(0) for m in PRODUCT.finditer(html)}
                found.update({t: "https://www.target.com" + path for path, t in REL_PRODUCT.findall(html)})
                row.update(http=r.status if r else None, title=page.title()[:100], target_links=len(found))
                for tcin, url in found.items():
                    links.setdefault(tcin, {"url": url.split("#")[0], "found_on": src})
            except Exception as exc:
                row["error"] = str(exc)[:160]
            page.close()
            out["sources"].append(row)
            print(json.dumps(row))
        for tcin, info in list(links.items())[:MAX_PRODUCTS]:
            url = info["url"]
            if not rp.can_fetch("*", url):
                out["products"].append({"tcin": tcin, "url": url, "skipped": "robots.txt"}); continue
            page = ctx.new_page()
            captured = []
            page.on("response", lambda resp, cap=captured: cap.append(resp) if ("redsky" in resp.url and resp.request.resource_type in ("fetch", "xhr")) else None)
            row = {"tcin": tcin, "url": url, "found_on": info["found_on"]}
            try:
                r = page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(6000)
                prices = []
                for resp in captured:
                    try:
                        walk_prices(resp.json(), prices)
                    except Exception:
                        pass
                text = page.inner_text("body")[:80000]
                visible = page.eval_on_selector_all('[data-test="product-price"], [data-test="product-regular-price"], [data-test="product-savings"], [data-test="fulfillment-cell-shipping"], [data-test="shippingButton"]',
                                                    "els => els.map(e => e.getAttribute('data-test') + ': ' + e.innerText.replace(/\\s+/g,' ').trim())")
                row.update(http=r.status if r else None, title=page.title()[:140],
                           page_price_data=prices[:6], visible=visible[:8],
                           reg_text=re.findall(r"[Rr]eg\s*\$\s?[0-9][0-9,]*\.\d{2}", text)[:3],
                           clearance_text=re.findall(r"[Cc]learance\s*\$?\s?[0-9][0-9,]*\.\d{2}", text)[:3],
                           signals=[s for s in ("Out of stock", "Sold out", "Not available", "Add to cart", "Ship it", "Shipping", "In stock") if s.lower() in text.lower()])
            except Exception as exc:
                row["error"] = str(exc)[:200]
            page.close()
            out["products"].append(row)
            print(json.dumps(row))
        b.close()
    json.dump(out, open("probe-target.json", "w"), indent=2)
    usable = [p for p in out["products"] if p.get("page_price_data") or p.get("reg_text")]
    print(f"Target product pages checked: {len(out['products'])}; with Target price data: {len(usable)}")


if __name__ == "__main__":
    main()

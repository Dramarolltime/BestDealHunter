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

REF = re.compile(r"(?:Orig\.?|Reg\.?|Regular|Was|List(?: Price)?|Comp\. Value|Typical price)\s*:?\s*\$\s?([0-9][0-9,]*\.?[0-9]{0,2})", re.I)
# Store-specific page elements: (current price, reference price, reference label, availability, buy button
# [, the page's own "-NN%" savings badge, which must agree with the discount computed from the two prices]).
# Comma-separated alternatives are tried in the order written; the first match wins.
VISIBLE = {
    "amazon.com": ("#corePriceDisplay_desktop_feature_div .priceToPay .a-offscreen, "
                   "#corePrice_feature_div .a-price:not(.a-text-price) .a-offscreen",
                   "#corePriceDisplay_desktop_feature_div .basisPrice .a-offscreen, #corePrice_feature_div .a-text-price .a-offscreen",
                   "#corePriceDisplay_desktop_feature_div .basisPrice", "#availability", "#add-to-cart-button",
                   "#corePriceDisplay_desktop_feature_div .savingsPercentage"),
    "bestbuy.com": ('[data-testid="customer-price"] span, .priceView-customer-price span',
                    '[data-testid="regular-price"], .pricing-price__regular-price', '[data-testid="regular-price"]',
                    '.fulfillment-fulfillment-summary', 'button.add-to-cart-button:not([disabled]), button[data-button-state="ADD_TO_CART"]'),
    "macys.com": ('[data-auto="sale-price"], .lowest-sale-price', '[data-auto="original-price"], .price-strike',
                  '[data-auto="original-price"]', '[data-auto="availability"]', 'button[data-auto="add-to-bag"]'),
}
NOW = re.compile(r"(?:Now|Sale|Your Price|Price)\s*:?\s*\$\s?([0-9][0-9,]*\.?[0-9]{0,2})", re.I)


BLOCK_TITLE = re.compile(r"access denied|robot or human|are you a human|captcha|blocked|unusual traffic|pardon our interruption", re.I)


def _num(value):
    try:
        return round(float(str(value).replace(",", "").replace("$", "")), 2)
    except (TypeError, ValueError):
        return None


MIN_DISCOUNT = 50
BUYABLE = ("instock", "limitedavailability", "onlineonly")


def page_facts(row):
    """Price, reference price and stock exactly as the retailer's product page shows them.

    Current price: the retailer's schema.org offer if it has one, else the page's own price
    element (store-specific selectors in VISIBLE). Reference price: the page's own labelled
    reference ("Was", "Reg.", "List Price", "Comp. Value", "Typical price"). Availability: the
    schema.org offer, else the page's stock text / buy button. Returns None for anything absent.
    """
    offers = [o for o in row.get("offers") or [] if _num(o.get("price")) is not None]
    vis = row.get("visible") or {}
    price = _num(offers[0]["price"]) if offers else _num(vis.get("price"))
    if vis:  # the product's own reference element only; never another number elsewhere on the page
        reference = _num(vis.get("reference"))
    else:    # schema.org stores: a labelled "Was/Reg./..." amount in the text, only if it is unambiguous
        refs = {r for r in (_num(m) for m in row.get("reference_values") or []) if r and price and r > price}
        reference = refs.pop() if len(refs) == 1 else None
    if reference is not None and (price is None or reference <= price):
        reference = None  # the "reference" element held the price itself: not a reference
    stock = (offers[0].get("availability") or "").lower() if offers else ""
    text = (vis.get("availability") or "").lower()
    signals = row.get("signals") or {}
    if stock:
        buyable = stock in BUYABLE
    elif any(w in text for w in ("out of stock", "unavailable", "sold out")) or signals.get("sold out") or signals.get("out of stock"):
        buyable = False
    else:
        buyable = "in stock" in text or bool(vis.get("buy_button"))
    pct = (round(reference * 100) - round(price * 100)) * 100 // round(reference * 100) if price and reference else None
    return {"price": price, "reference": reference, "reference_label": vis.get("reference_label"),
            "discount": pct, "page_savings_percent": vis.get("savings_percent"),
            "exact_discount": (reference - price) * 100 / reference if price and reference else None,
            "savings_expected": bool(vis.get("savings_expected")),
            "buyable": buyable, "seller": (offers[0].get("seller") if offers else vis.get("seller"))}


def verdict(row):
    """Classify a check. Only "verified" may be used as evidence; everything else is not.

    verified    HTTP 200, no block page, and the retailer's product page itself shows a current
                price, a higher reference price giving >= 50% off (floored), and that it can be
                bought now. If a candidate claimed a price, the page must show that price.
    blocked     the retailer refused the automated check (403/429, block/captcha page,
                protocol error); says NOTHING about the deal.
    mismatch    the page price differs from the claimed price, or the page's own savings badge
                ("-53%") disagrees with the discount computed from its price and reference.
    below_50    the page shows the item, but under 50% off its own reference price.
    unavailable the page says it can't be bought now.
    unconfirmed the page loaded but showed no price or no reference price.
    """
    if "error" in row or row.get("http") in (403, 429) or BLOCK_TITLE.search(row.get("title") or ""):
        return "blocked"
    if row.get("http") != 200:
        return "unconfirmed"
    facts = page_facts(row)
    row["page"] = facts
    if facts["price"] is None or facts["reference"] is None:
        return "unconfirmed"
    claimed = _num(row.get("claimed_price"))
    if claimed is not None and claimed != facts["price"]:
        return "mismatch"
    badge = facts["page_savings_percent"]
    if badge is None and facts["savings_expected"]:
        return "unconfirmed"  # the page shows no savings badge to confirm which reference it compares against
    exact = facts["exact_discount"]
    if badge is not None and badge not in (int(exact), int(exact + 0.5)):  # floor or rounded, nothing else
        return "mismatch"  # we read a different price or reference than the page's own savings badge uses
    if not facts["buyable"]:
        return "unavailable"
    if facts["discount"] < MIN_DISCOUNT:
        return "below_50"
    return "verified"


# Amazon pages are never opened: the Associates IP License excludes "data mining, robots, or similar data
# gathering and extraction tools", and Amazon prices may only come from Creators API / PA API (Program Policies).
AMAZON_HOSTS = re.compile(r"(^|\.)(amazon\.[a-z.]+|link\.amazon|amzn\.to|a\.co)$", re.I)


def is_amazon(url):
    host = (url.split("/")[2] if url.count("/") >= 2 else "").split(":")[0]
    return bool(AMAZON_HOSTS.search(host))


def cleared_hosts():
    """Retailers whose terms were reviewed and allow automated page reading (deal_rules.json)."""
    from pathlib import Path
    rules = json.loads((Path(__file__).resolve().parent / "deal_rules.json").read_text(encoding="utf-8"))
    return [h.lower() for h in rules.get("page_check_allowed_hosts", [])]


def page_reading_allowed(url, hosts=None):
    host = (url.split("/")[2] if url.count("/") >= 2 else "").split(":")[0].lower().removeprefix("www.")
    hosts = cleared_hosts() if hosts is None else hosts
    return any(host == h or host.endswith("." + h) for h in hosts)


def items(path):
    data = json.load(open(path, encoding="utf-8"))
    data = data if isinstance(data, list) else data.get("deals", [])
    for d in data:
        if isinstance(d, dict) and str(d.get("url", "")).startswith("https://"):
            if is_amazon(d["url"]) or d.get("store") == "Amazon":
                print(f"skipped (Amazon: API only, no page reading): {d['url']}")
                continue
            if not page_reading_allowed(d["url"]):
                print(f"skipped (retailer terms not cleared for automated page reading): {d['url']}")
                continue
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


def visible(page, url):
    """The page's own price/reference/stock elements for stores whose schema.org data lacks them."""
    host = re.sub(r"^www\.", "", page.url.split("/")[2])  # after redirects (link.amazon -> amazon.com)
    sel = VISIBLE.get(host)
    if not sel:
        return {}

    def first(css):
        for alternative in css.split(", "):
            el = page.query_selector(alternative)
            text = el.inner_text().strip()[:200] if el else ""
            if text:
                return text
        return None

    price, ref, label, avail, buy, *savings = sel
    out = {"price": first(price), "reference": first(ref), "reference_label": first(label), "availability": first(avail),
           "buy_button": page.query_selector(buy) is not None}
    badge = first(savings[0]) if savings else None
    m = re.search(r"(\d{1,2})\s*%", badge or "")
    out["savings_percent"] = int(m.group(1)) if m else None
    out["savings_expected"] = bool(savings)
    if out["reference"]:
        m = re.search(r"\$\s?([0-9][0-9,]*\.?[0-9]{0,2})", out["reference"])
        out["reference"] = m.group(1) if m else None
    if out["price"]:
        m = re.search(r"\$\s?([0-9][0-9,]*\.?[0-9]{0,2})", out["price"])
        out["price"] = m.group(1) if m else None
    if out["reference_label"]:
        out["reference_label"] = re.sub(r"\s*:?\s*\$.*", "", out["reference_label"], flags=re.S).strip()[:40] or None
    return out


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
        refs = list(REF.finditer(text))
        row["reference_text"] = sorted({m.group(0).strip() for m in refs})[:8]
        row["reference_values"] = sorted({m.group(1) for m in refs})[:8]
        row["visible"] = visible(page, url)
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
                row["verdict"] = verdict(row)
                rows.append(row)
                print(json.dumps(row, ensure_ascii=False))
        browser.close()
    json.dump(rows, open("verify-results.json", "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    counts = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    print("Verdicts: " + json.dumps(counts) + "  (only 'verified' counts as evidence)")


if __name__ == "__main__":
    main(sys.argv[1:] or ["deal-review-queue.json", "deals.json"])

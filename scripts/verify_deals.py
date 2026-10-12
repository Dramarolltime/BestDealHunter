#!/usr/bin/env python3
"""Verify deal candidates and published deals against official retailer data, then (only when
enabled) publish, re-verify or archive them. This is the step the pipeline was missing: discovery
filled the queues, but nothing could confirm a price, so every deal aged out.

Sources (one adapter each; a source without credentials is skipped and reported):
  * Best Buy Products API (api.bestbuy.com) -- needs the BEST_BUY_API_KEY secret.
    Price = salePrice, reference = regularPrice (Best Buy's own regular price, NOT the
    "comparable value" shown on some pages), availability = onlineAvailability + orderable.
Stores without an adapter (Macy's, Amazon, ...) are reported as needing manual verification.

Rules (never relaxed here):
  * verified_date is set ONLY from a successful retailer response fetched in this run.
  * A deal is published or kept active only if it is available online, passes strict
    validate() (>= 50% off, evidence, allowed store/domain) today.
  * A published deal that the retailer no longer shows at >= 50% off, or as available, is
    archived (status "archived", never deleted) with the reason recorded.
  * A deal a person archived by hand is never reactivated by this script.

Modes:
  python scripts/verify_deals.py            # dry run: report only, no files change
  python scripts/verify_deals.py --apply    # write deals.json + queues (workflow gates this)
"""
import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from urllib import error, parse, request

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_deals import RULES, deal_status, validate, validate_feed  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FEED = ROOT / "deals.json"
QUEUES = ("pending_deals.json", "deal-review-queue.json")
REPORT = ROOT / "verification-report.json"
MAX_NEW_PER_RUN = 10
SKU_IN_URL = re.compile(r"/sku/(\d{5,9})(?:[/?#]|$)|[?&]skuId=(\d{5,9})")
ARCHIVED_BY = "verify_deals"


class SourceError(Exception):
    """The retailer source could not answer reliably: change nothing for these items."""


# ---------------------------------------------------------------- Best Buy Products API

class BestBuy:
    store = "Best Buy"
    name = "Best Buy Products API"
    API = "https://api.bestbuy.com/v1/products({query})"
    FIELDS = "sku,name,salePrice,regularPrice,onSale,onlineAvailability,orderable,priceUpdateDate,url,modelNumber"

    def __init__(self, api_key, transport=None):
        self.api_key, self.transport = api_key, transport or self._http

    def _http(self, url):
        try:
            with request.urlopen(request.Request(url, headers={"User-Agent": "BestDealHunter/1.0",
                                                               "Accept": "application/json"}), timeout=30) as resp:
                return json.loads(resp.read())
        except error.HTTPError as exc:  # never echo the URL: it carries the API key
            raise SourceError(f"{self.name} HTTP {exc.code}") from None
        except (error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise SourceError(f"{self.name} unreachable ({type(exc).__name__})") from None

    def ident(self, item):
        """('sku', '6444455') or ('modelNumber', '65S7SG') or None."""
        for field in ("sku", "bestbuy_sku"):
            if str(item.get(field) or "").isdigit():
                return "sku", str(item[field])
        match = SKU_IN_URL.search(str(item.get("url", "")))
        if match:
            return "sku", match.group(1) or match.group(2)
        model = str(item.get("model_number") or "").strip()
        if re.fullmatch(r"[A-Za-z0-9./-]{3,40}", model):
            return "modelNumber", model
        return None

    def lookup(self, idents):
        """{(field, value): product} for every identifier the API returned (batched, <=100 each)."""
        found = {}
        for field in ("sku", "modelNumber"):
            values = sorted({v for f, v in idents if f == field})
            for start in range(0, len(values), 100):
                chunk = values[start:start + 100]
                query = f"{field} in({','.join(chunk)})"
                url = self.API.format(query=parse.quote(query, safe="(),=")) + "?" + parse.urlencode(
                    {"apiKey": self.api_key, "format": "json", "show": self.FIELDS, "pageSize": 100})
                payload = self.transport(url)
                if not isinstance(payload, dict) or not isinstance(payload.get("products"), list):
                    raise SourceError(f"{self.name} returned an unexpected payload")
                for product in payload["products"]:
                    key = str(product.get(field) or "")
                    if key in chunk:
                        if (field, key) in found:  # a model number matching several SKUs is ambiguous
                            found[(field, key)] = None
                        else:
                            found[(field, key)] = product
        return found

    def evaluate(self, product, fetched_at):
        """(ok, facts, reason). facts carries the price data and the evidence text."""
        sale, regular = product.get("salePrice"), product.get("regularPrice")
        if type(sale) not in (int, float) or type(regular) not in (int, float) or not 0 < sale <= regular:
            return False, {}, f"no usable price (salePrice={sale!r}, regularPrice={regular!r})"
        orderable = product.get("orderable")
        available = product.get("onlineAvailability") is True and orderable in (None, "Available")
        facts = {
            "price": round(float(sale), 2), "original_price": round(float(regular), 2),
            "retailer_title": product.get("name"), "sku": str(product.get("sku")),
            "price_evidence": (f"{self.name}, SKU {product.get('sku')}, fetched {fetched_at}: salePrice "
                               f"${sale:.2f}, regularPrice ${regular:.2f} (Best Buy's regular price, not "
                               f"'comparable value'), onlineAvailability {product.get('onlineAvailability')}, "
                               f"orderable {orderable!r}, priceUpdateDate {product.get('priceUpdateDate')}. "
                               "Taxes, shipping and local pickup availability not included."),
            "source": f"{self.name} (official Best Buy product data)",
        }
        if not available:
            return False, facts, f"not available online (onlineAvailability={product.get('onlineAvailability')!r}, orderable={orderable!r})"
        percent = int((regular - sale) * 100 // regular)
        if sale > regular * (1 - RULES["min_discount_percent"] / 100):
            return False, facts, f"only {percent}% below Best Buy's regular price (needs {RULES['min_discount_percent']}%)"
        return True, facts, f"{percent}% off, available online"


def sources_from_env(env):
    found, missing = {}, []
    if env.get("BEST_BUY_API_KEY"):
        found["Best Buy"] = BestBuy(env["BEST_BUY_API_KEY"])
    else:
        missing.append("Best Buy (BEST_BUY_API_KEY secret not set)")
    return found, missing


# ---------------------------------------------------------------- planning (pure)

def key(url):
    p = parse.urlparse(str(url))
    return (p.hostname or "").lower().removeprefix("www.") + p.path.rstrip("/").lower()


def collect(feed, queues):
    """Everything to check: published deals first (re-verification), then queue candidates."""
    items = [("feed", None, i, d) for i, d in enumerate(feed) if isinstance(d, dict)]
    published = {key(d.get("url")) for d in feed if isinstance(d, dict)}
    for name, queue in queues.items():
        for i, c in enumerate(queue):
            if isinstance(c, dict) and c.get("status") not in ("rejected", "published") \
                    and key(c.get("url")) not in published:
                items.append(("queue", name, i, c))
    return items


def plan(feed, queues, sources, today, fetched_at):
    """Decide every change without touching files. Returns (actions, report_rows)."""
    items = collect(feed, queues)
    by_source = {}
    rows = []
    for item in items:
        origin, qname, index, data = item
        store = data.get("store")
        source = sources.get(store)
        base = {"origin": origin if origin == "feed" else qname, "store": store, "title": data.get("title"), "url": data.get("url")}
        if not source:
            rows.append({**base, "outcome": "manual", "reason": f"no automated source configured for {store}; verify by hand"})
            continue
        ident = source.ident(data)
        if not ident:
            rows.append({**base, "outcome": "manual", "reason": "no SKU or model number to look up; add `sku`"})
            continue
        by_source.setdefault(store, []).append((item, ident))

    actions = []
    new_count = 0
    for store, entries in by_source.items():
        source = sources[store]
        try:
            products = source.lookup([ident for _, ident in entries])
        except SourceError as exc:
            for (origin, qname, _, data), _ in entries:
                rows.append({"origin": origin if origin == "feed" else qname, "store": store, "title": data.get("title"),
                             "url": data.get("url"), "outcome": "error", "reason": f"{exc}; nothing changed"})
            continue
        for (origin, qname, index, data), ident in entries:
            row = {"origin": origin if origin == "feed" else qname, "store": store, "title": data.get("title"), "url": data.get("url")}
            product = products.get(ident)
            if product is None:
                reason = "ambiguous model number" if ident in products else f"{ident[0]} {ident[1]} not found"
                if origin == "feed" and deal_status(data, today) == "active":
                    actions.append(("archive", index, f"retailer re-check {today}: {reason}"))
                    row.update(outcome="archive", reason=reason)
                else:
                    actions.append(("queue-review", (qname, index) if origin == "queue" else None, "not_verified", reason))
                    row.update(outcome="not_verified", reason=reason)
                rows.append(row)
                continue
            ok, facts, reason = source.evaluate(product, fetched_at)
            row["reason"] = reason
            if origin == "feed":
                hand_archived = data.get("status") == "archived" and data.get("archived_by") != ARCHIVED_BY
                if ok and not hand_archived:
                    updated = {**data, **{k: facts[k] for k in ("price", "original_price", "price_evidence", "source")},
                               "verified_date": today.isoformat(), "sku": facts["sku"]}
                    if data.get("archived_by") == ARCHIVED_BY:
                        updated["status"] = "active"
                        updated.pop("archived_reason", None)
                        updated.pop("archived_by", None)
                    try:
                        validate([updated], today=today)
                    except ValueError as exc:
                        row.update(outcome="not_verified", reason=f"{reason}, but fails validation: {exc}")
                        rows.append(row)
                        continue
                    actions.append(("update", index, updated))
                    row["outcome"] = "reverified"
                elif not ok and deal_status(data, today) == "active":
                    actions.append(("archive", index, f"retailer re-check {today}: {reason}"))
                    row["outcome"] = "archive"
                else:
                    row["outcome"] = "unchanged" if hand_archived else "not_verified"
            else:
                if not ok:
                    actions.append(("queue-review", (qname, index), "rejected" if facts else "not_verified", reason))
                    row["outcome"] = "rejected"
                elif new_count >= MAX_NEW_PER_RUN:
                    row.update(outcome="deferred", reason=f"{reason}; run limit of {MAX_NEW_PER_RUN} new deals")
                else:
                    deal = new_deal(data, facts, today)
                    try:
                        validate([deal], today=today)
                    except ValueError as exc:
                        actions.append(("queue-review", (qname, index), "not_verified", f"fails validation: {exc}"))
                        row.update(outcome="not_verified", reason=f"fails validation: {exc}")
                        rows.append(row)
                        continue
                    actions.append(("publish", (qname, index), deal))
                    row["outcome"] = "publish"
                    new_count += 1
            rows.append(row)
    return actions, rows


def new_deal(candidate, facts, today):
    deal = {
        "store": candidate["store"],
        "title": candidate.get("title") or facts.get("retailer_title"),
        "category": candidate.get("category") if candidate.get("category") in RULES["categories"] else "Electronics",
        "url": candidate["url"],
        "price": facts["price"],
        "original_price": facts["original_price"],
        "verified_date": today.isoformat(),
        "source": facts["source"],
        "price_evidence": facts["price_evidence"],
        "sku": facts["sku"],
    }
    for field in ("variant", "promo_note"):
        if candidate.get(field):
            deal[field] = candidate[field]
    return deal


def apply(actions, feed, queues, today):
    """Apply planned actions to in-memory copies; validate_feed must still pass."""
    feed = [dict(d) for d in feed]
    queues = {n: [dict(c) for c in q] for n, q in queues.items()}
    stamp = today.isoformat()
    for action in actions:
        kind = action[0]
        if kind == "update":
            feed[action[1]] = action[2]
        elif kind == "archive":
            feed[action[1]].update(status="archived", archived_by=ARCHIVED_BY, archived_reason=action[2])
        elif kind == "publish":
            (qname, index), deal = action[1], action[2]
            feed.append(dict(deal, published_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")))
            queues[qname][index].update(status="published", last_review={"date": stamp, "result": "verified_published",
                                                                       "reason": deal["price_evidence"]})
        elif kind == "queue-review" and action[1]:
            qname, index = action[1]
            queues[qname][index]["last_review"] = {"date": stamp, "result": action[2], "reason": action[3]}
            if action[2] == "rejected":
                queues[qname][index]["status"] = "rejected"
    validate_feed(feed, today)
    return feed, queues


def render(rows, sources_missing, applied):
    counts = {}
    for r in rows:
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
    out = ["## Retailer verification", "",
           f"Mode: **{'APPLIED to deals.json' if applied else 'dry run (no files changed)'}**",
           "Outcomes: " + (", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "nothing to check"), ""]
    if sources_missing:
        out += ["Sources not configured: " + "; ".join(sources_missing), ""]
    out += ["| Outcome | Store | Item | Reason |", "|---|---|---|---|"]
    out += [f"| {r['outcome']} | {r['store']} | {str(r['title'])[:70]} | {str(r.get('reason', '')).replace('|', '/')[:160]} |" for r in rows]
    return "\n".join(out)


def load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def main(argv=None, env=None, sources=None, today=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="write deals.json and queue updates")
    args = parser.parse_args(argv)
    env = os.environ if env is None else env
    today = today or datetime.now(timezone.utc).date()
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    missing = []
    if sources is None:
        sources, missing = sources_from_env(env)
    feed = load(FEED, [])
    queues = {name: load(ROOT / name, []) for name in QUEUES}
    actions, rows = plan(feed, queues, sources, today, fetched_at)
    applied = False
    if args.apply and actions:
        new_feed, new_queues = apply(actions, feed, queues, today)
        FEED.write_text(json.dumps(new_feed, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        for name, queue in new_queues.items():
            (ROOT / name).write_text(json.dumps(queue, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        applied = True
    report = render(rows, missing, applied)
    print(report)
    REPORT.write_text(json.dumps({"applied": applied, "sources_missing": missing, "rows": rows}, indent=2) + "\n",
                      encoding="utf-8")
    summary = env.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(report + "\n")
    return 1 if any(r["outcome"] == "error" for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())

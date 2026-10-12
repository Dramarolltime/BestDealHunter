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
from publication_gate import card_id, publication_problems  # noqa: E402

try:  # branded-card renderer from PR #5; until it is merged, deals needing a new card are held
    from deal_graphics import CardError, render_card, save as save_card  # noqa: E402
except ImportError:  # pragma: no cover - depends on which PRs are merged
    render_card = save_card = None

    class CardError(ValueError):
        pass

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
            # Cards default to "Comparable value" for Best Buy; this reference is the regular price.
            "reference_price_label": "Best Buy regular price",
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

CARD_DIR = "images/generated"
PRICE_FIELDS = ("price", "original_price")


def key(url):
    p = parse.urlparse(str(url))
    return (p.hostname or "").lower().removeprefix("www.") + p.path.rstrip("/").lower()


def card_path(deal):
    return f"{CARD_DIR}/deal-{card_id(deal['url'])}-square.jpg"


class Renderer:
    """Renders a deal's branded cards (square for the site, 4:5 feed for Instagram) into `root`.
    available is False until PR #5's deal_graphics is merged; then new or changed deals get cards."""

    def __init__(self, root=ROOT, render=None, save=None):
        self.root = root
        self.render, self.save = render or render_card, save or save_card
        self.available = bool(self.render and self.save)

    def write(self, deal):
        for kind in ("square", "feed"):
            path = self.root / CARD_DIR / f"deal-{card_id(deal['url'])}-{kind}.jpg"
            self.save(self.render(deal, kind), path)
        return card_path(deal)


def collect(feed, queues):
    """Everything to check: published deals first (re-verification), then queue candidates."""
    items = [("feed", None, i, d) for i, d in enumerate(feed) if isinstance(d, dict)]
    published = {key(d.get("url")) for d in feed if isinstance(d, dict)}
    published_skus = {str(d["sku"]) for d in feed if isinstance(d, dict) and d.get("sku")}
    for name, queue in queues.items():
        for i, c in enumerate(queue):
            if not isinstance(c, dict) or c.get("status") in ("rejected", "published"):
                continue
            if key(c.get("url")) in published or (c.get("sku") and str(c["sku"]) in published_skus):
                continue
            items.append(("queue", name, i, c))
    return items


def plan(feed, queues, sources, today, fetched_at, renderer=None, root=ROOT):
    """Decide every change without touching files. Returns (actions, report_rows).

    Publication safety (publication_gate.py) is enforced here AND again in apply():
    a candidate with unresolved blockers, an explicit hold, or no image is never published,
    even when the retailer confirms the price. A deal whose price changed is re-published
    only with a freshly rendered card; otherwise it is archived, because its card would
    show a price that is no longer true.
    """
    renderer = renderer or Renderer(root)
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
                rows.append(plan_published(data, index, ok, facts, reason, row, actions, today, renderer, root))
                continue
            if not ok:
                actions.append(("queue-review", (qname, index), "rejected" if facts else "not_verified", reason))
                row["outcome"] = "rejected"
                rows.append(row)
                continue
            deal = new_deal(data, facts, today, by_model=ident[0] == "modelNumber")
            try:
                validate([deal], today=today)
            except ValueError as exc:
                actions.append(("queue-review", (qname, index), "not_verified", f"fails validation: {exc}"))
                row.update(outcome="not_verified", reason=f"fails validation: {exc}")
                rows.append(row)
                continue
            if renderer.available:
                deal["image"] = card_path(deal)
            problems = publication_problems(deal, data, root, card_pending=renderer.available)
            if problems:
                # Retailer data checked out, but a person still owes a decision (or an image is missing).
                actions.append(("queue-review", (qname, index), "verified_but_held",
                                f"{reason}; held: " + "; ".join(problems), facts))
                row.update(outcome="held", reason=f"{reason}; held: " + "; ".join(problems))
            elif new_count >= MAX_NEW_PER_RUN:
                row.update(outcome="deferred", reason=f"{reason}; run limit of {MAX_NEW_PER_RUN} new deals")
            else:
                actions.append(("publish", (qname, index), deal, renderer.available))
                row["outcome"] = "publish"
                new_count += 1
            rows.append(row)
    return actions, rows


def plan_published(data, index, ok, facts, reason, row, actions, today, renderer, root):
    hand_archived = data.get("status") == "archived" and data.get("archived_by") != ARCHIVED_BY
    if hand_archived:
        row["outcome"] = "unchanged"
        return row
    if not ok:
        if deal_status(data, today) == "active":
            actions.append(("archive", index, f"retailer re-check {today}: {reason}"))
            row["outcome"] = "archive"
        else:
            row["outcome"] = "not_verified"
        return row
    updated = {**data, **{k: facts[k] for k in ("price", "original_price", "price_evidence", "source", "reference_price_label")},
               "verified_date": today.isoformat(), "sku": facts["sku"]}
    if data.get("archived_by") == ARCHIVED_BY:
        updated["status"] = "active"
        updated.pop("archived_reason", None)
        updated.pop("archived_by", None)
    price_changed = any(round(float(data.get(f) or 0), 2) != updated[f] for f in PRICE_FIELDS)
    rerender = renderer.available
    if rerender:
        updated["image"] = card_path(updated)  # cards print price and verified date: always refresh
    elif price_changed:
        # The current image shows the old price and no card can be rendered: stop showing it as active.
        if deal_status(data, today) == "active":
            actions.append(("archive", index, f"retailer re-check {today}: price changed to ${updated['price']:.2f} "
                                              f"(was ${float(data['price']):.2f}); a new card is needed before re-publishing"))
            row.update(outcome="archive", reason=f"{reason}, but the price changed and no card renderer is available")
        else:
            row.update(outcome="held", reason=f"{reason}, but the price changed and no card renderer is available")
        return row
    try:
        validate([updated], today=today)
    except ValueError as exc:
        row.update(outcome="not_verified", reason=f"{reason}, but fails validation: {exc}")
        return row
    problems = publication_problems(updated, None, root, card_pending=rerender)
    if problems:
        if deal_status(data, today) == "active":
            actions.append(("archive", index, f"retailer re-check {today}: " + "; ".join(problems)))
            row.update(outcome="archive", reason=f"{reason}, but " + "; ".join(problems))
        else:
            row.update(outcome="held", reason=f"{reason}, but " + "; ".join(problems))
        return row
    actions.append(("update", index, updated, rerender))
    row["outcome"] = "reverified"
    return row


def canonical_url(store, sku):
    return f"https://www.bestbuy.com/site/{sku}.p?skuId={sku}" if store == "Best Buy" else None


def new_deal(candidate, facts, today, by_model=False):
    # A model-number lookup may resolve to a different listing than the researched URL, so the
    # link must point at exactly the SKU whose price was verified.
    url = canonical_url(candidate["store"], facts["sku"]) if by_model else candidate["url"]
    deal = {
        "store": candidate["store"],
        "title": candidate.get("title") or facts.get("retailer_title"),
        "category": candidate.get("category") if candidate.get("category") in RULES["categories"] else "Electronics",
        "url": url,
        "price": facts["price"],
        "original_price": facts["original_price"],
        "reference_price_label": facts["reference_price_label"],
        "verified_date": today.isoformat(),
        "source": facts["source"],
        "price_evidence": facts["price_evidence"],
        "sku": facts["sku"],
    }
    for field in ("variant", "promo_note"):
        if candidate.get(field):
            deal[field] = candidate[field]
    return deal


def apply(actions, feed, queues, today, renderer=None, root=ROOT):
    """Apply planned actions to copies, rendering cards where planned. The publication gate runs
    again on the final records (files now exist), and validate_feed must still pass."""
    renderer = renderer or Renderer(root)
    feed = [dict(d) for d in feed]
    queues = {n: [dict(c) for c in q] for n, q in queues.items()}
    stamp = today.isoformat()
    for action in actions:
        kind = action[0]
        if kind == "update":
            _, index, updated, rerender = action
            if rerender:
                try:
                    renderer.write(updated)
                except (CardError, OSError) as exc:
                    feed[index].setdefault("verification_note", f"{stamp}: card render failed ({exc}); not re-verified")
                    continue
            if publication_problems(updated, None, root):
                continue
            feed[index] = updated
        elif kind == "archive":
            feed[action[1]].update(status="archived", archived_by=ARCHIVED_BY, archived_reason=action[2])
        elif kind == "publish":
            (qname, index), deal, rerender = action[1], action[2], action[3]
            candidate = queues[qname][index]
            if rerender:
                try:
                    deal = dict(deal, image=renderer.write(deal))
                except (CardError, OSError) as exc:
                    candidate["last_review"] = {"date": stamp, "result": "verified_but_held",
                                                "reason": f"card render failed: {exc}"}
                    continue
            problems = publication_problems(deal, candidate, root)
            if problems:  # defence in depth: never publish past the gate
                candidate["last_review"] = {"date": stamp, "result": "verified_but_held", "reason": "; ".join(problems)}
                continue
            feed.append(dict(deal, published_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")))
            candidate.update(status="published", last_review={"date": stamp, "result": "verified_published",
                                                               "reason": deal["price_evidence"]})
        elif kind == "queue-review" and action[1]:
            qname, index = action[1]
            review = {"date": stamp, "result": action[2], "reason": action[3]}
            if len(action) > 4:  # verified price facts for a held candidate, so a reviewer sees them
                review["retailer_check"] = {k: action[4][k] for k in ("price", "original_price", "price_evidence")}
            queues[qname][index]["last_review"] = review
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
    renderer = Renderer(ROOT)
    if not renderer.available:
        missing.append("card renderer (deal_graphics from PR #5): new or re-priced deals are held")
    actions, rows = plan(feed, queues, sources, today, fetched_at, renderer)
    applied = False
    if args.apply and actions:
        new_feed, new_queues = apply(actions, feed, queues, today, renderer)
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

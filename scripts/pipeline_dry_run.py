#!/usr/bin/env python3
"""End-to-end BestDealHunter pipeline dry run: every stage, no side effects.

Stages: feed validation → freshness → discovery configuration → branded cards →
Cloudinary hosting plan → Instagram (Buffer) post plan. Each stage reports ok / warn /
fail and continues even if an earlier stage failed, so one run shows every problem.
Exit code is 1 if any stage fails.

No network calls, uploads, commits or posts are made. Secrets are never read; discovery
sources are reported only as configured / not configured via *_CONFIGURED flags.
"""
import json
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

from validate_deals import RULES, deal_status, validate, validate_feed

ROOT = Path(__file__).resolve().parents[1]
OK, WARN, FAIL = "ok", "warn", "fail"


def stage(name, status, summary, details=()):
    return {"name": name, "status": status, "summary": summary, "details": list(details)}


def load(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def active_deals(deals, today):
    return [d for d in deals if isinstance(d, dict) and deal_status(d, today) == "active"]


def check_feed(deals, today):
    try:
        active, archived = validate_feed(deals, today)
    except ValueError as exc:
        return stage("Feed validation", FAIL, f"deals.json is invalid: {exc}")
    return stage("Feed validation", OK, f"{active} active, {archived} archived (Past Deals)")


def check_freshness(deals, today):
    active = active_deals(deals, today)
    if not active:
        return stage("Freshness", WARN, "No active deals: the site shows only Past Deals")
    limit = RULES["max_verification_age_days"]
    soon = [d for d in active if date.fromisoformat(d["verified_date"]) + timedelta(days=limit) <= today]
    details = [f"{d['store']}: {d['title']} (verified {d['verified_date']})" for d in soon]
    if soon:
        return stage("Freshness", WARN, f"{len(soon)} of {len(active)} active deal(s) move to Past Deals within 24h", details)
    return stage("Freshness", OK, f"{len(active)} active deal(s) verified within {limit} days")


def check_discovery(env):
    sources = {
        "DEAL_FEED_URL feed": env.get("DEAL_FEED_CONFIGURED") == "true",
        "eBay Browse API": env.get("EBAY_CONFIGURED") == "true",
        "Impact catalogs": env.get("IMPACT_CONFIGURED") == "true",
    }
    details = [f"{name}: {'configured' if on else 'not configured'}" for name, on in sources.items()]
    if not any(sources.values()):
        return stage("Discovery", WARN, "No discovery source configured; no new candidates can be found", details)
    return stage("Discovery", OK, f"{sum(sources.values())} source(s) configured", details)


def check_cards(deals, today):
    from deal_graphics import CardError, card_facts, card_id, render_card
    problems, warnings, checked = [], [], 0
    with tempfile.TemporaryDirectory() as tmp:
        for deal in active_deals(deals, today):
            title = deal.get("title", "Untitled")
            try:
                facts = card_facts(deal)
                validate([deal], today)
            except (CardError, ValueError) as exc:
                problems.append(f"{title}: {exc}")
                continue
            price, original = float(deal["price"]), float(deal["original_price"])
            if facts["percent"] != int((original - price) * 100 // original):
                problems.append(f"{title}: card % does not match deal data")
            image = str(deal.get("image") or "")
            expected = f"images/generated/deal-{card_id(deal)}-square.jpg"
            if image != expected:
                warnings.append(f"{title}: site image is not its branded card ({image or 'none'})")
            else:
                fresh = Path(tmp) / "card.jpg"
                render_card(deal, "square").save(fresh, "JPEG", quality=92, optimize=True, progressive=True)
                committed = ROOT / image
                if not committed.is_file() or committed.read_bytes() != fresh.read_bytes():
                    problems.append(f"{title}: committed card is out of date with the deal data; regenerate it")
            feed = ROOT / f"images/generated/deal-{card_id(deal)}-feed.jpg"
            if image == expected and not feed.is_file():
                problems.append(f"{title}: missing 4:5 feed card for Instagram")
            checked += 1
    if problems:
        return stage("Branded cards", FAIL, f"{len(problems)} card problem(s)", problems + warnings)
    if warnings:
        return stage("Branded cards", WARN, f"{checked} active deal(s) checked", warnings)
    return stage("Branded cards", OK, f"{checked} active deal card(s) match their deal data byte-for-byte")


def check_hosting(deals):
    from cloudinary_upload import UploadError, load_mapping, sync
    try:
        mapping = load_mapping()
    except (UploadError, ValueError) as exc:
        return stage("Cloudinary hosting", FAIL, f"cloudinary-images.json invalid: {exc}")
    bad = [m.get("image", "?") for m in mapping
           if not str(m.get("cloudinary_url", "")).startswith("https://res.cloudinary.com/")]
    pending = []
    _, failed = sync(deals, list(mapping), ("", "", ""), dry_run=True, log=pending.append)
    if bad or failed:
        return stage("Cloudinary hosting", FAIL, "invalid mapping entries or unusable image paths",
                     [f"bad URL for {b}" for b in bad] + pending)
    if pending:
        return stage("Cloudinary hosting", WARN,
                     f"{len(pending)} image(s) to upload on the next scheduled refresh", pending)
    return stage("Cloudinary hosting", OK, f"all {len(mapping)} recorded image(s) hosted; nothing pending")


def check_publishing(deals, today, env):
    from publish_buffer import POSTS, plan
    mapping = load(ROOT / "cloudinary-images.json", [])
    posts = load(POSTS, [])
    ready, skipped = plan(deals, mapping, posts, today)
    by_url = {d.get("url"): d for d in deals if isinstance(d, dict)}
    violations = []
    for post in ready:
        deal = by_url.get(post["deal_url"], {})
        if deal.get("store") == "Amazon":
            violations.append(f"{post['title']}: Amazon deal planned for posting")
        if deal_status(deal, today) != "active":
            violations.append(f"{post['title']}: archived deal planned for posting")
        if deal.get("publication_approved") is not True:
            violations.append(f"{post['title']}: unapproved deal planned for posting")
        if "#ad" not in post["text"]:
            violations.append(f"{post['title']}: caption missing #ad disclosure")
    switch = "ON" if env.get("INSTAGRAM_PUBLISH_ENABLED") == "true" else "OFF"
    details = [f"Kill switch INSTAGRAM_PUBLISH_ENABLED: {switch}"] + \
              [f"Would post: {p['title']}" for p in ready] + [f"Not eligible: {t}: {r}" for t, r in skipped]
    if violations:
        return stage("Instagram plan (Buffer, dry run)", FAIL, "publishing safety rule violated", violations + details)
    return stage("Instagram plan (Buffer, dry run)", OK,
                 f"{len(ready)} post(s) would be queued; {len(skipped)} deal(s) not eligible; nothing sent", details)


def run(today=None, env=None, root_deals=None):
    today = today or date.today()
    env = os.environ if env is None else env
    deals = root_deals if root_deals is not None else load(ROOT / "deals.json", [])
    results = [check_feed(deals, today), check_freshness(deals, today), check_discovery(env)]
    for name, check in (("Branded cards", lambda: check_cards(deals, today)),
                        ("Cloudinary hosting", lambda: check_hosting(deals)),
                        ("Instagram plan (Buffer, dry run)", lambda: check_publishing(deals, today, env))):
        try:
            results.append(check())
        except Exception as exc:  # a crashing stage is reported, not hidden
            results.append(stage(name, FAIL, f"stage crashed: {type(exc).__name__}: {exc}"))
    return results


def report(results):
    icon = {OK: "✅", WARN: "⚠️", FAIL: "❌"}
    lines = ["## BestDealHunter pipeline dry run", "", "No network calls, uploads, commits or posts were made.", "",
             "| Stage | Result | Summary |", "|---|---|---|"]
    lines += [f"| {r['name']} | {icon[r['status']]} {r['status']} | {r['summary']} |" for r in results]
    for r in results:
        if r["details"]:
            lines += ["", f"<details><summary>{r['name']}</summary>", ""] + [f"- {d}" for d in r["details"]] + ["", "</details>"]
    return "\n".join(lines)


def main():
    results = run()
    text = report(results)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    return 1 if any(r["status"] == FAIL for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())

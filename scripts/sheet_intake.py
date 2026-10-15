#!/usr/bin/env python3
"""Publish deals Alpha adds to the BestDealHunter Google Sheet (owner + Alpha, 2026-10-15).

Alpha finds a deal, adds a row to the Sheet, makes the deal image and saves it (and a screenshot of the store
page) to Google Drive. Each hourly run reads the Sheet's published CSV (File > Share > Publish to web > CSV; the
link goes in the GitHub variable DEALS_SHEET_CSV_URL, no password needed) and puts every new "Ready" row through
exactly the same checks as the "Submit a deal" form (submit_deal.accept): a listed store other than Amazon, the
store's own product link, >= 50% off and under 80%, in stock, sold by the store, checked within the last 3 hours,
a screenshot as price evidence, no duplicates. Accepted rows join deals.json with Alpha's image (if its numbers
were confirmed); the rest of the run makes the card, hosts it, lists it on the site and posts it to Instagram.

Results go to sheet-intake.json (one entry per row, accepted or not, with the reason), so a row is processed
once; to retry, Alpha adds a new row. Without DEALS_SHEET_CSV_URL this does nothing.

  DEALS_SHEET_CSV_URL=... python scripts/sheet_intake.py [--apply]
"""
import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from add_verified_deals import Rejected
from links import direct_url
from submit_deal import EVIDENCE, HOLD_AT_PERCENT, LABELS, ROOT, accept, download_image, money

FEED = ROOT / "deals.json"
LEDGER = ROOT / "sheet-intake.json"
MAX_AGE = timedelta(hours=3)
PACIFIC = ZoneInfo("America/Los_Angeles")
SHEET_CSV = re.compile(r"https://docs\.google\.com/spreadsheets/d/(e/)?[\w-]+/(pub|export)\?[\w=&%-]*")
SHEET_LINK = re.compile(r"https://docs\.google\.com/spreadsheets/d/([\w-]{20,})(?:/[^?#]*)?(?:\?[^#]*)?(?:#gid=(\d+))?")
DRIVE_ID = re.compile(r"https://(?:drive|docs)\.google\.com/(?:file/d/|open\?id=|uc\?(?:export=\w+&)?id=)([\w-]{20,})")
READY = {"ready", "verified", "yes"}
YES = {"yes", "y", "true", "x", "✅", "checked"}
# Column names Alpha may use -> the field they fill (matched without case, spaces or punctuation).
COLUMNS = {
    "store": ("store", "retailer"),
    "title": ("product", "productname", "title"),
    "url": ("producturl", "dealurl", "url", "link", "productlink"),
    "category": ("category",),
    "price": ("saleprice", "price", "now"),
    "original": ("wasprice", "originalprice", "regularprice", "listprice", "was"),
    "label": ("waslabel", "referencelabel", "pricelabel"),
    "in_stock": ("instock", "instockonline"),
    "sold_by": ("soldbystore", "soldby", "soldbyretailer"),
    "checked_at": ("checkedat", "verifiedat", "seenat", "timechecked"),
    "screenshot": ("screenshot", "screenshotlink", "proof"),
    "image": ("alphaimage", "image", "imagelink", "drivelink", "imagedrivelink"),
    "image_ok": ("imagenumbersmatch", "imagecheck", "numbersmatch"),
    "status": ("status", "verificationstatus"),
}


def norm(name):
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def rows(csv_text):
    """CSV text -> list of {field: value} using COLUMNS, with the sheet row number in '_row'."""
    reader = csv.reader(io.StringIO(csv_text))
    header = next(reader, [])
    index = {}
    for i, name in enumerate(header):
        for field, names in COLUMNS.items():
            if norm(name) in names and field not in index:
                index[field] = i
    out = []
    for n, values in enumerate(reader, start=2):
        row = {field: (values[i].strip() if i < len(values) else "") for field, i in index.items()}
        row["_row"] = n
        out.append(row)
    return out


def csv_url(link):
    """The Sheet's CSV export URL from a published CSV link or an ordinary share link (".../edit#gid=0").
    A share link works when the Sheet is shared "Anyone with the link: Viewer"; None for anything else."""
    link = str(link or "").strip()
    if SHEET_CSV.fullmatch(link):
        return link
    m = SHEET_LINK.fullmatch(link)
    if not m:
        return None
    return f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv" + (f"&gid={m.group(2)}" if m.group(2) else "")


def drive_download_url(link):
    """A Google Drive share link -> its direct download URL; None for anything that isn't a Drive file link."""
    m = DRIVE_ID.match(str(link or "").strip())
    return f"https://drive.google.com/uc?export=download&id={m.group(1)}" if m else None


def checked_time(text):
    """'2026-10-15T09:30:00-07:00', '2026-10-15 09:30' or '10/15/2026 9:30:00' (no zone = Pacific)."""
    text = str(text or "").strip()
    for fmt in (None, "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M"):
        try:
            t = datetime.fromisoformat(text) if fmt is None else datetime.strptime(text, fmt)
        except ValueError:
            continue
        return t if t.tzinfo else t.replace(tzinfo=PACIFIC)
    raise Rejected(f"Checked At must be a date and time like 2026-10-15 09:30 (got {text!r})")


def key(row):
    raw = "|".join(row.get(f, "") for f in ("url", "price", "original", "checked_at"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def record(row, now):
    """The add_verified_deals record for a sheet row (same rules as the form), and its screenshot link."""
    store = row.get("store", "")
    if store == "Amazon":
        raise Rejected("Amazon deals can't be posted: Amazon's Associates rules allow Amazon prices only from "
                       "Amazon's own API")
    if norm(row.get("in_stock")) not in YES or norm(row.get("sold_by")) not in YES:
        raise Rejected("In Stock and Sold By Store must both be Yes")
    when = checked_time(row.get("checked_at"))
    if when > now + timedelta(minutes=5):
        raise Rejected("Checked At is in the future")
    if now - when > MAX_AGE:
        raise Rejected("the price was checked more than 3 hours ago; check it again and add a new row")
    url = direct_url(row.get("url", ""))
    if not url or not url.startswith("https://"):
        raise Rejected("use the store's own https product link, not an affiliate or redirect link")
    price, original = money(row.get("price"), "Sale Price"), money(row.get("original"), "Was Price")
    if original > 0 and price < original and int((original - price) * 100 // original) >= HOLD_AT_PERCENT:
        raise Rejected(f"{HOLD_AT_PERCENT}% off or more is held: double-check the was-price is the store's own "
                       "crossed-out price")
    shot = drive_download_url(row.get("screenshot"))
    if not shot:
        raise Rejected("add the Google Drive link of a screenshot showing the sale price and the was price")
    return {
        "store": store, "title": row.get("title", ""), "url": url,
        "category": row.get("category") or "Other", "seller": store,
        "price": price, "original_price": original,
        "reference_label": LABELS.get(row.get("label") or "Was", "Was"),
        "availability": "In stock online (seen on the store's page)",
        "evidence": {"method": "human_browser_check", "checked_at": when.astimezone(timezone.utc).isoformat(),
                     "checked_by": f"Alpha via the BestDealHunter deal sheet, row {row['_row']}", "source": None},
    }, shot


def fetchers(k, opener=urllib.request.urlopen):
    """Download the screenshot / Alpha's image from Drive into evidence/ (images only, at most 10 MB)."""
    return (lambda url, _ref: download_image(url, EVIDENCE / f"deal-sheet-{k}", "screenshot", opener),
            lambda url, _ref: download_image(url, EVIDENCE / f"deal-sheet-{k}-card", "Alpha's image", opener))


def run(csv_text, deals, ledger, now, opener=urllib.request.urlopen, log=print):
    """Process new Ready rows. Returns the new deals.json entries; appends one ledger entry per processed row."""
    done = {e.get("key") for e in ledger if isinstance(e, dict)}
    new = []
    for row in rows(csv_text):
        if norm(row.get("status")) not in READY:
            continue
        k = key(row)
        if k in done:
            continue
        result = {"key": k, "row": row["_row"], "product": row.get("title", ""), "at": now.isoformat()}
        try:
            rec, shot = record(row, now)
            fetch, fetch_card = fetchers(k, opener)
            entry, note = accept(rec, shot, drive_download_url(row.get("image")), norm(row.get("image_ok")) in YES,
                                 k, f"owner deal form: Alpha deal sheet row {row['_row']}", deals + new, now,
                                 fetch, fetch_card)
        except (Rejected, ValueError, OSError, urllib.error.URLError) as exc:
            result.update(result="rejected", reason=str(exc))
            log(f"❌ Row {row['_row']} ({row.get('title', '')}): not published: {exc}")
        else:
            new.append(entry)
            result.update(result="accepted", reason=note.strip() or "Standard card.")
            log(f"✅ Row {row['_row']}: {entry['store']} · {entry['title']} · ${entry['price']:.2f}.{note}")
        ledger.append(result)
        done.add(k)
    return new


def main(argv=None, env=os.environ, now=None, opener=urllib.request.urlopen):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="write deals.json and sheet-intake.json")
    args = parser.parse_args(argv)
    url = env.get("DEALS_SHEET_CSV_URL", "").strip()
    if not url:
        print("DEALS_SHEET_CSV_URL not set: Alpha's deal sheet is not connected yet.")
        return 0
    url = csv_url(url)
    if not url:
        print("::error::DEALS_SHEET_CSV_URL must be the Google Sheet's link (docs.google.com/spreadsheets/...).")
        return 1
    try:
        with opener(urllib.request.Request(url, headers={"User-Agent": "BestDealHunter sheet intake"}),
                    timeout=30) as resp:
            text = resp.read(5_000_000).decode("utf-8-sig")
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"Could not read the deal sheet ({type(exc).__name__}); is it shared 'Anyone with the link: Viewer'? "
              "Trying again next run.")
        return 0
    if text.lstrip().startswith("<"):  # Google's sign-in page instead of the CSV
        print("Could not read the deal sheet: Google asked for a sign-in. Share it 'Anyone with the link: Viewer'.")
        return 0
    header = next(csv.reader(io.StringIO(text)), [])
    missing = [f for f in ("store", "title", "url", "price", "original", "checked_at", "screenshot", "status")
               if not any(norm(h) in COLUMNS[f] for h in header)]
    print(f"Deal sheet columns: {', '.join(header)}" + (f" · MISSING: {', '.join(missing)}" if missing else ""))
    deals = json.loads(FEED.read_text(encoding="utf-8"))
    ledger = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else []
    new = run(text, deals, ledger, now or datetime.now(timezone.utc), opener)
    print(f"Deal sheet: {len(new)} new deal(s) accepted.")
    if args.apply:
        if new:
            FEED.write_text(json.dumps(deals + new, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        LEDGER.write_text(json.dumps(ledger, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

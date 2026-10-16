#!/usr/bin/env python3
"""Alpha's AI deal images: Google Drive -> Cloudinary -> Google Sheet (issue #59).

Alpha (ChatGPT) cannot send image bytes to GitHub or Cloudinary. It (or the owner, from a phone) saves the image
in the shared Drive folder and puts the file's link in the deal's `AI Image Drive URL` cell. Every 15 minutes the
"Alpha images" workflow runs this script:

  1. read the Deal Queue tab (DEALS_SHEET_CSV_URL, the same link the deal intake uses; no Google password);
  2. for each row with a Deal ID and an `AI Image Drive URL`, download that one Drive file (Drive file links only);
  3. check it: a real JPEG or PNG (magic bytes and decoded by Pillow), at most 10 MB, at least 600 px on its short
     side, not a copy of an image already used for another deal;
  4. upload it to Cloudinary with the repository's existing CLOUDINARY_* secrets (signed, overwrite=false), public
     ID bestdealhunter/alpha/<deal-id>-<hash> (TEST- deals go to bestdealhunter/alpha-test/), and check the public
     URL answers with an image;
  5. record the result in alpha-images.json (published on GitHub). The Sheet's Apps Script (docs/ALPHA_IMAGES.md)
     reads that file and writes the URL into the row's `AI Image Cloudinary URL` cell, only if that cell is empty.

AI images are never price evidence: the retailer screenshot stays in `Screenshot Drive URL`, is never uploaded
here, and every deal still needs it (sheet_intake.py). A row whose image fails a check is recorded with the reason
and not retried; to fix it, add the corrected image as a new row.

  DEALS_SHEET_CSV_URL=... CLOUDINARY_*=... python scripts/alpha_images.py [--apply] [--sheet URL]
"""
import argparse
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from cloudinary_upload import UploadError, http, multipart, sign
from sheet_intake import csv_url, drive_download_url, norm, rows

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "alpha-images.json"
MAX_BYTES = 10 * 1024 * 1024
MIN_SIDE = 600
TYPES = {b"\xff\xd8\xff": ("image/jpeg", "jpg"), b"\x89PNG\r\n\x1a\n": ("image/png", "png")}
DEAL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{1,60}")


class ImageRejected(Exception):
    pass


def slug(deal_id):
    return re.sub(r"[^a-z0-9-]+", "-", deal_id.lower()).strip("-")[:48]


def is_test(deal_id):
    return deal_id.upper().startswith("TEST-")


def check_image(data):
    """(mime, ext, width, height) for a real JPEG/PNG within limits, else ImageRejected."""
    if len(data) > MAX_BYTES:
        raise ImageRejected("image is larger than 10 MB")
    kind = next((t for magic, t in TYPES.items() if data.startswith(magic)), None)
    if not kind:
        raise ImageRejected("not a JPEG or PNG image (a Drive share page or another file type was downloaded)")
    from PIL import Image
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
            fmt = img.format
    except Exception as exc:  # truncated or corrupt
        raise ImageRejected(f"image could not be decoded ({type(exc).__name__})")
    if fmt not in ("JPEG", "PNG") or (fmt == "JPEG") != (kind[0] == "image/jpeg"):
        raise ImageRejected("image content does not match its type")
    if min(width, height) < MIN_SIDE:
        raise ImageRejected(f"image is {width}x{height}; at least {MIN_SIDE} px on the short side is needed")
    return kind[0], kind[1], width, height


def download(url, opener=urllib.request.urlopen):
    req = urllib.request.Request(url, headers={"User-Agent": "BestDealHunter alpha images"})
    with opener(req, timeout=60) as resp:
        return resp.read(MAX_BYTES + 1)


def upload(data, mime, ext, public_id, creds, transport=http, now=time.time):
    """Signed Cloudinary upload of these bytes; returns the verified public HTTPS URL."""
    cloud, key, secret = creds
    params = {"public_id": public_id, "overwrite": "false", "timestamp": str(int(now()))}
    fields = {**params, "api_key": key, "signature": sign(params, secret)}
    body, ctype = multipart(fields, f"alpha.{ext}", mime, data)
    status, _, raw = transport("POST", f"https://api.cloudinary.com/v1_1/{cloud}/image/upload", body,
                               {"Content-Type": ctype})
    try:
        result = json.loads(raw or b"{}")
    except ValueError:
        result = {}
    if status != 200:
        message = (result.get("error") or {}).get("message", "") if isinstance(result, dict) else ""
        raise UploadError(f"Cloudinary HTTP {status}{': ' + message if message else ''}")
    url = result.get("secure_url", "")
    if not url.startswith(f"https://res.cloudinary.com/{cloud}/image/upload/") or f"/{public_id}." not in url:
        raise UploadError("unexpected Cloudinary URL")
    status, headers, _ = transport("HEAD", url, None, {"User-Agent": "BestDealHunter/1.0"})
    content_type = next((v for k, v in headers.items() if k.lower() == "content-type"), "")
    if status != 200 or not content_type.startswith("image/"):
        raise UploadError(f"hosted image not publicly reachable (HTTP {status})")
    return url


def run(csv_text, ledger, creds, opener=urllib.request.urlopen, transport=http, now=None, log=print):
    """Upload every new row's AI image. Appends one ledger entry per processed row; returns the new entries."""
    now = now or datetime.now(timezone.utc)
    done = {(e.get("deal_id"), e.get("drive_file_id")) for e in ledger}
    by_hash = {e.get("sha256"): e.get("deal_id") for e in ledger if e.get("result") == "uploaded"}
    new = []
    for row in rows(csv_text):
        deal_id, link = row.get("deal_id", "").strip(), row.get("image", "").strip()
        if not deal_id or not link:
            continue
        source = drive_download_url(link)
        file_id = source.rsplit("id=", 1)[-1] if source else link
        if (deal_id, file_id) in done:
            continue
        entry = {"deal_id": deal_id, "row": row["_row"], "drive_file_id": file_id, "test": is_test(deal_id),
                 "at": now.isoformat(timespec="seconds")}
        try:
            if not DEAL_ID.fullmatch(deal_id):
                raise ImageRejected("Deal ID must be letters, numbers, - _ or . only")
            if not source:
                raise ImageRejected("AI Image Drive URL must be a Google Drive file link")
            data = download(source, opener)
            mime, ext, width, height = check_image(data)
            digest = hashlib.sha256(data).hexdigest()
            if by_hash.get(digest, deal_id) != deal_id:
                raise ImageRejected(f"the same image is already used for deal {by_hash[digest]}")
            folder = "bestdealhunter/alpha-test" if entry["test"] else "bestdealhunter/alpha"
            url = upload(data, mime, ext, f"{folder}/{slug(deal_id)}-{digest[:10]}", creds, transport)
        except (ImageRejected, UploadError, urllib.error.URLError, TimeoutError, OSError) as exc:
            entry.update(result="rejected", reason=str(exc)[:200])
            log(f"❌ Row {row['_row']} ({deal_id}): image not used: {entry['reason']}")
        else:
            entry.update(result="uploaded", cloudinary_url=url, sha256=digest, width=width, height=height,
                         bytes=len(data), mime=mime)
            by_hash[digest] = deal_id
            log(f"✅ Row {row['_row']} ({deal_id}): {url}")
        ledger.append(entry)
        done.add((deal_id, file_id))
        new.append(entry)
    return new


def image_for(deal_id, drive_link, ledger):
    """The verified Cloudinary URL recorded for this deal's Drive image, or None."""
    source = drive_download_url(drive_link) or ""
    file_id = source.rsplit("id=", 1)[-1] if source else None
    return next((e["cloudinary_url"] for e in reversed(ledger) if e.get("result") == "uploaded"
                 and e.get("deal_id") == deal_id and (file_id is None or e.get("drive_file_id") == file_id)), None)


def main(argv=None, env=os.environ, opener=urllib.request.urlopen):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="write alpha-images.json")
    parser.add_argument("--sheet", help="Sheet link to read instead of DEALS_SHEET_CSV_URL (e.g. a test Sheet)")
    args = parser.parse_args(argv)
    url = csv_url(args.sheet or env.get("DEALS_SHEET_CSV_URL", ""))
    if not url:
        print("No Google Sheet link (DEALS_SHEET_CSV_URL): nothing to do.")
        return 0
    creds = tuple(env.get(k, "") for k in ("CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET"))
    if not all(creds):
        print("::error::Cloudinary secrets are missing; images are not uploaded.")
        return 1
    try:
        with opener(urllib.request.Request(url, headers={"User-Agent": "BestDealHunter alpha images"}),
                    timeout=30) as resp:
            text = resp.read(5_000_000).decode("utf-8-sig")
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"Could not read the Sheet ({type(exc).__name__}); trying again next run.")
        return 0
    if text.lstrip().startswith("<"):
        print("Could not read the Sheet: Google asked for a sign-in. Share it 'Anyone with the link: Viewer'.")
        return 0
    ledger = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else []
    new = run(text, ledger, creds, opener)
    print(f"Alpha images: {sum(e['result'] == 'uploaded' for e in new)} uploaded, "
          f"{sum(e['result'] == 'rejected' for e in new)} rejected.")
    if args.apply and new:
        LEDGER.write_text(json.dumps(ledger, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

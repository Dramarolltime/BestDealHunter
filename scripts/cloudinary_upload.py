#!/usr/bin/env python3
"""The single Cloudinary uploader for BestDealHunter deal images.

* Uploads local images under images/ (including images/generated/ branded cards).
* public_id is a content hash, so identical bytes never create a second asset and
  a changed image always gets a new URL (no stale cache from overwrite=false).
* Verifies every returned URL is public (HTTP 200, image/*) before recording it.
* MERGES results into cloudinary-images.json: existing entries are kept, so a
  failed or skipped upload never removes a URL the website already uses.
* Skips images whose recorded sha256 already matches (no wasted free-tier credits).

Credentials come only from CLOUDINARY_CLOUD_NAME / CLOUDINARY_API_KEY /
CLOUDINARY_API_SECRET and are never printed. Nothing is posted to social media.

Usage:
  python scripts/cloudinary_upload.py                # sync every deals.json image
  python scripts/cloudinary_upload.py --dry-run      # list what would upload; no network
  python scripts/cloudinary_upload.py --image images/generated/x.jpg   # one file, prints URL
"""
import argparse
import hashlib
import json
import mimetypes
import os
import re
import sys
import time
from pathlib import Path
from urllib import error, parse, request
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / "images"
MAPPING = ROOT / "cloudinary-images.json"
ALLOWED = re.compile(r"images/[\w./-]+\.(?:jpg|jpeg|png|webp)", re.I)
MAX_BYTES = 10 * 1024 * 1024
FOLDER = "bestdealhunter"


class UploadError(Exception):
    pass


def local_image(relative):
    """Resolve a repo-relative image path, refusing anything outside images/."""
    if not isinstance(relative, str) or not ALLOWED.fullmatch(relative) or ".." in relative:
        raise UploadError(f"not an allowed image path: {relative!r}")
    path = (ROOT / relative).resolve()
    if not path.is_relative_to(IMAGES.resolve()) or not path.is_file():
        raise UploadError(f"image not found: {relative}")
    if path.stat().st_size > MAX_BYTES:
        raise UploadError(f"image over 10 MB: {relative}")
    return path


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sign(params, secret):
    payload = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hashlib.sha1((payload + secret).encode()).hexdigest()


def multipart(fields, filename, mime, data):
    boundary = "----bdh" + uuid4().hex
    body = b"".join(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
        for k, v in fields.items())
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
             f"Content-Type: {mime}\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def http(method, url, body=None, headers=None, timeout=60):
    """Default transport: returns (status, headers_dict, body_bytes)."""
    req = request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read() if method != "HEAD" else b""
    except error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read() if method != "HEAD" else b""


def upload(path, creds, transport=http, now=time.time):
    cloud, key, secret = creds
    digest = sha256(path)
    params = {"public_id": f"{FOLDER}/{digest[:24]}", "overwrite": "false", "timestamp": str(int(now()))}
    fields = {**params, "api_key": key, "signature": sign(params, secret)}
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    body, ctype = multipart(fields, path.name, mime, path.read_bytes())
    status, _, raw = transport("POST", f"https://api.cloudinary.com/v1_1/{parse.quote(cloud, safe='')}/image/upload",
                               body, {"Content-Type": ctype})
    try:
        data = json.loads(raw or b"{}")
    except ValueError:
        data = {}
    if status != 200:
        message = (data.get("error") or {}).get("message", "") if isinstance(data, dict) else ""
        raise UploadError(f"Cloudinary HTTP {status}{': ' + message if message else ''}")
    url = data.get("secure_url", "")
    if not url.startswith(f"https://res.cloudinary.com/{cloud}/image/upload/"):
        raise UploadError("unexpected Cloudinary URL")
    status, headers, _ = transport("HEAD", url, None, {"User-Agent": "BestDealHunter/1.0"})
    content_type = next((v for k, v in headers.items() if k.lower() == "content-type"), "")
    if status != 200 or not content_type.startswith("image/"):
        raise UploadError(f"hosted image not publicly reachable (HTTP {status})")
    return url, digest


SQUARE_CARD = re.compile(r"(images/generated/deal-[0-9a-f]{12})-square\.jpg")


def deal_images(deals):
    """Each deal's site image, plus the 4:5 feed card next to a square card (used for Instagram)."""
    seen = []
    for deal in deals:
        image = deal.get("image") if isinstance(deal, dict) else None
        if not (isinstance(image, str) and image.startswith("images/")):
            continue
        candidates = [image]
        match = SQUARE_CARD.fullmatch(image)
        if match and (ROOT / f"{match.group(1)}-feed.jpg").is_file():
            candidates.append(f"{match.group(1)}-feed.jpg")
        seen.extend(c for c in candidates if c not in seen)
    return seen


def load_mapping():
    try:
        data = json.loads(MAPPING.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    if not isinstance(data, list):
        raise UploadError("cloudinary-images.json must be a list")
    return data


def merge(mapping, image, title, url, digest):
    entry = {"title": title, "image": image, "cloudinary_url": url, "sha256": digest}
    for i, old in enumerate(mapping):
        if isinstance(old, dict) and old.get("image") == image:
            mapping[i] = entry
            return mapping
    mapping.append(entry)
    return mapping


def needs_upload(mapping, image, digest):
    for entry in mapping:
        if isinstance(entry, dict) and entry.get("image") == image:
            # Legacy entries (no sha256) keep their working URL; re-upload only on a content change.
            return entry.get("sha256") not in (None, digest) or not entry.get("cloudinary_url")
    return True


def sync(deals, mapping, creds, transport=http, dry_run=False, log=print):
    titles = {}
    for d in deals:
        if isinstance(d, dict) and isinstance(d.get("image"), str):
            titles[d["image"]] = d.get("title")
            titles[d["image"].replace("-square.jpg", "-feed.jpg")] = d.get("title")
    uploaded, failed = 0, 0
    for image in deal_images(deals):
        try:
            path = local_image(image)
            digest = sha256(path)
            if not needs_upload(mapping, image, digest):
                continue
            if dry_run:
                log(f"Would upload: {image}")
                continue
            url, digest = upload(path, creds, transport)
            merge(mapping, image, titles.get(image), url, digest)
            uploaded += 1
            log(f"Uploaded: {image} -> {url}")
        except (UploadError, OSError, error.URLError) as exc:
            failed += 1
            log(f"Upload failed for {image}: {exc}")
    return uploaded, failed


def credentials():
    creds = tuple(os.environ.get(k, "") for k in ("CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET"))
    if not all(creds):
        raise SystemExit("Cloudinary credentials missing (set the three CLOUDINARY_* secrets).")
    return creds


def main(argv=None):
    parser = argparse.ArgumentParser(description="Upload deal images to Cloudinary")
    parser.add_argument("--image", help="upload one repo image and print its URL (mapping unchanged)")
    parser.add_argument("--dry-run", action="store_true", help="list pending uploads; no credentials or network")
    args = parser.parse_args(argv)

    if args.image:
        url, _ = upload(local_image(args.image), credentials())
        print(url)
        return 0

    deals = json.loads((ROOT / "deals.json").read_text(encoding="utf-8"))
    mapping = load_mapping()
    creds = ("", "", "") if args.dry_run else credentials()
    uploaded, failed = sync(deals, mapping, creds, dry_run=args.dry_run)
    if uploaded:
        MAPPING.write_text(json.dumps(mapping, indent=2) + "\n", encoding="utf-8")
    print(f"Uploaded {uploaded}, failed {failed}; {len(mapping)} hosted image(s) recorded. No social posts made.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

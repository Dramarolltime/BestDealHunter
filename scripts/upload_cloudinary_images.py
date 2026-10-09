#!/usr/bin/env python3
"""Upload existing, owner-approved deal images to Cloudinary.

No synthetic product imagery or social publishing. Local file paths only.
Prints public image URLs for use in the website/social review workflow.
"""
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import time
from urllib import request, parse, error
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = ROOT / "images"
def upload(path: Path, cloud: str, key: str, secret: str):
    timestamp = str(int(time.time()))
    public_id = "bestdealhunter/" + path.stem.lower().replace(" ", "-")
    params = {"timestamp": timestamp, "public_id": public_id, "overwrite": "false"}
    signature = hashlib.sha1(("&".join(f"{k}={v}" for k, v in sorted(params.items())) + secret).encode()).hexdigest()
    boundary = "----bdh" + uuid4().hex
    fields = {**params, "api_key": key, "signature": signature}
    body = b""
    for k, v in fields.items():
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    body += f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{path.name}"\r\nContent-Type: {mime}\r\n\r\n'.encode()
    body += path.read_bytes() + b"\r\n" + f"--{boundary}--\r\n".encode()
    req = request.Request(
        f"https://api.cloudinary.com/v1_1/{parse.quote(cloud, safe='')}/image/upload",
        data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST"
    )
    with request.urlopen(req, timeout=60) as response:
        data = json.load(response)
    url = data.get("secure_url", "")
    if not url.startswith(f"https://res.cloudinary.com/{cloud}/image/upload/"):
        raise RuntimeError("Unexpected Cloudinary image URL")
    return url

def main():
    cloud, key, secret = (os.getenv(x, "") for x in ("CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET"))
    if not all((cloud, key, secret)):
        raise SystemExit("Cloudinary credentials missing")
    deals = json.loads((ROOT / "deals.json").read_text())
    results = []
    for deal in deals:
        image = deal.get("image", "")
        # Only existing, owner-provided images; never publish the illustrative Delsey drawing.
        if not image.startswith("images/") or image.startswith("images/generated/"):
            continue
        path = (ROOT / image).resolve()
        if not path.is_relative_to(ALLOWED.resolve()) or not path.is_file() or path.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
            continue
        try:
            url = upload(path, cloud, key, secret)
            results.append({"title": deal.get("title"), "image": image, "cloudinary_url": url})
            print(f"Uploaded: {deal.get('title')} -> {url}")
        except error.HTTPError as exc:
            print(f"Upload failed for {image}: HTTP {exc.code}")
        except Exception as exc:
            print(f"Upload failed for {image}: {type(exc).__name__}")
    output = ROOT / "cloudinary-images.json"
    output.write_text(json.dumps(results, indent=2) + "\n")
    print(f"Uploaded {len(results)} owner-provided images; no Instagram posts created.")

if __name__ == "__main__":
    main()

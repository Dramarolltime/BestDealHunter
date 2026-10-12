#!/usr/bin/env python3
"""One-off, owner-approved brand test: the BestDealHunter logo -> Cloudinary -> ONE Instagram post via Buffer.

Modes (run from .github/workflows/logo-crosspost.yml, never on a schedule):
  prepare  read-only Buffer schema checks + upload the committed logo to Cloudinary and prove the
           hosted bytes are identical (sha256). Posts nothing.
  publish  at most ONE live post, ever. Before contacting Buffer it (1) refuses if the lock tag
           refs/tags/brand-logo-post/lock exists, (2) re-verifies the Cloudinary bytes, then
           (3) creates the lock tag on GitHub without force. A post id is recorded as the tag
           refs/tags/brand-logo-post/post-<id>. Any failure keeps the lock: a retry needs a person to
           confirm in Buffer that nothing was posted and delete the lock on purpose.
  status   read-only: the recorded post's Buffer status, sent time and any Instagram link fields.

Never reads INSTAGRAM_PUBLISH_ENABLED and never touches deals, posts.json or the deal publisher.
Secrets come only from the environment and are never printed.
"""
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib import error, parse, request

ROOT = Path(__file__).resolve().parents[1]
LOGO = ROOT / "images" / "brand" / "bestdealhunter-logo-1080.jpg"
BUFFER_API = "https://api.buffer.com"
LOCK = "brand-logo-post/lock"
POST_TAG = "brand-logo-post/post-"
CAPTION = ("Meet BestDealHunter: big discounts, less searching. 🔎🏷️\n\n"
           "We hunt down verified deals from trusted US retailers and only share them once the price checks out.\n\n"
           "Follow along for deals worth your time. Link in bio.\n\n#bestdealhunter #deals #savings")
# The exact image hosted by the `prepare` run (38295148828); its bytes are re-verified before posting.
HOSTED_LOGO = "https://res.cloudinary.com/j9g6uest/image/upload/v1791831850/bestdealhunter/brand/logo-9af788d4a735.jpg"
LINK_FIELDS = ("externalLink", "serviceLink", "link", "permalink", "url", "postUrl", "externalUrl")


class Stop(Exception):
    """Fail closed: nothing further is attempted."""


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def logo_bytes():
    data = LOGO.read_bytes()
    return data, sha256(data)


def public_id(digest):
    return f"bestdealhunter/brand/logo-{digest[:12]}"


# ---------------------------------------------------------------- Cloudinary

def cloudinary_upload(data, digest, env, http=None):
    cloud, key, secret = env.get("CLOUDINARY_CLOUD_NAME"), env.get("CLOUDINARY_API_KEY"), env.get("CLOUDINARY_API_SECRET")
    if not all((cloud, key, secret)):
        raise Stop("Cloudinary secrets are not configured")
    pid, ts = public_id(digest), str(int(time.time()))
    params = {"overwrite": "false", "public_id": pid, "timestamp": ts}
    signature = hashlib.sha1(("&".join(f"{k}={v}" for k, v in sorted(params.items())) + secret).encode()).hexdigest()
    boundary = "----bdh" + digest[:16]
    body = b""
    for k, v in {**params, "api_key": key, "signature": signature}.items():
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="logo.jpg"\r\n'
             f"Content-Type: image/jpeg\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = request.Request(f"https://api.cloudinary.com/v1_1/{parse.quote(cloud, safe='')}/image/upload", data=body,
                          method="POST", headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with request.urlopen(req, timeout=60) as resp:
            result = json.load(resp)
    except error.HTTPError as exc:
        raise Stop(f"Cloudinary upload HTTP {exc.code}") from None
    url = result.get("secure_url", "")
    if not url.startswith(f"https://res.cloudinary.com/{cloud}/image/upload/"):
        raise Stop("Cloudinary returned no usable secure_url")
    return {"url": url, "public_id": result.get("public_id"), "bytes": result.get("bytes"),
            "width": result.get("width"), "height": result.get("height"), "format": result.get("format"),
            "existing": result.get("existing", False)}


def fetch(url, timeout=30):
    req = request.Request(url, headers={"User-Agent": "BestDealHunter/1.0"})
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.headers.get("Content-Type", ""), resp.read(), resp.geturl()
    except error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type", "") if exc.headers else "", b"", url


def verify_hosted(url, digest):
    status, ctype, body, _ = fetch(url)
    if status != 200 or not ctype.startswith("image/jpeg"):
        raise Stop(f"hosted logo not reachable as JPEG (HTTP {status}, {ctype or 'no type'})")
    if sha256(body) != digest:
        raise Stop(f"hosted bytes differ from the committed logo (sha256 {sha256(body)[:16]} != {digest[:16]})")
    return {"http": status, "content_type": ctype, "sha256": digest, "bytes": len(body)}


def visual_diff(logo, body):
    import io
    from PIL import Image, ImageChops, ImageStat
    a = Image.open(io.BytesIO(logo)).convert("RGB").resize((64, 64))
    img = Image.open(io.BytesIO(body)).convert("RGB")
    diff = sum(ImageStat.Stat(ImageChops.difference(a, img.resize((64, 64)))).mean) / 3
    return img.size, round(diff, 2)


def compare_full_image(post_url, logo):
    """The post's full-size image via Instagram's public /media/?size=l redirect, compared visually."""
    url = post_url.rstrip("/") + "/media/?size=l"
    status, ctype, body, final = fetch(url)
    if status != 200 or not ctype.startswith("image/"):
        return {"url": url, "http": status, "content_type": ctype}
    size, diff = visual_diff(logo, body)
    return {"http": status, "content_type": ctype, "size": size, "host": final.split("/")[2],
            "mean_abs_diff_0_255": diff, "visual_match": diff < 12}


def compare_og_image(html, logo):
    """Instagram re-encodes uploads, so bytes can't match; compare the public og:image visually."""
    import html as htmlmod
    import io
    import re
    match = re.search(rb'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', html or b"")
    if not match:
        return {"og_image": None, "note": "no og:image in the public page (login wall?)"}
    url = htmlmod.unescape(match.group(1).decode())
    status, ctype, body, _ = fetch(url)
    if status != 200 or not body:
        return {"og_image": url[:120], "http": status}
    from PIL import Image, ImageChops, ImageStat
    a = Image.open(io.BytesIO(logo)).convert("RGB").resize((64, 64))
    b = Image.open(io.BytesIO(body)).convert("RGB")
    size = b.size
    b = b.resize((64, 64))
    diff = sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3
    import base64
    thumb = io.BytesIO()
    Image.open(io.BytesIO(body)).convert("RGB").resize((160, 160)).save(thumb, "JPEG", quality=80)
    host = url.split("/")[2] if "//" in url else url[:40]
    return {"og_image_host": host, "og_image_http": status, "og_image_size": size, "mean_abs_diff_0_255": round(diff, 2),
            "visual_match": diff < 12, "thumbnail_jpeg_b64": base64.b64encode(thumb.getvalue()).decode()}


# ---------------------------------------------------------------- Buffer

class Buffer:
    def __init__(self, key):
        if not key:
            raise Stop("BUFFER_API_KEY is not configured")
        self.key = key

    def call(self, query, variables=None):
        req = request.Request(BUFFER_API, data=json.dumps({"query": query, "variables": variables or {}}).encode(),
                              method="POST", headers={"Authorization": f"Bearer {self.key}",
                                                      "Content-Type": "application/json", "User-Agent": "BestDealHunter/1.0"})
        try:
            with request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
        except error.HTTPError as exc:
            raise Stop(f"Buffer HTTP {exc.code}") from None
        if data.get("errors"):
            raise Stop("Buffer error: " + "; ".join(str(e.get("message", e)) for e in data["errors"]))
        return data.get("data") or {}

    def type_fields(self, name):
        t = self.call('query($n: String!) { __type(name: $n) { kind fields { name } inputFields { name } enumValues { name } } }',
                      {"n": name}).get("__type") or {}
        return {k: [f["name"] for f in (t.get(k) or [])] for k in ("fields", "inputFields", "enumValues")}

    def post(self, post_id, link_fields):
        extra = " ".join(link_fields)
        q = f"query($i: PostInput!) {{ post(input: $i) {{ id status channelId sentAt dueAt schedulingType {extra} }} }}"
        return self.call(q, {"i": {"id": post_id}}).get("post")


def inspect(buf):
    """Read-only: confirm the shapes this script relies on and which link fields Post exposes."""
    post = buf.type_fields("Post")["fields"]
    info = {
        "CreatePostInput": buf.type_fields("CreatePostInput")["inputFields"],
        "ShareMode": buf.type_fields("ShareMode")["enumValues"],
        "SchedulingType": buf.type_fields("SchedulingType")["enumValues"],
        "PostStatus": buf.type_fields("PostStatus")["enumValues"],
        "Post fields": post,
        "link fields available": [f for f in LINK_FIELDS if f in post],
    }
    if "shareNow" not in info["ShareMode"] or "automatic" not in info["SchedulingType"]:
        raise Stop(f"Buffer schema changed: ShareMode={info['ShareMode']} SchedulingType={info['SchedulingType']}")
    return info


def create_live_post(buf, channel_id, image_url):
    q = """mutation($input: CreatePostInput!) { createPost(input: $input) {
      __typename
      ... on PostActionSuccess { post { id status channelId sentAt dueAt schedulingType } }
      ... on MutationError { message } } }"""
    payload = {"text": CAPTION, "channelId": channel_id, "schedulingType": "automatic", "mode": "shareNow",
               "assets": [{"image": {"url": image_url}}],
               "metadata": {"instagram": {"type": "post", "shouldShareToFeed": True}}}
    result = buf.call(q, {"input": payload}).get("createPost") or {}
    if result.get("__typename") != "PostActionSuccess" or not (result.get("post") or {}).get("id"):
        raise Stop(f"Buffer did not create the post ({result.get('__typename')}): {result.get('message', 'no message')}")
    return result["post"]


# ---------------------------------------------------------------- one-time lock (git tags on GitHub)

def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def remote_tags(prefix):
    out = git("ls-remote", "--tags", "origin", f"refs/tags/{prefix}*")
    if out.returncode not in (0, 2):
        raise Stop(f"could not read lock tags: {out.stderr.strip()}")
    return [line.split("refs/tags/", 1)[1] for line in out.stdout.splitlines() if "refs/tags/" in line and not line.endswith("^{}")]


def push_tag(name, message):
    made = git("-c", "user.name=github-actions[bot]", "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
               "tag", "-a", name, "-m", message, "HEAD")
    if made.returncode:
        raise Stop(f"could not create tag {name}: {made.stderr.strip()}")
    pushed = git("push", "origin", f"refs/tags/{name}:refs/tags/{name}")  # never --force
    if pushed.returncode:
        raise Stop(f"tag {name} was not created on GitHub: {pushed.stderr.strip()}")


def recorded_post_id():
    ids = [t[len(POST_TAG):] for t in remote_tags(POST_TAG)]
    if len(ids) > 1:
        raise Stop(f"more than one recorded post: {ids}")
    return ids[0] if ids else None


# ---------------------------------------------------------------- modes

def run(mode, env, log=print):
    data, digest = logo_bytes()
    log(f"Logo: {LOGO.relative_to(ROOT)} sha256 {digest} ({len(data)} bytes)")
    if mode == "prepare":
        info = inspect(Buffer(env.get("BUFFER_API_KEY")))
        log("Buffer schema: " + json.dumps(info))
        hosted = cloudinary_upload(data, digest, env)
        log("Cloudinary: " + json.dumps(hosted))
        log("Hosted bytes: " + json.dumps(verify_hosted(hosted["url"], digest)))
        log(f"Existing lock tags: {remote_tags('brand-logo-post/')}")
        return 0
    buf = Buffer(env.get("BUFFER_API_KEY"))
    links = inspect(buf)["link fields available"]
    if mode == "status":
        post_id = recorded_post_id()
        if not post_id:
            log(f"No recorded post. Lock tags: {remote_tags('brand-logo-post/')}")
            return 1
        post = buf.post(post_id, links) or {}
        log("Post: " + json.dumps(post))
        link = post.get("externalLink")
        if link:
            status, ctype, body, final = fetch(link)
            log(f"Instagram link {link}: HTTP {status}, final URL {final}, {len(body)} bytes")
            og = compare_og_image(body, data)
            og.pop("thumbnail_jpeg_b64", None)
            log("Instagram link-preview (og:image, may be cropped): " + json.dumps(og))
            log("Instagram full post image: " + json.dumps(compare_full_image(link, data)))
        return 0 if post.get("status") == "sent" else 1
    if mode != "publish":
        raise Stop(f"unknown mode {mode}")
    channel = env.get("BUFFER_CHANNEL_ID", "")
    if not channel:
        raise Stop("BUFFER_CHANNEL_ID is not configured")
    existing = remote_tags("brand-logo-post/")
    if existing:  # idempotency: never post twice, whatever happened before
        post_id = recorded_post_id()
        log(f"Refused: lock already taken ({existing}). Recorded post: {post_id or 'none'}. No Buffer post created.")
        if post_id:
            log("Post: " + json.dumps(buf.post(post_id, links)))
        return 3
    hosted = {"url": HOSTED_LOGO}  # the existing Cloudinary image; no new upload
    if digest[:12] not in HOSTED_LOGO:
        raise Stop("committed logo no longer matches the hosted image; run prepare again first")
    log("Hosted bytes verified: " + json.dumps(verify_hosted(hosted["url"], digest)))
    push_tag(LOCK, f"One-time BestDealHunter logo post; image {hosted['url']} sha256 {digest}; "
                   f"run {env.get('GITHUB_RUN_ID', 'local')}. Never delete unless Buffer shows no post was created.")
    log(f"Lock refs/tags/{LOCK} acquired; creating ONE Buffer post (shareNow).")
    post = create_live_post(buf, channel, hosted["url"])
    log("Created: " + json.dumps(post))  # logged first, so the id survives even if recording fails
    push_tag(POST_TAG + post["id"], json.dumps({"post": post, "image": hosted["url"], "sha256": digest}))
    for _ in range(12):  # Buffer sends asynchronously; report what it says, never assume
        current = buf.post(post["id"], links) or {}
        log("Status: " + json.dumps(current))
        if current.get("status") in ("sent", "error"):
            break
        time.sleep(10)
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or argv[0] not in ("prepare", "publish", "status"):
        print("usage: logo_crosspost.py prepare|publish|status")
        return 2
    try:
        return run(argv[0], os.environ)
    except Stop as exc:
        print(f"STOPPED: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

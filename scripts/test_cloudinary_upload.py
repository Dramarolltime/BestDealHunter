"""Cloudinary uploader tests with a fake transport: no network, credentials or uploads."""
import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cloudinary_upload as cu  # noqa: E402

CREDS = ("democloud", "key123", "secret456")
REAL_IMAGE = "images/IMG_4236.jpeg"


class FakeCloudinary:
    def __init__(self, upload_status=200, head_status=200, head_type="image/jpeg", url_host=None):
        self.upload_status, self.head_status, self.head_type = upload_status, head_status, head_type
        self.url_host = url_host or f"https://res.cloudinary.com/{CREDS[0]}/image/upload/"
        self.calls = []

    def __call__(self, method, url, body=None, headers=None):
        self.calls.append((method, url, body, headers))
        if method == "POST":
            if self.upload_status != 200:
                return self.upload_status, {}, json.dumps({"error": {"message": "Invalid Signature"}}).encode()
            public_id = body.split(b'name="public_id"\r\n\r\n')[1].split(b"\r\n")[0].decode()
            return 200, {}, json.dumps({"secure_url": f"{self.url_host}v1/{public_id}.jpg"}).encode()
        return self.head_status, {"Content-Type": self.head_type}, b""


class UploadTests(unittest.TestCase):
    def test_signature_matches_cloudinary_scheme(self):
        params = {"timestamp": "1", "public_id": "a/b", "overwrite": "false"}
        expected = hashlib.sha1(b"overwrite=false&public_id=a/b&timestamp=1secret").hexdigest()
        self.assertEqual(cu.sign(params, "secret"), expected)

    def test_upload_uses_content_hash_and_never_sends_secret(self):
        fake = FakeCloudinary()
        path = cu.local_image(REAL_IMAGE)
        url, digest = cu.upload(path, CREDS, fake, now=lambda: 1_700_000_000)
        self.assertEqual(digest, hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertIn(f"bestdealhunter/{digest[:24]}", url)
        body = fake.calls[0][2]
        self.assertNotIn(CREDS[2].encode(), body)
        self.assertIn(b'name="overwrite"\r\n\r\nfalse', body)
        self.assertEqual([c[0] for c in fake.calls], ["POST", "HEAD"])

    def test_same_bytes_same_public_id(self):
        a, b = FakeCloudinary(), FakeCloudinary()
        path = cu.local_image(REAL_IMAGE)
        self.assertEqual(cu.upload(path, CREDS, a)[0], cu.upload(path, CREDS, b)[0])

    def test_rejects_http_error_wrong_host_and_unreachable_url(self):
        path = cu.local_image(REAL_IMAGE)
        for fake in (FakeCloudinary(upload_status=401),
                     FakeCloudinary(url_host="https://evil.example/"),
                     FakeCloudinary(head_status=404),
                     FakeCloudinary(head_type="text/html")):
            with self.assertRaises(cu.UploadError):
                cu.upload(path, CREDS, fake)

    def test_local_image_refuses_paths_outside_images(self):
        for bad in ("../secrets.png", "images/../index.html", "/etc/passwd", "images/missing.png", "deals.json", None):
            with self.assertRaises(cu.UploadError):
                cu.local_image(bad)


class SyncTests(unittest.TestCase):
    def deals(self):
        return [{"title": "TV", "image": REAL_IMAGE},
                {"title": "Duplicate image", "image": REAL_IMAGE},
                {"title": "Remote", "image": "https://example.com/x.jpg"},
                {"title": "No image"}]

    def test_uploads_new_image_once_and_merges(self):
        mapping = [{"title": "Other", "image": "images/other.jpg", "cloudinary_url": "https://res.cloudinary.com/x/o.jpg"}]
        fake = FakeCloudinary()
        uploaded, failed = cu.sync(self.deals(), mapping, CREDS, fake, log=lambda *_: None)
        self.assertEqual((uploaded, failed), (1, 0))
        self.assertEqual([m["image"] for m in mapping], ["images/other.jpg", REAL_IMAGE])
        self.assertEqual(sum(1 for c in fake.calls if c[0] == "POST"), 1)

    def test_unchanged_image_is_skipped(self):
        mapping = []
        cu.sync(self.deals(), mapping, CREDS, FakeCloudinary(), log=lambda *_: None)
        fake = FakeCloudinary()
        self.assertEqual(cu.sync(self.deals(), mapping, CREDS, fake, log=lambda *_: None), (0, 0))
        self.assertEqual(fake.calls, [])

    def test_legacy_entry_without_hash_is_kept(self):
        legacy = {"title": "TV", "image": REAL_IMAGE, "cloudinary_url": "https://res.cloudinary.com/x/legacy.jpg"}
        mapping = [dict(legacy)]
        fake = FakeCloudinary()
        self.assertEqual(cu.sync(self.deals(), mapping, CREDS, fake, log=lambda *_: None), (0, 0))
        self.assertEqual(mapping, [legacy])

    def test_changed_content_reuploads(self):
        mapping = [{"title": "TV", "image": REAL_IMAGE, "cloudinary_url": "https://res.cloudinary.com/x/old.jpg", "sha256": "0" * 64}]
        self.assertEqual(cu.sync(self.deals(), mapping, CREDS, FakeCloudinary(), log=lambda *_: None), (1, 0))
        self.assertNotIn("old.jpg", mapping[0]["cloudinary_url"])

    def test_failure_keeps_existing_entries(self):
        existing = {"title": "TV", "image": REAL_IMAGE, "cloudinary_url": "https://res.cloudinary.com/x/old.jpg", "sha256": "0" * 64}
        mapping = [dict(existing)]
        self.assertEqual(cu.sync(self.deals(), mapping, CREDS, FakeCloudinary(upload_status=500), log=lambda *_: None), (0, 1))
        self.assertEqual(mapping, [existing])

    def test_dry_run_makes_no_calls(self):
        fake, lines = FakeCloudinary(), []
        self.assertEqual(cu.sync(self.deals(), [], CREDS, fake, dry_run=True, log=lines.append), (0, 0))
        self.assertEqual((fake.calls, lines), ([], [f"Would upload: {REAL_IMAGE}"]))

    def test_generated_cards_are_included(self):
        self.assertEqual(cu.deal_images([{"image": "images/generated/card.jpg"}]), ["images/generated/card.jpg"])

    def test_repository_mapping_is_valid(self):
        for entry in cu.load_mapping():
            self.assertTrue(entry["cloudinary_url"].startswith("https://res.cloudinary.com/"))
            cu.local_image(entry["image"])


if __name__ == "__main__":
    unittest.main()

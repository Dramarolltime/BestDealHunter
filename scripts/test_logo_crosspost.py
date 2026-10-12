"""Brand logo cross-post safety tests; no network, secrets, Buffer or Cloudinary calls."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import logo_crosspost as lc  # noqa: E402


class FakeBuffer:
    def __init__(self):
        self.created = 0

    def type_fields(self, name):
        return {"ShareMode": {"enumValues": ["addToQueue", "shareNow"]},
                "SchedulingType": {"enumValues": ["automatic", "notification"]}}.get(name, {"fields": [], "inputFields": [], "enumValues": []})

    def post(self, post_id, links):
        return {"id": post_id, "status": "sent"}


class LogoTests(unittest.TestCase):
    def test_committed_logo_is_a_square_jpeg(self):
        data, digest = lc.logo_bytes()
        self.assertEqual(data[:3], b"\xff\xd8\xff")
        from PIL import Image
        with Image.open(lc.LOGO) as im:
            self.assertEqual((im.format, im.size), ("JPEG", (1080, 1080)))
        self.assertEqual(lc.public_id(digest), f"bestdealhunter/brand/logo-{digest[:12]}")

    def test_caption_is_brand_only(self):
        for word in ("$", "% off", "price drop", "#ad"):
            self.assertNotIn(word, lc.CAPTION.lower())

    def test_never_reads_the_deal_kill_switch(self):
        self.assertNotIn("INSTAGRAM_PUBLISH_ENABLED", Path(lc.__file__).read_text().split('"""', 2)[2])


class HostedLogoTests(unittest.TestCase):
    def test_pinned_cloudinary_url_is_the_committed_logo(self):
        _, digest = lc.logo_bytes()
        self.assertIn(lc.public_id(digest), lc.HOSTED_LOGO)
        self.assertTrue(lc.HOSTED_LOGO.startswith("https://res.cloudinary.com/"))


class WebsiteTests(unittest.TestCase):
    """The site shows the identical logo file, linked to the exact Instagram post, only once it exists."""
    ROOT = Path(__file__).resolve().parents[1]

    def data(self):
        import json
        return json.loads((self.ROOT / "brand-post.json").read_text())

    def test_site_image_is_the_posted_logo(self):
        d = self.data()
        _, digest = lc.logo_bytes()
        self.assertEqual(d["image"], str(lc.LOGO.relative_to(self.ROOT)))
        self.assertEqual(d["image_sha256"], digest)
        self.assertEqual(d["cloudinary_url"], lc.HOSTED_LOGO)
        html = (self.ROOT / "index.html").read_text()
        self.assertIn(f'id="brand-post-image" src="{d["image"]}"', html)

    def test_section_hidden_until_a_real_post_url_exists(self):
        import re
        html = (self.ROOT / "index.html").read_text()
        self.assertIn('id="brand-post" hidden', html)
        url = self.data()["instagram_url"]
        if url is not None:
            self.assertRegex(url, r"^https://www\.instagram\.com/(?:p|reel)/[A-Za-z0-9_-]+/?$")
            self.assertTrue(self.data()["buffer_post_id"])
        self.assertIsNotNone(re.search(r"instagram\\\.com\\/\(\?:p\|reel\)", html))


class PublishOnceTests(unittest.TestCase):
    """Publishing must refuse whenever any lock or post record exists, before any upload or post."""

    def run_publish(self, tags):
        buf = FakeBuffer()
        logs = []
        with mock.patch.object(lc, "Buffer", return_value=buf), \
                mock.patch.object(lc, "remote_tags", side_effect=lambda prefix: [t for t in tags if t.startswith(prefix)]), \
                mock.patch.object(lc, "cloudinary_upload", side_effect=AssertionError("must not upload")), \
                mock.patch.object(lc, "create_live_post", side_effect=AssertionError("must not post")), \
                mock.patch.object(lc, "push_tag", side_effect=AssertionError("must not tag")):
            code = lc.run("publish", {"BUFFER_API_KEY": "k", "BUFFER_CHANNEL_ID": "c"}, log=logs.append)
        return code, logs

    def test_existing_lock_refuses(self):
        code, logs = self.run_publish(["brand-logo-post/lock"])
        self.assertEqual(code, 3)
        self.assertTrue(any("Refused" in line for line in logs))

    def test_recorded_post_refuses_and_reports_it(self):
        code, logs = self.run_publish(["brand-logo-post/lock", "brand-logo-post/post-abc123"])
        self.assertEqual(code, 3)
        self.assertTrue(any("abc123" in line for line in logs))

    def test_lock_is_taken_before_the_post_and_post_is_recorded(self):
        order = []
        buf = FakeBuffer()
        with mock.patch.object(lc, "Buffer", return_value=buf), \
                mock.patch.object(lc, "remote_tags", return_value=[]), \
                mock.patch.object(lc, "cloudinary_upload", side_effect=AssertionError("must reuse the hosted logo")), \
                mock.patch.object(lc, "verify_hosted", side_effect=lambda url, d: order.append(("verify", url)) or {}), \
                mock.patch.object(lc, "push_tag", side_effect=lambda name, msg: order.append(name)), \
                mock.patch.object(lc, "create_live_post", side_effect=lambda b, c, u: order.append("post") or {"id": "p1", "status": "sending"}), \
                mock.patch.object(lc.time, "sleep"):
            code = lc.run("publish", {"BUFFER_API_KEY": "k", "BUFFER_CHANNEL_ID": "c"}, log=lambda *_: None)
        self.assertEqual(code, 0)
        self.assertEqual(order, [("verify", lc.HOSTED_LOGO), "brand-logo-post/lock", "post", "brand-logo-post/post-p1"])

    def test_bad_hosted_image_stops_before_lock_or_post(self):
        with mock.patch.object(lc, "Buffer", return_value=FakeBuffer()), \
                mock.patch.object(lc, "remote_tags", return_value=[]), \
                mock.patch.object(lc, "verify_hosted", side_effect=lc.Stop("hosted bytes differ")), \
                mock.patch.object(lc, "push_tag", side_effect=AssertionError("must not lock")), \
                mock.patch.object(lc, "create_live_post", side_effect=AssertionError("must not post")):
            with self.assertRaises(lc.Stop):
                lc.run("publish", {"BUFFER_API_KEY": "k", "BUFFER_CHANNEL_ID": "c"}, log=lambda *_: None)

    def test_verify_hosted_requires_identical_bytes(self):
        data, digest = lc.logo_bytes()
        with mock.patch.object(lc, "fetch", return_value=(200, "image/jpeg", data, "u")):
            self.assertEqual(lc.verify_hosted("u", digest)["sha256"], digest)
        with mock.patch.object(lc, "fetch", return_value=(200, "image/jpeg", data + b"x", "u")):
            with self.assertRaises(lc.Stop):
                lc.verify_hosted("u", digest)
        with mock.patch.object(lc, "fetch", return_value=(404, "text/html", b"", "u")):
            with self.assertRaises(lc.Stop):
                lc.verify_hosted("u", digest)


if __name__ == "__main__":
    unittest.main()

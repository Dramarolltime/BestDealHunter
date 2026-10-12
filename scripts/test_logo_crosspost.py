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
                mock.patch.object(lc, "cloudinary_upload", return_value={"url": "https://res.cloudinary.com/x/image/upload/a.jpg"}), \
                mock.patch.object(lc, "verify_hosted", side_effect=lambda url, d: order.append("verify") or {}), \
                mock.patch.object(lc, "push_tag", side_effect=lambda name, msg: order.append(name)), \
                mock.patch.object(lc, "create_live_post", side_effect=lambda b, c, u: order.append("post") or {"id": "p1", "status": "sending"}), \
                mock.patch.object(lc.time, "sleep"):
            code = lc.run("publish", {"BUFFER_API_KEY": "k", "BUFFER_CHANNEL_ID": "c"}, log=lambda *_: None)
        self.assertEqual(code, 0)
        self.assertEqual(order, ["verify", "brand-logo-post/lock", "post", "brand-logo-post/post-p1"])

    def test_bad_hosted_image_stops_before_lock_or_post(self):
        with mock.patch.object(lc, "Buffer", return_value=FakeBuffer()), \
                mock.patch.object(lc, "remote_tags", return_value=[]), \
                mock.patch.object(lc, "cloudinary_upload", return_value={"url": "u"}), \
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

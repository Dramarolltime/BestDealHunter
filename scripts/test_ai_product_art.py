"""AI product picture tests: dormant without a key, prompt forbids text/logos, failures keep the card. No network."""
import base64
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ai_product_art as ai  # noqa: E402
import deal_graphics as g  # noqa: E402

KEY = "sk-test-not-a-real-key"


def png():
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), "#123456").save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def deal(**over):
    d = {"store": "Best Buy", "title": "ILLUSTRATIVE TEST TV — NOT A REAL DEAL", "category": "Electronics",
         "url": "https://www.bestbuy.com/site/test", "price": 299.99, "original_price": 699.99,
         "publication_approved": True}
    d.update(over)
    return d


class ArtTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (ai.ROOT, ai.SOURCE)
        ai.ROOT, ai.SOURCE = Path(self.tmp.name), Path(self.tmp.name) / "images" / "source"
        self.requests = []

    def tearDown(self):
        ai.ROOT, ai.SOURCE = self.saved
        self.tmp.cleanup()

    def ok(self, request, timeout=120):
        self.requests.append(request)
        return {"data": [{"b64_json": png()}]}

    def test_no_ai_call_without_key_and_nothing_written_offline(self):
        def boom(*a, **k):
            raise AssertionError("no AI call without a key")
        feed = ai.ROOT / "deals.json"
        feed.write_text(json.dumps([deal(listing_photo_url="https://i.ebayimg.com/x/s-l1600.jpg")]))
        saved, ai.FEED = ai.FEED, feed
        try:
            def offline(*a, **k):
                raise urllib.error.URLError("offline")
            self.assertEqual(ai.main([], env={}, transport=boom, opener=offline), 0)
        finally:
            ai.FEED = saved
        self.assertNotIn("image_source", json.loads(feed.read_text())[0])

    def test_picture_is_saved_and_linked_to_the_deal(self):
        deals = [deal()]
        self.assertEqual(ai.run(deals, KEY, 3, transport=self.ok, log=lambda *_: None), 1)
        self.assertEqual(deals[0]["image_source_kind"], g.AI_SCENE)
        self.assertRegex(deals[0]["image_source"], r"^images/source/ai-[0-9a-f]{12}\.jpg$")
        with Image.open(ai.ROOT / deals[0]["image_source"]) as img:
            self.assertEqual(img.format, "JPEG")
        req = self.requests[0]
        self.assertEqual(req.full_url, ai.API)
        self.assertEqual(req.get_header("Authorization"), f"Bearer {KEY}")
        prompt = json.loads(req.data)["prompt"]
        self.assertIn("ILLUSTRATIVE TEST TV", prompt)
        for banned in ("no text", "numbers", "prices", "logos", "brand names"):
            self.assertIn(banned, prompt.lower())

    def test_only_new_approved_non_amazon_deals_without_a_card(self):
        deals = [deal(image="images/generated/deal-0123456789ab-square.jpg"), deal(publication_approved=False),
                 deal(store="Amazon"), deal(image_source="images/source/x.jpg")]
        self.assertEqual(ai.run(deals, KEY, 3, transport=self.ok, log=lambda *_: None), 0)
        self.assertEqual(self.requests, [])

    def test_run_limit(self):
        deals = [deal(url=f"https://www.bestbuy.com/site/{i}") for i in range(5)]
        self.assertEqual(ai.run(deals, KEY, 2, transport=self.ok, log=lambda *_: None), 2)

    def test_failure_keeps_the_standard_card_and_never_logs_the_key(self):
        logs = []

        def fail(request, timeout=120):
            raise urllib.error.HTTPError(ai.API, 429, "rate limited", {}, None)
        deals = [deal()]
        self.assertEqual(ai.run(deals, KEY, 3, transport=fail, log=logs.append), 0)
        self.assertNotIn("image_source", deals[0])
        self.assertIn("HTTP 429", logs[0])
        self.assertNotIn(KEY, " ".join(logs))
        self.assertEqual(ai.run([deal()], KEY, 3, transport=lambda r, timeout=120: {"data": []},
                                log=logs.append), 0)


class CardWithPictureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "images" / "source").mkdir(parents=True)
        Image.new("RGB", (1024, 1024), "#22c55e").save(root / "images" / "source" / "ai-0123456789ab.jpg")
        self.saved = (g.ROOT, g.SOURCE)
        g.ROOT, g.SOURCE = root, root / "images" / "source"

    def tearDown(self):
        g.ROOT, g.SOURCE = self.saved
        self.tmp.cleanup()

    def test_picture_fills_the_middle_and_numbers_come_from_the_deal(self):
        d = deal(verified_date="2026-10-14", image_source="images/source/ai-0123456789ab.jpg",
                 image_source_kind=g.AI_KIND)
        for kind, size in g.SIZES.items():
            card = g.render_card(d, kind)
            self.assertEqual(card.size, size)
            centre = card.getpixel((size[0] // 2, int(size[1] * 0.42)))
            self.assertTrue(all(abs(a - b) <= 12 for a, b in zip(centre, (34, 197, 94))), centre)


if __name__ == "__main__":
    unittest.main()


class ListingPhotoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (ai.ROOT, ai.SOURCE)
        ai.ROOT, ai.SOURCE = Path(self.tmp.name), Path(self.tmp.name) / "images" / "source"

    def tearDown(self):
        ai.ROOT, ai.SOURCE = self.saved
        self.tmp.cleanup()

    @staticmethod
    def opener(data):
        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self, n): return data[:n]
        return lambda req, timeout=30: Resp()

    def test_real_listing_photo_replaces_ai_picture_and_needs_no_key(self):
        buf = io.BytesIO()
        Image.new("RGB", (500, 500), "#ffffff").save(buf, "JPEG")
        d = deal(listing_photo_url="https://i.ebayimg.com/images/g/abc/s-l1600.jpg",
                 image="images/generated/deal-0123456789ab-square.jpg", image_source="images/source/ai-x.jpg",
                 image_source_kind=g.AI_SCENE)
        self.assertEqual(ai.listing_photos([d], opener=self.opener(buf.getvalue()), log=lambda *_: None), 1)
        self.assertEqual(d["image_source_kind"], g.LISTING_PHOTO)
        self.assertNotIn("image", d)  # card is redrawn with the real photo
        self.assertFalse(ai.needs_art(dict(d, image_source=None)))  # never an AI picture when a listing photo exists

    def test_only_ebay_image_host_and_real_images(self):
        for url, data in (("https://evil.example.com/x.jpg", b"\xff\xd8\xff"),
                          ("https://i.ebayimg.com/x.jpg", b"<html>")):
            d = deal(listing_photo_url=url)
            self.assertEqual(ai.listing_photos([d], opener=self.opener(data), log=lambda *_: None), 0)
            self.assertNotIn("image_source", d)

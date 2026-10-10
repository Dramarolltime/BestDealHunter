#!/usr/bin/env python3
"""Branded deal card tests. Renders only illustrative test data; no listing or post changes."""
import io
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import deal_graphics as g  # noqa: E402
from generate_deal_images import generate_missing  # noqa: E402


def sample(**changes):
    deal = {"store": "Best Buy", "title": "ILLUSTRATIVE TEST PRODUCT — NOT A REAL DEAL", "category": "Electronics",
            "url": "https://www.bestbuy.com/site/test", "price": 299.99, "original_price": 699.99,
            "verified_date": date.today().isoformat(), "source": "Test fixture", "price_evidence": "Test fixture"}
    deal.update(changes)
    return deal


def jpeg_bytes(image):
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=92)
    return buf.getvalue()


class FactsTests(unittest.TestCase):
    def test_numbers_come_from_the_deal(self):
        facts = g.card_facts(sample())
        self.assertEqual((facts["price"], facts["reference"], facts["savings"], facts["percent"]),
                         ("$299.99", "$699.99", "$400", 57))

    def test_percent_is_never_rounded_up(self):
        self.assertEqual(g.card_facts(sample(price=50.01, original_price=100))["percent"], 49)
        self.assertEqual(g.card_facts(sample(price=10.14, original_price=27))["percent"], 62)

    def test_money_format(self):
        self.assertEqual([g.money(v) for v in (169, 1299.5, 11.62)], ["$169", "$1,299.50", "$11.62"])

    def test_reference_label_is_honest_per_store(self):
        self.assertEqual(g.card_facts(sample())["reference_label"], "Comparable value")
        self.assertEqual(g.card_facts(sample(store="Amazon"))["reference_label"], "List price")
        self.assertEqual(g.card_facts(sample(store="Target"))["reference_label"], "Reference price")
        self.assertEqual(g.card_facts(sample(reference_price_label="Typical price"))["reference_label"], "Typical price")

    def test_verified_date_text(self):
        self.assertEqual(g.card_facts(sample(verified_date="2026-10-09"))["verified"], "Oct 9, 2026")
        self.assertIsNone(g.card_facts(sample(verified_date=None))["verified"])

    def test_rejects_unusable_deals(self):
        for bad in (sample(price=700), sample(price=0), sample(title=" "), sample(original_price="x"),
                    sample(verified_date="10/09/2026"), {"title": "x"}):
            with self.assertRaises(g.CardError):
                g.card_facts(bad)


class RenderTests(unittest.TestCase):
    def test_sizes_and_valid_jpeg(self):
        for kind, size in g.SIZES.items():
            card = g.render_card(sample(), kind)
            self.assertEqual(card.size, size)
            with Image.open(io.BytesIO(jpeg_bytes(card))) as reread:
                reread.verify()

    def test_deterministic_output(self):
        self.assertEqual(jpeg_bytes(g.render_card(sample())), jpeg_bytes(g.render_card(sample())))

    def test_long_titles_still_render(self):
        self.assertEqual(g.render_card(sample(title="Very long product name " * 12), "square").size, (1080, 1080))

    def test_bundled_font_and_licence_present(self):
        self.assertTrue(g.FONT.is_file())
        self.assertIn("SIL Open Font License", (g.FONT.parent / "Montserrat-OFL.txt").read_text())


class PhotoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "images" / "source").mkdir(parents=True)
        Image.new("RGB", (640, 480), "#22c55e").save(root / "images" / "source" / "photo.png")
        self.saved = (g.ROOT, g.SOURCE)
        g.ROOT, g.SOURCE = root, root / "images" / "source"

    def tearDown(self):
        g.ROOT, g.SOURCE = self.saved
        self.tmp.cleanup()

    def test_authorized_photo_is_used(self):
        with_photo = g.render_card(sample(image_source="images/source/photo.png"))
        without = g.render_card(sample())
        self.assertEqual(with_photo.getpixel((540, 470)), (34, 197, 94))  # photo centre (green)
        self.assertNotEqual(without.getpixel((540, 470)), (34, 197, 94))

    def test_unauthorized_photo_paths_are_refused(self):
        for raw in ("images/IMG_4236.jpeg", "images/source/../../deals.json", "/etc/passwd",
                    "images/source/missing.png", "https://example.com/x.jpg"):
            with self.assertRaises(g.CardError):
                g.render_card(sample(image_source=raw))


class GeneratorTests(unittest.TestCase):
    def test_only_deals_without_images_get_cards(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            deals = [sample(), sample(url="https://www.bestbuy.com/site/other", image="images/existing.jpg"),
                     sample(url="https://www.bestbuy.com/site/bad", price=600)]
            made = generate_missing(deals, out=out, log=lambda *_: None)
            self.assertEqual(made, 1)
            self.assertTrue(deals[0]["image"].endswith("-feed.jpg"))
            self.assertEqual(deals[1]["image"], "images/existing.jpg")
            self.assertNotIn("image", deals[2])  # fails the 50% rule: no card
            self.assertEqual(len(list(out.glob("*.jpg"))), 2)


if __name__ == "__main__":
    unittest.main()

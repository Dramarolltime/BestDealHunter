"""eBay Browse API source: only eBay-reported, credible >=50% discounts verify. No network."""
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ebay_deals as eb  # noqa: E402


def item(**changes):
    i = {"itemId": "v1|123456789012|0", "legacyItemId": "123456789012", "title": "ILLUSTRATIVE TEST Air Fryer",
         "price": {"value": "39.99", "currency": "USD"}, "condition": "New", "conditionId": "1000",
         "itemWebUrl": "https://www.ebay.com/itm/123456789012?hash=item1cbe&_trkparms=x",
         "marketingPrice": {"originalPrice": {"value": "99.99", "currency": "USD"}, "discountPercentage": "60",
                            "priceTreatment": "STRIKETHROUGH"},
         "seller": {"username": "bigstore", "feedbackPercentage": "99.4", "feedbackScore": 25000}}
    i.update(changes)
    return i


class RowTests(unittest.TestCase):
    def test_credible_strikethrough_discount_verifies_with_a_clean_link(self):
        r = eb.row_for(item(), "t", "Home")
        self.assertEqual((r["verdict"], r["page"]["discount"], r["page"]["reference_label"]), ("verified", 60, "Was"))
        self.assertEqual(r["url"], "https://www.ebay.com/itm/123456789012")
        self.assertEqual((r["store"], r["evidence_method"], r["category_hint"]), ("eBay", "retailer_api", "Home"))

    def test_listing_photo_is_taken_only_from_ebay_at_full_size(self):
        row = eb.row_for(item(image={"imageUrl": "https://i.ebayimg.com/images/g/abc/s-l225.jpg"}), "2026-10-15T00:00:00Z")
        self.assertEqual(row["photo_url"], "https://i.ebayimg.com/images/g/abc/s-l1600.jpg")
        self.assertIsNone(eb.row_for(item(image={"imageUrl": "http://elsewhere.com/x.jpg"}), "t")["photo_url"])

    def test_rejections(self):
        mp = item()["marketingPrice"]
        cases = {
            "below_50": item(price={"value": "50.00"}),
            "unconfirmed": item(marketingPrice=dict(mp, priceTreatment="MINIMUM_ADVERTISED_PRICE")),
        }
        for verdict, i in cases.items():
            self.assertEqual(eb.row_for(i, "t")["verdict"], verdict)
        for weak in (item(condition="Used", conditionId="3000"),
                     item(seller={"username": "new", "feedbackPercentage": "100", "feedbackScore": 12}),
                     item(seller={"username": "meh", "feedbackPercentage": "95.0", "feedbackScore": 50000}),
                     item(marketingPrice=None), item(legacyItemId=None, itemId="weird", itemWebUrl="https://ebay.example/x")):
            self.assertEqual(eb.row_for(weak, "t")["verdict"], "unconfirmed", weak)

    def test_exactly_fifty_percent_in_cents(self):
        self.assertEqual(eb.row_for(item(price={"value": "49.99"}, marketingPrice={
            "originalPrice": {"value": "99.98"}, "priceTreatment": "LIST_PRICE"}), "t")["verdict"], "verified")


class MainTests(unittest.TestCase):
    def test_dormant_without_secrets(self):
        out = Path(tempfile.mkdtemp()) / "o.json"
        with mock.patch("sys.stdout"):
            self.assertEqual(eb.main(["--out", str(out)], env={}), 0)
        self.assertEqual(json.loads(out.read_text()), [])

    def test_end_to_end_with_fake_api_and_no_secret_in_output(self):
        def opener(req, timeout):
            if req.full_url == eb.TOKEN_URL:
                return io.BytesIO(json.dumps({"access_token": "TOKEN"}).encode())
            self.assertIn("X-ebay-c-marketplace-id", req.headers)
            return io.BytesIO(json.dumps({"itemSummaries": [item()]}).encode())
        out = Path(tempfile.mkdtemp()) / "o.json"
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            eb.main(["--out", str(out)], env={"EBAY_CLIENT_ID": "CID", "EBAY_CLIENT_SECRET": "SECRET123"},
                    opener=opener, sleep=lambda s: None)
        rows = json.loads(out.read_text())
        self.assertEqual(len(rows), 1)  # same item in every category is deduplicated
        self.assertNotIn("SECRET123", buf.getvalue())

    def test_api_errors_never_crash(self):
        def fail(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)
        out = Path(tempfile.mkdtemp()) / "o.json"
        with mock.patch("sys.stdout"):
            self.assertEqual(eb.main(["--out", str(out)], env={"EBAY_CLIENT_ID": "a", "EBAY_CLIENT_SECRET": "b"},
                                     opener=fail), 0)
        self.assertEqual(json.loads(out.read_text()), [])


if __name__ == "__main__":
    unittest.main()

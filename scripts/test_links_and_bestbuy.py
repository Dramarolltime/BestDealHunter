"""Direct links / disclosure, and the Best Buy Products API source. No network."""
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bestbuy_deals as bb  # noqa: E402
from links import direct_url, disclosure, is_affiliate_link  # noqa: E402


class LinkTests(unittest.TestCase):
    def test_tracking_parameters_are_removed_product_parameters_kept(self):
        self.assertEqual(direct_url("https://www.macys.com/shop/product/x?ID=123&cm_sp=a&utm_source=ig&tag=t"),
                         "https://www.macys.com/shop/product/x?ID=123&cm_sp=a")
        self.assertEqual(direct_url("https://www.amazon.com/dp/B0X?tag=bestdealhun0b-20&th=1#reviews"),
                         "https://www.amazon.com/dp/B0X")
        self.assertIsNone(direct_url("https://link.amazon/B0iYV850b"))
        self.assertIsNone(direct_url("https://goto.target.com/c/1/2/3"))

    def test_affiliate_detection_and_matching_disclosure(self):
        self.assertTrue(is_affiliate_link("https://www.amazon.com/dp/B0X?tag=bestdealhun0b-20"))
        self.assertTrue(is_affiliate_link("https://link.amazon/B0iYV850b"))
        self.assertFalse(is_affiliate_link("https://www.bestbuy.com/site/6444455.p?skuId=6444455"))
        card, caption = disclosure({"url": "https://www.bestbuy.com/site/1.p", "store": "Best Buy"})
        self.assertNotIn("#ad", card + " ".join(caption))
        card, caption = disclosure({"url": "https://www.amazon.com/dp/B0X?tag=t", "store": "Amazon"})
        self.assertIn("#ad", card)
        self.assertIn("As an Amazon Associate I earn from qualifying purchases.", caption)


def product(**changes):
    p = {"sku": 6444455, "name": "ILLUSTRATIVE TEST Game", "url": "https://www.bestbuy.com/site/test/6444455.p?skuId=6444455",
         "salePrice": 19.99, "regularPrice": 39.99, "onlineAvailability": True, "marketplace": False,
         "categoryPath": [{"name": "Best Buy"}, {"name": "Video Games"}], "priceUpdateDate": "2026-10-13T00:00:00"}
    p.update(changes)
    return p


class BestBuyTests(unittest.TestCase):
    def test_verdicts_from_best_buys_own_fields(self):
        ok = bb.row_for(product(), "t")
        self.assertEqual((ok["verdict"], ok["page"]["discount"], ok["url"]),
                         ("verified", 50, "https://www.bestbuy.com/site/test/6444455.p"))  # 19.99/39.99 = 50.01% off
        self.assertEqual(bb.row_for(product(salePrice=20.00), "t")["verdict"], "below_50")
        self.assertEqual(bb.row_for(product(onlineAvailability=False), "t")["verdict"], "unavailable")
        self.assertEqual(bb.row_for(product(marketplace=True), "t")["verdict"], "unavailable")
        self.assertEqual(bb.row_for(product(regularPrice=None), "t")["verdict"], "unconfirmed")

    def test_dormant_without_key_and_never_crashes_on_api_errors(self):
        out = Path(tempfile.mkdtemp()) / "o.json"
        with unittest.mock.patch("sys.stdout"):
            self.assertEqual(bb.main(["--out", str(out)], env={}), 0)
        self.assertEqual(json.loads(out.read_text()), [])

        def fail(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)
        with unittest.mock.patch("sys.stdout"):
            self.assertEqual(bb.main(["--out", str(out)], env={"BEST_BUY_API_KEY": "k"}, opener=fail), 0)
        self.assertEqual(json.loads(out.read_text()), [])

    def test_key_is_never_printed(self):
        out = Path(tempfile.mkdtemp()) / "o.json"
        body = json.dumps({"products": [product()], "totalPages": 1}).encode()
        buf = io.StringIO()
        with unittest.mock.patch("sys.stdout", buf):
            bb.main(["--out", str(out)], env={"BEST_BUY_API_KEY": "SECRETKEY123"}, opener=lambda r, timeout: io.BytesIO(body))
        self.assertNotIn("SECRETKEY123", buf.getvalue())
        self.assertIn("verified", buf.getvalue())


import unittest.mock  # noqa: E402

if __name__ == "__main__":
    unittest.main()

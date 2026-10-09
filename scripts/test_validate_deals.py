"""Safety checks for deal validation; no external services or secrets required."""
import unittest
from datetime import date, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_deals import validate

def sample():
    return {
        "store": "eBay",
        "title": "Sample verified product",
        "category": "Electronics",
        "source": "Authorized retailer data",
        "price_evidence": "Independent, like-condition comparison",
        "url": "https://www.ebay.com/itm/example",
        "price": 50,
        "original_price": 100,
        "verified_date": date.today().isoformat(),
    }

class DealValidationTests(unittest.TestCase):
    def test_exactly_fifty_percent(self):
        self.assertEqual(validate([sample()]), 1)

    def test_below_fifty_percent(self):
        d = sample()
        d["price"] = 50.01
        with self.assertRaises(ValueError):
            validate([d])

    def test_missing_evidence(self):
        d = sample()
        d["price_evidence"] = ""
        with self.assertRaises(ValueError):
            validate([d])

    def test_stale_verification(self):
        d = sample()
        d["verified_date"] = (date.today() - timedelta(days=3)).isoformat()
        with self.assertRaises(ValueError):
            validate([d])

    def test_insecure_url(self):
        d = sample()
        d["url"] = "http://example.com"
        with self.assertRaises(ValueError):
            validate([d])

    def test_invalid_store(self):
        d = sample()
        d["store"] = "Unverified Marketplace"
        with self.assertRaises(ValueError):
            validate([d])

    def test_empty_feed(self):
        self.assertEqual(validate([]), 0)

if __name__ == "__main__":
    unittest.main()

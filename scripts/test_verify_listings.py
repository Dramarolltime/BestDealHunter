"""verdict() must never treat a blocked or failed check as verification. No network."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_listings import verdict  # noqa: E402

OK = {"http": 200, "title": "Insignia 55in TV - Best Buy", "claimed_price": 299.99,
      "offers": [{"price": "299.99", "availability": "InStock"}]}


class VerdictTests(unittest.TestCase):
    def test_only_matching_in_stock_offer_is_verified(self):
        self.assertEqual(verdict(OK), "verified")

    def test_blocked_or_failed_never_verified(self):
        for row in ({"error": "net::ERR_HTTP2_PROTOCOL_ERROR"},
                    dict(OK, http=403), dict(OK, http=429),
                    dict(OK, title="Access Denied - Macy's"),           # 200 + block page still blocked
                    dict(OK, title="Robot or human?"),
                    dict(OK, title="Pardon Our Interruption")):
            self.assertEqual(verdict(row), "blocked", row)

    def test_page_without_offer_data_is_unconfirmed(self):
        self.assertEqual(verdict(dict(OK, offers=[])), "unconfirmed")
        self.assertEqual(verdict(dict(OK, http=404)), "unconfirmed")

    def test_price_difference_is_mismatch(self):
        self.assertEqual(verdict(dict(OK, claimed_price=249.99)), "mismatch")
        self.assertEqual(verdict(dict(OK, claimed_price=None)), "mismatch")

    def test_out_of_stock_is_unavailable(self):
        self.assertEqual(verdict(dict(OK, offers=[{"price": 299.99, "availability": "OutOfStock"}])), "unavailable")


if __name__ == "__main__":
    unittest.main()

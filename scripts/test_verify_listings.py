"""verdict(): the product page alone must show price, >=50% off its own reference, and stock.
A blocked or failed check is never verification. No network."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_listings import verdict  # noqa: E402

OK = {"http": 200, "title": "Insignia 55in TV - Best Buy", "claimed_price": 299.99,
      "offers": [{"price": "299.99", "availability": "InStock"}], "reference_values": ["699.99"]}
AMAZON = {"http": 200, "title": "Amazon.com: Bose QuietComfort", "claimed_price": None, "offers": [],
          "visible": {"price": "169.00", "reference": "359.00", "reference_label": "List Price",
                      "availability": "In Stock", "buy_button": True}}


class VerdictTests(unittest.TestCase):
    def test_page_price_reference_and_stock_verify(self):
        self.assertEqual(verdict(OK), "verified")
        self.assertEqual((OK["page"]["price"], OK["page"]["reference"], OK["page"]["discount"]), (299.99, 699.99, 57))

    def test_visible_page_elements_verify_without_schema_offer(self):
        row = dict(AMAZON)
        self.assertEqual(verdict(row), "verified")
        self.assertEqual(row["page"]["reference_label"], "List Price")

    def test_blocked_or_failed_never_verified(self):
        for row in ({"error": "net::ERR_HTTP2_PROTOCOL_ERROR"},
                    dict(OK, http=403), dict(OK, http=429),
                    dict(OK, title="Access Denied - Macy's"),           # 200 + block page still blocked
                    dict(OK, title="Robot or human?"),
                    dict(OK, title="Pardon Our Interruption")):
            self.assertEqual(verdict(row), "blocked", row)

    def test_no_price_or_no_reference_on_the_page_is_unconfirmed(self):
        self.assertEqual(verdict(dict(OK, offers=[], reference_values=[])), "unconfirmed")
        self.assertEqual(verdict(dict(OK, reference_values=[])), "unconfirmed")
        self.assertEqual(verdict(dict(OK, reference_values=["199.00"])), "unconfirmed")  # "reference" below price
        self.assertEqual(verdict(dict(OK, http=404)), "unconfirmed")

    def test_claimed_price_must_match_the_page(self):
        self.assertEqual(verdict(dict(OK, claimed_price=249.99)), "mismatch")
        self.assertEqual(verdict(dict(OK, claimed_price=None)), "verified")  # page values are the evidence

    def test_under_50_percent_is_rejected_with_floor(self):
        self.assertEqual(verdict(dict(OK, reference_values=["599.97"])), "below_50")  # 49.99% -> 49
        self.assertEqual(verdict(dict(OK, reference_values=["599.98"])), "verified")  # exactly 50%

    def test_not_buyable_is_unavailable(self):
        self.assertEqual(verdict(dict(OK, offers=[{"price": 299.99, "availability": "OutOfStock"}])), "unavailable")
        self.assertEqual(verdict(dict(AMAZON, visible=dict(AMAZON["visible"], availability="Currently unavailable."))),
                         "unavailable")
        self.assertEqual(verdict(dict(AMAZON, visible=dict(AMAZON["visible"], availability=None, buy_button=False))),
                         "unavailable")


if __name__ == "__main__":
    unittest.main()

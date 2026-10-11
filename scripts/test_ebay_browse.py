"""eBay Browse adapter tests with synthetic API-shaped fixtures. No network or credentials."""
import json
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect_ebay as ce  # noqa: E402
import ebay_browse as eb  # noqa: E402
from validate_deals import validate  # noqa: E402

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


def item(**changes):
    d = {"itemId": "v1|123|0", "title": "ILLUSTRATIVE TEST HEADPHONES — NOT A REAL LISTING",
         "price": {"value": "49.99", "currency": "USD"},
         "marketingPrice": {"originalPrice": {"value": "129.99", "currency": "USD"},
                            "discountPercentage": "62", "priceTreatment": "LIST_PRICE"},
         "condition": "New", "buyingOptions": ["FIXED_PRICE"],
         "itemWebUrl": "https://www.ebay.com/itm/123",
         "itemAffiliateWebUrl": "https://www.ebay.com/itm/123?mkcid=1&campid=5339220016"}
    d.update(changes)
    return d


class CandidateTests(unittest.TestCase):
    def test_qualifying_item_goes_to_review_only(self):
        cand, reason = eb.candidate(item(), "Electronics", NOW)
        self.assertIsNone(reason)
        self.assertEqual((cand["price"], cand["original_price"]), (49.99, 129.99))
        self.assertEqual((cand["review_status"], cand["publication_eligible"]), ("needs_review", False))
        self.assertEqual(cand["reference_price_label"], "Seller's list price")
        self.assertIn("not independently verified", cand["reference_price_type"])
        self.assertIn("campid=5339220016", cand["url"])  # affiliate URL preferred
        self.assertEqual(cand["verified_date"], "2026-10-10")
        validate([cand], today=NOW.date())  # structurally valid if a human later approves it

    def test_rejections(self):
        cases = {
            "no seller reference price": item(marketingPrice={}),
            "no USD price": item(price={"value": "49.99", "currency": "GBP"}),
            "not new condition": item(condition="Used"),
            "not a fixed-price listing": item(buyingOptions=["AUCTION"]),
            "only 23% off": item(price={"value": "99.99", "currency": "USD"},
                                 marketingPrice={"originalPrice": {"value": "129.99", "currency": "USD"}}),
            "disagrees": item(marketingPrice={"originalPrice": {"value": "129.99", "currency": "USD"},
                                              "discountPercentage": "80"}),
            "reference price not above price": item(marketingPrice={"originalPrice": {"value": "49.99", "currency": "USD"}}),
            "missing eBay item URL": item(itemWebUrl="https://evil.example/x", itemAffiliateWebUrl=None),
            "missing title": item(title="  "),
        }
        for expected, it in cases.items():
            cand, reason = eb.candidate(it, "Electronics", NOW)
            self.assertIsNone(cand, expected)
            self.assertIn(expected, reason)

    def test_discount_is_computed_and_floored(self):
        cand, _ = eb.candidate(item(price={"value": "50.01", "currency": "USD"},
                                    marketingPrice={"originalPrice": {"value": "100.02", "currency": "USD"}}),
                               "Electronics", NOW)
        self.assertIsNotNone(cand)  # exactly 50.0% off qualifies, computed by us


class FakeEbay:
    def __init__(self, token_status=200, items=None):
        self.token_status, self.items, self.calls = token_status, items or [item()], []

    def __call__(self, method, url, body=None, headers=None):
        self.calls.append((method, url, body, headers or {}))
        if "oauth2/token" in url:
            return self.token_status, json.dumps({"access_token": "tok"} if self.token_status == 200 else {}).encode()
        return 200, json.dumps({"itemSummaries": self.items}).encode()


class ApiTests(unittest.TestCase):
    def test_token_uses_basic_auth_and_browse_scope(self):
        fake = FakeEbay()
        self.assertEqual(eb.app_token("id", "sec", transport=fake), "tok")
        method, url, body, headers = fake.calls[0]
        self.assertTrue(url.startswith("https://api.ebay.com/identity/v1/oauth2/token"))
        self.assertNotIn("sec", url)
        self.assertIn(b"client_credentials", body)
        self.assertTrue(headers["Authorization"].startswith("Basic "))

    def test_token_failure(self):
        with self.assertRaises(eb.SourceError):
            eb.app_token("id", "sec", transport=FakeEbay(token_status=401))

    def test_search_sends_marketplace_affiliate_and_filters(self):
        fake = FakeEbay(items=[item(), item(), item(itemId="v1|456|0", condition="Used")])
        found = eb.collect([{"q": "headphones", "category": "Electronics"}], "tok", "5339220016",
                           transport=fake, now=NOW, log=lambda *_: None)
        self.assertEqual(len(found), 1)  # duplicate id collapsed, used item skipped
        _, url, _, headers = fake.calls[0]
        self.assertIn("conditions%3A%7BNEW%7D", url)
        self.assertEqual(headers["X-EBAY-C-MARKETPLACE-ID"], "EBAY_US")
        self.assertEqual(headers["X-EBAY-C-ENDUSERCTX"], "affiliateCampaignId=5339220016")

    def test_unknown_category_rejected(self):
        with self.assertRaises(eb.SourceError):
            eb.collect([{"q": "x", "category": "Gadgets"}], "tok", transport=FakeEbay(), now=NOW)

    def test_query_config_is_valid(self):
        queries = json.loads(ce.QUERIES.read_text())
        self.assertTrue(queries)
        for q in queries:
            self.assertTrue(q["q"].strip())
            self.assertIn(q["category"], eb.RULES["categories"])


class CollectorTests(unittest.TestCase):
    def test_merge_dedupes_against_queue_and_public_and_limits(self):
        new, _ = eb.candidate(item(), "Electronics", NOW)
        other, _ = eb.candidate(item(itemId="v1|9|0", itemWebUrl="https://www.ebay.com/itm/9",
                                     itemAffiliateWebUrl=None), "Electronics", NOW)
        queued = dict(new)
        merged, added = ce.merge_candidates([queued], [], [new, other])
        self.assertEqual([a["ebay_item_id"] for a in added], ["v1|9|0"])
        published = {"url": "https://www.ebay.com/itm/9"}
        self.assertEqual(ce.merge_candidates([], [published], [other])[1], [])
        many = [dict(other, ebay_item_id=str(i), url=f"https://www.ebay.com/itm/{i}") for i in range(30)]
        self.assertEqual(len(ce.merge_candidates([], [], many)[1]), ce.MAX_NEW_PER_RUN)

    def run_main(self, env, argv=()):
        with mock.patch.dict(os.environ, env, clear=False), mock.patch("sys.stdout"):
            for key in ("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET", "EBAY_ENV"):
                if key not in env:
                    os.environ.pop(key, None)
            return ce.main(list(argv))

    def test_dormant_without_credentials(self):
        with mock.patch.object(ce, "app_token") as token:
            self.assertEqual(self.run_main({}), 0)
            token.assert_not_called()

    def test_sandbox_is_always_a_dry_run(self):
        new, _ = eb.candidate(item(), "Electronics", NOW)
        with mock.patch.object(ce, "app_token", return_value="tok"), \
             mock.patch.object(ce, "collect", return_value=[new]), \
             mock.patch.object(Path, "write_text") as write:
            self.assertEqual(self.run_main({"EBAY_CLIENT_ID": "i", "EBAY_CLIENT_SECRET": "s", "EBAY_ENV": "sandbox"}), 0)
            write.assert_not_called()


if __name__ == "__main__":
    unittest.main()

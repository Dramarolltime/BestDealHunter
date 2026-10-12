"""Retailer verification tests with recorded-style fixtures; no network, keys or real retailers."""
import json
import sys
import unittest
from datetime import date
from pathlib import Path
from urllib import parse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_deals  # noqa: E402
from verify_deals import BestBuy, SourceError, apply, plan  # noqa: E402
from validate_deals import validate_feed  # noqa: E402

TODAY = date(2026, 10, 12)
NOW = "2026-10-12 16:00 UTC"
GAME_URL = "https://www.bestbuy.com/product/it-takes-two-playstation-5-playstation-4/J7CYF9YKJ5/sku/6444455"
BOOK_URL = "https://www.bestbuy.com/product/lenovo-chromebook/JJGH3QCJKR/sku/6673811"


def product(sku, sale, regular, online=True, orderable="Available", **extra):
    return {"sku": int(sku), "name": f"Product {sku}", "salePrice": sale, "regularPrice": regular, "onSale": sale < regular,
            "onlineAvailability": online, "orderable": orderable, "priceUpdateDate": "2026-10-12T03:00:00",
            "url": f"https://api.bestbuy.com/click/-/{sku}/pdp", **extra}


class FakeBestBuyAPI:
    def __init__(self, products, fail=False):
        self.products, self.fail, self.urls = products, fail, []

    def __call__(self, url):
        self.urls.append(url)
        if self.fail:
            raise SourceError("Best Buy Products API HTTP 503")
        query = parse.unquote(url.split("/products(", 1)[1].split(")?", 1)[0])
        field, values = query.split(" in(")
        wanted = set(values.rstrip(")").split(","))
        return {"from": 1, "total": 0, "products": [p for p in self.products if str(p.get(field)) in wanted]}


def candidate(url=GAME_URL, **extra):
    return {"status": "needs_checkout_verification", "publication_eligible": False, "store": "Best Buy",
            "title": "It Takes Two — PS5/PS4", "url": url, "listed_price": 19.99, **extra}


def published(url=GAME_URL, verified="2026-10-09", **extra):
    return {"store": "Best Buy", "title": "It Takes Two", "category": "Electronics", "url": url, "price": 19.99,
            "original_price": 39.99, "verified_date": verified, "source": "Best Buy page",
            "price_evidence": "Checked page", **extra}


def run(feed, queues, products, fail=False):
    api = FakeBestBuyAPI(products, fail)
    sources = {"Best Buy": BestBuy("SECRET-KEY", transport=api)}
    actions, rows = plan(feed, queues, sources, TODAY, NOW)
    return actions, rows, api


class PlanTests(unittest.TestCase):
    def test_candidate_verified_at_fifty_percent_is_published(self):
        queues = {"deal-review-queue.json": [candidate()]}
        actions, rows, _ = run([], queues, [product(6444455, 19.99, 39.99)])
        self.assertEqual([a[0] for a in actions], ["publish"])
        deal = actions[0][2]
        self.assertEqual((deal["price"], deal["original_price"], deal["verified_date"]), (19.99, 39.99, "2026-10-12"))
        self.assertIn("Best Buy Products API, SKU 6444455", deal["price_evidence"])
        self.assertIn("regularPrice $39.99", deal["price_evidence"])
        feed, new_queues = apply(actions, [], queues, TODAY)
        self.assertEqual(validate_feed(feed, TODAY), (1, 0))
        self.assertEqual(new_queues["deal-review-queue.json"][0]["status"], "published")

    def test_comparable_value_is_not_the_reference(self):
        # The page showed "comparable value $39.99", but Best Buy's regular price is $27.99: only 28% off.
        queues = {"deal-review-queue.json": [candidate()]}
        actions, rows, _ = run([], queues, [product(6444455, 19.99, 27.99)])
        self.assertEqual(actions[0][:3], ("queue-review", ("deal-review-queue.json", 0), "rejected"))
        self.assertIn("only 28%", rows[0]["reason"])

    def test_unavailable_candidate_is_rejected(self):
        for kwargs in ({"online": False}, {"orderable": "SoldOut"}):
            with self.subTest(**kwargs):
                actions, rows, _ = run([], {"q": [candidate()]}, [product(6444455, 9.99, 39.99, **kwargs)])
                self.assertEqual(rows[0]["outcome"], "rejected")
                self.assertIn("not available online", rows[0]["reason"])

    def test_published_deal_reverified_only_from_retailer_data(self):
        feed = [published()]
        actions, rows, _ = run(feed, {}, [product(6444455, 17.99, 39.99)])
        self.assertEqual(rows[0]["outcome"], "reverified")
        new_feed, _ = apply(actions, feed, {}, TODAY)
        self.assertEqual((new_feed[0]["verified_date"], new_feed[0]["price"]), ("2026-10-12", 17.99))
        self.assertEqual(validate_feed(new_feed, TODAY), (1, 0))

    def test_published_deal_that_lost_its_discount_is_archived_not_deleted(self):
        feed = [published(verified="2026-10-11")]
        actions, rows, _ = run(feed, {}, [product(6444455, 29.99, 39.99)])
        self.assertEqual(rows[0]["outcome"], "archive")
        new_feed, _ = apply(actions, feed, {}, TODAY)
        self.assertEqual(len(new_feed), 1)
        self.assertEqual((new_feed[0]["status"], new_feed[0]["verified_date"], new_feed[0]["price"]),
                         ("archived", "2026-10-11", 19.99))  # price and date at time of posting kept
        self.assertEqual(validate_feed(new_feed, TODAY), (0, 1))

    def test_hand_archived_deal_is_never_reactivated(self):
        feed = [published(status="archived")]
        actions, rows, _ = run(feed, {}, [product(6444455, 9.99, 39.99)])
        self.assertEqual((actions, rows[0]["outcome"]), ([], "unchanged"))

    def test_deal_archived_by_verifier_can_be_reactivated_by_a_new_check(self):
        feed = [published(status="archived", archived_by="verify_deals", archived_reason="x")]
        actions, _, _ = run(feed, {}, [product(6444455, 9.99, 39.99)])
        new_feed, _ = apply(actions, feed, {}, TODAY)
        self.assertEqual(new_feed[0]["status"], "active")
        self.assertNotIn("archived_reason", new_feed[0])

    def test_source_outage_changes_nothing(self):
        feed, queues = [published(verified="2026-10-11")], {"q": [candidate(url=BOOK_URL)]}
        actions, rows, _ = run(feed, queues, [], fail=True)
        self.assertEqual(actions, [])
        self.assertEqual({r["outcome"] for r in rows}, {"error"})

    def test_unknown_sku_on_active_deal_archives_it(self):
        actions, rows, _ = run([published(verified="2026-10-11")], {}, [])
        self.assertEqual(rows[0]["outcome"], "archive")

    def test_stores_without_a_source_need_manual_verification(self):
        macys = {"store": "Macy's", "title": "Set", "url": "https://www.macys.com/shop/product/x?ID=1", "status": "needs_checkout_verification"}
        actions, rows, api = run([], {"q": [macys, candidate(url="https://www.bestbuy.com/product/tv/J2FPJK9F77")]}, [])
        self.assertEqual(actions, [])
        self.assertEqual([r["outcome"] for r in rows], ["manual", "manual"])
        self.assertIn("add `sku`", rows[1]["reason"])
        self.assertEqual(api.urls, [])

    def test_model_number_lookup_and_ambiguity(self):
        c = candidate(url="https://www.bestbuy.com/product/hisense/J3Z9Z42HK4", model_number="65S7SG")
        actions, rows, _ = run([], {"q": [c]}, [product(111, 499.99, 999.99, modelNumber="65S7SG")])
        self.assertEqual(rows[0]["outcome"], "publish")
        actions, rows, _ = run([], {"q": [c]}, [product(111, 499.99, 999.99, modelNumber="65S7SG"),
                                                 product(222, 499.99, 999.99, modelNumber="65S7SG")])
        self.assertEqual(rows[0]["outcome"], "not_verified")  # our lookup failed; the retailer did not reject it
        self.assertIn("ambiguous", rows[0]["reason"])
        self.assertEqual(actions[0][2], "not_verified")

    def test_rejected_and_published_queue_items_are_not_rechecked(self):
        queues = {"q": [candidate(status="rejected"), candidate(url=BOOK_URL, status="published")]}
        _, rows, api = run([], queues, [])
        self.assertEqual((rows, api.urls), ([], []))

    def test_run_limit_on_new_deals(self):
        many = [candidate(url=f"https://www.bestbuy.com/site/x/{n}.p?skuId={n}") for n in range(1000001, 1000013)]
        products = [product(n, 10, 30) for n in range(1000001, 1000013)]
        actions, rows, _ = run([], {"q": many}, products)
        self.assertEqual(sum(a[0] == "publish" for a in actions), verify_deals.MAX_NEW_PER_RUN)
        self.assertEqual(sum(r["outcome"] == "deferred" for r in rows), 2)

    def test_api_key_never_in_report_and_requests_are_batched(self):
        queues = {"q": [candidate(), candidate(url=BOOK_URL)]}
        _, rows, api = run([], queues, [product(6444455, 19.99, 39.99), product(6673811, 189, 419)])
        self.assertEqual(len(api.urls), 1)
        self.assertIn("sku%20in(6444455,6673811)", api.urls[0])
        self.assertNotIn("SECRET-KEY", json.dumps(rows))
        self.assertNotIn("SECRET-KEY", verify_deals.render(rows, [], False))


class MainTests(unittest.TestCase):
    def test_dry_run_without_key_changes_no_files(self):
        before = {n: (verify_deals.ROOT / n).read_bytes() for n in ("deals.json", *verify_deals.QUEUES)}
        try:
            code = verify_deals.main(["--apply"], env={}, today=TODAY)
        finally:
            verify_deals.REPORT.unlink(missing_ok=True)
        self.assertEqual(code, 0)
        after = {n: (verify_deals.ROOT / n).read_bytes() for n in before}
        self.assertEqual(before, after)

    def test_repository_best_buy_candidates_have_lookup_ids(self):
        bb = BestBuy("k")
        queues = [json.loads((verify_deals.ROOT / n).read_text()) for n in verify_deals.QUEUES]
        items = [c for q in queues for c in q if c.get("store") == "Best Buy"]
        with_ids = [c["title"] for c in items if bb.ident(c)]
        self.assertGreaterEqual(len(with_ids), 3, with_ids)


if __name__ == "__main__":
    unittest.main()

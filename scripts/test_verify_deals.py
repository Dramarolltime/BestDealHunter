"""Retailer verification tests with recorded-style fixtures; no network, keys or real retailers."""
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from urllib import parse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_deals  # noqa: E402
from publication_gate import card_id  # noqa: E402
from verify_deals import BestBuy, Renderer, SourceError, apply, plan  # noqa: E402
from validate_deals import validate_feed  # noqa: E402

TODAY = date(2026, 10, 12)
NOW = "2026-10-12 16:00 UTC"
GAME_URL = "https://www.bestbuy.com/product/it-takes-two-playstation-5-playstation-4/J7CYF9YKJ5/sku/6444455"
BOOK_URL = "https://www.bestbuy.com/product/lenovo-chromebook/JJGH3QCJKR/sku/6673811"
BLOCKER = "Product has approximately 3.0/5 customer rating; editorial quality review recommended"


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


class FakeRenderer(Renderer):
    """Writes a placeholder file per card and records what it rendered (and the prices printed)."""

    def __init__(self, root, available=True, fail=False):
        super().__init__(root, render=lambda deal, kind: (kind, deal["price"], deal["verified_date"]),
                         save=self._save)
        self.available, self.fail, self.rendered = available, fail, []

    def _save(self, card, path):
        if self.fail:
            raise OSError("disk full")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(json.dumps(card).encode())
        self.rendered.append(card)
        return path


def candidate(url=GAME_URL, **extra):
    return {"status": "needs_checkout_verification", "store": "Best Buy", "title": "It Takes Two — PS5/PS4",
            "url": url, "listed_price": 19.99, "publication_eligible": True, **extra}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "images").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def published(self, url=GAME_URL, verified="2026-10-09", price=19.99, original=39.99, card=True, **extra):
        image = f"images/generated/deal-{card_id(url)}-square.jpg" if card else "images/photo.jpg"
        path = self.root / image
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        return {"store": "Best Buy", "title": "It Takes Two", "category": "Electronics", "url": url, "price": price,
                "original_price": original, "verified_date": verified, "source": "Best Buy page",
                "price_evidence": "Checked page", "image": image, **extra}

    def run_plan(self, feed, queues, products, fail=False, renderer=None):
        api = FakeBestBuyAPI(products, fail)
        self.renderer = renderer or FakeRenderer(self.root)
        sources = {"Best Buy": BestBuy("SECRET-KEY", transport=api)}
        actions, rows = plan(feed, queues, sources, TODAY, NOW, self.renderer, self.root)
        return actions, rows, api

    def apply(self, actions, feed, queues):
        return apply(actions, feed, queues, TODAY, self.renderer, self.root)


class PublishTests(Base):
    def test_clean_candidate_verified_at_fifty_percent_is_published_with_its_card(self):
        queues = {"q": [candidate()]}
        actions, rows, _ = self.run_plan([], queues, [product(6444455, 19.99, 39.99)])
        self.assertEqual([r["outcome"] for r in rows], ["publish"])
        feed, new_queues = self.apply(actions, [], queues)
        deal = feed[0]
        self.assertEqual((deal["price"], deal["original_price"], deal["verified_date"]), (19.99, 39.99, "2026-10-12"))
        self.assertEqual(deal["reference_price_label"], "Best Buy regular price")
        self.assertIn("Best Buy Products API, SKU 6444455", deal["price_evidence"])
        self.assertTrue((self.root / deal["image"]).is_file())
        self.assertEqual(deal["image"], f"images/generated/deal-{card_id(GAME_URL)}-square.jpg")
        self.assertEqual(sorted(c[0] for c in self.renderer.rendered), ["feed", "square"])
        self.assertEqual(validate_feed(feed, TODAY), (1, 0))
        self.assertEqual(new_queues["q"][0]["status"], "published")

    def test_comparable_value_is_not_the_reference(self):
        queues = {"q": [candidate()]}
        actions, rows, _ = self.run_plan([], queues, [product(6444455, 19.99, 27.99)])
        self.assertEqual(actions[0][:3], ("queue-review", ("q", 0), "rejected"))
        self.assertIn("only 28%", rows[0]["reason"])

    def test_unavailable_candidate_is_rejected(self):
        for kwargs in ({"online": False}, {"orderable": "SoldOut"}):
            with self.subTest(**kwargs):
                _, rows, _ = self.run_plan([], {"q": [candidate()]}, [product(6444455, 9.99, 39.99, **kwargs)])
                self.assertEqual(rows[0]["outcome"], "rejected")
                self.assertIn("not available online", rows[0]["reason"])

    def test_model_number_lookup_links_to_the_verified_sku(self):
        c = candidate(url="https://www.bestbuy.com/product/hisense/J3Z9Z42HK4", model_number="65S7SG")
        actions, rows, _ = self.run_plan([], {"q": [c]}, [product(111, 499.99, 999.99, modelNumber="65S7SG")])
        self.assertEqual(rows[0]["outcome"], "publish")
        self.assertEqual(actions[0][2]["url"], "https://www.bestbuy.com/site/111.p?skuId=111")
        actions, rows, _ = self.run_plan([], {"q": [c]}, [product(111, 499.99, 999.99, modelNumber="65S7SG"),
                                                           product(222, 499.99, 999.99, modelNumber="65S7SG")])
        self.assertEqual(rows[0]["outcome"], "not_verified")  # our lookup failed; the retailer did not reject it
        self.assertIn("ambiguous", rows[0]["reason"])

    def test_run_limit_on_new_deals(self):
        many = [candidate(url=f"https://www.bestbuy.com/site/x/{n}.p?skuId={n}") for n in range(1000001, 1000013)]
        products = [product(n, 10, 30) for n in range(1000001, 1000013)]
        actions, rows, _ = self.run_plan([], {"q": many}, products)
        self.assertEqual(sum(a[0] == "publish" for a in actions), verify_deals.MAX_NEW_PER_RUN)
        self.assertEqual(sum(r["outcome"] == "deferred" for r in rows), 2)


class PublicationSafetyTests(Base):
    """Regression tests: a verified price alone never publishes a deal."""

    def test_unresolved_blocker_holds_a_price_verified_candidate(self):
        queues = {"q": [candidate(blockers=[BLOCKER])]}
        actions, rows, _ = self.run_plan([], queues, [product(6444455, 19.99, 39.99)])
        self.assertEqual(rows[0]["outcome"], "held")
        self.assertIn("unresolved blocker: Product has approximately 3.0/5", rows[0]["reason"])
        feed, new_queues = self.apply(actions, [], queues)
        self.assertEqual(feed, [])
        review = new_queues["q"][0]["last_review"]
        self.assertEqual(review["result"], "verified_but_held")
        self.assertEqual(review["retailer_check"]["price"], 19.99)  # reviewer sees the verified facts
        self.assertNotEqual(new_queues["q"][0].get("status"), "published")
        self.assertEqual(self.renderer.rendered, [])  # no card is made for a held deal

    def test_explicit_hold_blocks_even_without_blockers(self):
        _, rows, _ = self.run_plan([], {"q": [candidate(publication_eligible=False)]}, [product(6444455, 19.99, 39.99)])
        self.assertEqual(rows[0]["outcome"], "held")
        self.assertIn("not marked publication_eligible true", rows[0]["reason"])

    def test_human_resolution_of_every_blocker_allows_publishing(self):
        c = candidate(blockers=[BLOCKER], publication_eligible=True,
                      resolved_blockers={BLOCKER: {"by": "Alpha", "note": "Accepted: 4.1/5 on current reviews"}})
        _, rows, _ = self.run_plan([], {"q": [c]}, [product(6444455, 19.99, 39.99)])
        self.assertEqual(rows[0]["outcome"], "publish")

    def test_partial_or_automated_resolutions_do_not_count(self):
        other = "Final checkout total not verified"
        cases = {
            "one of two resolved": {BLOCKER: {"by": "Alpha", "note": "ok"}},
            "resolved by automation": {BLOCKER: {"by": "verify_deals", "note": "ok"}, other: {"by": "github-actions[bot]", "note": "ok"}},
            "missing note": {BLOCKER: {"by": "Alpha"}, other: {"by": "Alpha", "note": " "}},
            "resolution for different text": {BLOCKER.upper(): {"by": "Alpha", "note": "ok"}, other: {"by": "Alpha", "note": "ok"}},
        }
        for name, resolved in cases.items():
            with self.subTest(name):
                c = candidate(blockers=[BLOCKER, other], publication_eligible=True, resolved_blockers=resolved)
                _, rows, _ = self.run_plan([], {"q": [c]}, [product(6444455, 19.99, 39.99)])
                self.assertEqual(rows[0]["outcome"], "held")

    def test_missing_eligibility_or_do_not_publish_note_holds(self):
        for extra in ({"publication_eligible": None}, {"notes": "Research only. Do not publish until checked."}):
            with self.subTest(extra=extra):
                _, rows, _ = self.run_plan([], {"q": [candidate(**extra)]}, [product(6444455, 19.99, 39.99)])
                self.assertEqual(rows[0]["outcome"], "held")

    def test_eligible_flag_does_not_override_open_blockers(self):
        c = candidate(blockers=[BLOCKER], publication_eligible=True)
        _, rows, _ = self.run_plan([], {"q": [c]}, [product(6444455, 19.99, 39.99)])
        self.assertEqual(rows[0]["outcome"], "held")

    def test_no_card_renderer_means_no_image_means_held(self):
        renderer = FakeRenderer(self.root, available=False)
        queues = {"q": [candidate()]}
        actions, rows, _ = self.run_plan([], queues, [product(6444455, 19.99, 39.99)], renderer=renderer)
        self.assertEqual(rows[0]["outcome"], "held")
        self.assertIn("no image", rows[0]["reason"])
        feed, _ = self.apply(actions, [], queues)
        self.assertEqual(feed, [])

    def test_failed_card_render_never_publishes(self):
        renderer = FakeRenderer(self.root, fail=True)
        queues = {"q": [candidate()]}
        actions, rows, _ = self.run_plan([], queues, [product(6444455, 19.99, 39.99)], renderer=renderer)
        self.assertEqual(rows[0]["outcome"], "publish")
        feed, new_queues = self.apply(actions, [], queues)
        self.assertEqual(feed, [])
        self.assertIn("card render failed", new_queues["q"][0]["last_review"]["reason"])

    def test_apply_rechecks_the_gate_if_a_blocker_appears_after_planning(self):
        queues = {"q": [candidate()]}
        actions, _, _ = self.run_plan([], queues, [product(6444455, 19.99, 39.99)])
        queues["q"][0]["blockers"] = [BLOCKER]  # e.g. a reviewer added a concern concurrently
        feed, new_queues = self.apply(actions, [], queues)
        self.assertEqual(feed, [])
        self.assertEqual(new_queues["q"][0]["last_review"]["result"], "verified_but_held")

    def test_repository_research_candidates_cannot_auto_publish(self):
        # Every current research candidate carries blockers and publication_eligible false; even a
        # perfect retailer answer must not publish any of them without a reviewer.
        queues = {n: json.loads((verify_deals.ROOT / n).read_text()) for n in verify_deals.QUEUES}
        bb = BestBuy("k")
        products = []
        for q in queues.values():
            for c in q:
                ident = bb.ident(c) if c.get("store") == "Best Buy" else None
                if ident:
                    products.append(product(ident[1] if ident[0] == "sku" else 999, 10, 100,
                                            **({"modelNumber": ident[1]} if ident[0] == "modelNumber" else {})))
        actions, rows, _ = self.run_plan([], queues, products)
        self.assertFalse([a for a in actions if a[0] == "publish"])
        self.assertGreaterEqual(sum(r["outcome"] == "held" for r in rows), 3)


class ReverifyTests(Base):
    def test_published_deal_reverified_with_fresh_card(self):
        feed = [self.published()]
        actions, rows, _ = self.run_plan(feed, {}, [product(6444455, 17.99, 39.99)])
        self.assertEqual(rows[0]["outcome"], "reverified")
        new_feed, _ = self.apply(actions, feed, {})
        self.assertEqual((new_feed[0]["verified_date"], new_feed[0]["price"]), ("2026-10-12", 17.99))
        self.assertIn(("square", 17.99, "2026-10-12"), self.renderer.rendered)  # card shows the new price
        self.assertEqual(validate_feed(new_feed, TODAY), (1, 0))

    def test_price_change_without_renderer_archives_instead_of_showing_a_stale_card(self):
        renderer = FakeRenderer(self.root, available=False)
        feed = [self.published(verified="2026-10-11", card=False)]
        actions, rows, _ = self.run_plan(feed, {}, [product(6444455, 17.99, 39.99)], renderer=renderer)
        self.assertEqual(rows[0]["outcome"], "archive")
        new_feed, _ = self.apply(actions, feed, {})
        self.assertEqual((new_feed[0]["status"], new_feed[0]["price"]), ("archived", 19.99))
        self.assertIn("price changed to $17.99", new_feed[0]["archived_reason"])

    def test_same_price_without_renderer_keeps_existing_image_and_reverifies(self):
        renderer = FakeRenderer(self.root, available=False)
        feed = [self.published(card=False)]
        actions, rows, _ = self.run_plan(feed, {}, [product(6444455, 19.99, 39.99)], renderer=renderer)
        self.assertEqual(rows[0]["outcome"], "reverified")
        new_feed, _ = self.apply(actions, feed, {})
        self.assertEqual((new_feed[0]["image"], new_feed[0]["verified_date"]), ("images/photo.jpg", "2026-10-12"))

    def test_active_deal_whose_image_vanished_is_archived(self):
        renderer = FakeRenderer(self.root, available=False)
        deal = self.published(verified="2026-10-11", card=False)
        (self.root / deal["image"]).unlink()
        actions, rows, _ = self.run_plan([deal], {}, [product(6444455, 19.99, 39.99)], renderer=renderer)
        self.assertEqual(rows[0]["outcome"], "archive")
        self.assertIn("does not exist", rows[0]["reason"])

    def test_published_deal_that_lost_its_discount_is_archived_not_deleted(self):
        feed = [self.published(verified="2026-10-11")]
        actions, rows, _ = self.run_plan(feed, {}, [product(6444455, 29.99, 39.99)])
        self.assertEqual(rows[0]["outcome"], "archive")
        new_feed, _ = self.apply(actions, feed, {})
        self.assertEqual(len(new_feed), 1)
        self.assertEqual((new_feed[0]["status"], new_feed[0]["verified_date"], new_feed[0]["price"]),
                         ("archived", "2026-10-11", 19.99))  # price and date at time of posting kept
        self.assertEqual(validate_feed(new_feed, TODAY), (0, 1))

    def test_hand_archived_deal_is_never_reactivated(self):
        actions, rows, _ = self.run_plan([self.published(status="archived")], {}, [product(6444455, 9.99, 39.99)])
        self.assertEqual((actions, rows[0]["outcome"]), ([], "unchanged"))

    def test_deal_archived_by_verifier_can_be_reactivated_by_a_new_check(self):
        feed = [self.published(status="archived", archived_by="verify_deals", archived_reason="x")]
        actions, _, _ = self.run_plan(feed, {}, [product(6444455, 9.99, 39.99)])
        new_feed, _ = self.apply(actions, feed, {})
        self.assertEqual(new_feed[0]["status"], "active")
        self.assertNotIn("archived_reason", new_feed[0])

    def test_source_outage_changes_nothing(self):
        feed, queues = [self.published(verified="2026-10-11")], {"q": [candidate(url=BOOK_URL)]}
        actions, rows, _ = self.run_plan(feed, queues, [], fail=True)
        self.assertEqual(actions, [])
        self.assertEqual({r["outcome"] for r in rows}, {"error"})

    def test_unknown_sku_on_active_deal_archives_it(self):
        _, rows, _ = self.run_plan([self.published(verified="2026-10-11")], {}, [])
        self.assertEqual(rows[0]["outcome"], "archive")


class LookupTests(Base):
    def test_stores_without_a_source_need_manual_verification(self):
        macys = {"store": "Macy's", "title": "Set", "url": "https://www.macys.com/shop/product/x?ID=1", "status": "needs_checkout_verification"}
        actions, rows, api = self.run_plan([], {"q": [macys, candidate(url="https://www.bestbuy.com/product/tv/J2FPJK9F77")]}, [])
        self.assertEqual(actions, [])
        self.assertEqual([r["outcome"] for r in rows], ["manual", "manual"])
        self.assertIn("add `sku`", rows[1]["reason"])
        self.assertEqual(api.urls, [])

    def test_rejected_published_and_already_listed_candidates_are_not_rechecked(self):
        listed = self.published(url="https://www.bestbuy.com/site/5555555.p?skuId=5555555", sku="5555555")
        queues = {"q": [candidate(status="rejected"), candidate(url=BOOK_URL, status="published"),
                        candidate(url="https://www.bestbuy.com/product/other/ABC/sku/7777777", sku="5555555")]}
        _, rows, _ = self.run_plan([listed], queues, [product(5555555, 10, 30)])
        self.assertEqual([r["origin"] for r in rows], ["feed"])

    def test_api_key_never_in_report_and_requests_are_batched(self):
        queues = {"q": [candidate(), candidate(url=BOOK_URL)]}
        _, rows, api = self.run_plan([], queues, [product(6444455, 19.99, 39.99), product(6673811, 189, 419)])
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

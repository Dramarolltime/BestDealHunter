"""Buffer publisher tests: eligibility, captions, dedupe and kill switch. No network."""
import os
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish_buffer as pb  # noqa: E402

TODAY = date(2026, 10, 10)
CARD = "images/generated/deal-0123456789ab-feed.jpg"
HOSTED = [{"image": CARD, "cloudinary_url": "https://res.cloudinary.com/x/image/upload/v1/bestdealhunter/abc.jpg"}]


def deal(**changes):
    d = {"store": "Best Buy", "title": "ILLUSTRATIVE TEST TV — NOT A REAL DEAL", "category": "Electronics",
         "url": "https://www.bestbuy.com/site/test", "price": 299.99, "original_price": 699.99,
         "verified_date": "2026-10-10", "source": "Test", "price_evidence": "Test",
         "image": CARD, "publication_approved": True}
    d.update(changes)
    return d


class EligibilityTests(unittest.TestCase):
    def check(self, d, posts=()):
        return pb.eligibility(d, pb.hosted_urls(HOSTED), {p for p in posts}, TODAY)

    def test_fully_ready_deal_is_eligible(self):
        self.assertEqual(self.check(deal()), (True, "eligible"))

    def test_each_gate_blocks(self):
        cases = {
            "fails validation": deal(verified_date="2026-10-01"),
            "not approved": deal(publication_approved="yes"),
            "Amazon": deal(store="Amazon", url="https://www.amazon.com/dp/X"),
            "not a generated branded card": deal(image="images/IMG_4236.jpeg"),
            "not hosted": deal(image="images/generated/deal-ffffffffffff-feed.jpg"),
            "fails validation (": deal(verified_date="2026-10-07"),  # archived deals never post
        }
        for expected, d in cases.items():
            ok, reason = self.check(d)
            self.assertFalse(ok, expected)
            self.assertIn(expected, reason)

    def test_square_site_image_posts_its_feed_card(self):
        square = "images/generated/deal-0123456789ab-square.jpg"
        self.assertEqual(self.check(deal(image=square)), (True, "eligible"))
        ready, _ = pb.plan([deal(image=square)], HOSTED, [], TODAY)
        self.assertEqual(ready[0]["image_url"], HOSTED[0]["cloudinary_url"])

    def test_png_or_foreign_host_is_refused(self):
        for url in ("https://res.cloudinary.com/x/a.png", "https://evil.example/a.jpg"):
            hosted = {CARD: url}
            self.assertFalse(pb.eligibility(deal(), hosted, set(), TODAY)[0])

    def test_dedupe_by_url_and_price(self):
        key = pb.post_key(deal())
        self.assertFalse(self.check(deal(), posts=[key])[0])
        self.assertTrue(self.check(deal(price=279.99), posts=[key])[0])  # a new price may be posted


class CaptionTests(unittest.TestCase):
    def test_caption_uses_deal_numbers_and_disclosures(self):
        text = pb.caption(deal())
        for expected in ("57% OFF at Best Buy", "$299.99", "Comparable value $699.99", "Save $400",
                         "Price verified Oct 10, 2026", "#ad", "may earn a commission", "Link in bio"):
            self.assertIn(expected, text)
        self.assertLessEqual(len(text), pb.CAPTION_LIMIT)


class PlanTests(unittest.TestCase):
    def test_plan_limits_posts_and_records_keys(self):
        deals = [deal(), deal(url="https://www.bestbuy.com/site/second")]
        hosted = HOSTED
        ready, skipped = pb.plan(deals, hosted, [], TODAY)
        self.assertEqual(len(ready), 1)
        self.assertIn("run limit", skipped[0][1])
        self.assertEqual(ready[0]["channel"], "best_dealhunter")

    def test_already_posted_deal_is_skipped(self):
        ready, skipped = pb.plan([deal()], HOSTED, [{"key": pb.post_key(deal()), "status": "sent"}], TODAY)
        self.assertEqual(ready, [])
        self.assertIn("already queued or posted", skipped[0][1])

    def test_live_repository_deals_are_not_eligible(self):
        import json
        root = Path(__file__).resolve().parents[1]
        deals = json.loads((root / "deals.json").read_text())
        mapping = json.loads((root / "cloudinary-images.json").read_text())
        ready, _ = pb.plan(deals, mapping, [], TODAY)
        self.assertEqual(ready, [])


class KillSwitchTests(unittest.TestCase):
    def run_main(self, env, argv):
        with mock.patch.dict(os.environ, env, clear=False), mock.patch("sys.stdout"):
            os.environ.pop("GITHUB_STEP_SUMMARY", None)
            return pb.main(argv)

    def test_dry_run_is_default_and_succeeds(self):
        self.assertEqual(self.run_main({}, []), 0)

    def test_live_refused_when_kill_switch_off(self):
        self.assertEqual(self.run_main({"INSTAGRAM_PUBLISH_ENABLED": "false", "BUFFER_API_KEY": "x"}, ["--live"]), 2)

    def test_live_refused_without_secret(self):
        with mock.patch.dict(os.environ, {"INSTAGRAM_PUBLISH_ENABLED": "true"}):
            os.environ.pop("BUFFER_API_KEY", None)
            with mock.patch("sys.stdout"):
                self.assertEqual(pb.main(["--live"]), 2)

    def test_live_refused_without_channel(self):
        self.assertEqual(self.run_main({"INSTAGRAM_PUBLISH_ENABLED": "true", "BUFFER_API_KEY": "x"}, ["--live"]), 2)

    def test_dry_run_approval_cannot_be_combined_with_live(self):
        self.assertEqual(self.run_main({}, ["--live", "--approve-for-dry-run", "abc"]), 2)
        self.assertEqual(self.run_main({}, ["--draft-check", "0123456789ab", "--approve-for-dry-run", "abc"]), 2)

    def test_live_and_draft_check_are_mutually_exclusive(self):
        with mock.patch("sys.stderr"):
            self.assertEqual(self.run_main({}, ["--live", "--draft-check", "0123456789ab"]), 2)

    def test_post_log_starts_empty(self):
        import json
        self.assertEqual(json.loads(pb.POSTS.read_text()), [])


class FakeBuffer:
    """Stands in for Buffer's GraphQL endpoint; records every request."""

    def __init__(self, fail=None, status="sent"):
        self.requests, self.fail, self.status = [], fail, status

    def __call__(self, payload):
        self.requests.append(payload)
        if self.fail == "transport":
            raise TimeoutError("network timeout")
        if "createPost" in payload["query"]:
            if self.fail == "mutation":
                return {"data": {"createPost": {"__typename": "InvalidInputError", "message": "image URL not reachable"}}}
            return {"data": {"createPost": {"__typename": "PostActionSuccess",
                                            "post": {"id": f"p{len(self.requests)}", "status": "scheduled"}}}}
        return {"data": {"post": {"id": payload["variables"]["input"]["id"], "status": self.status}}}


class PublishLedgerTests(unittest.TestCase):
    def ready(self):
        return pb.plan([deal()], HOSTED, [], TODAY)[0]

    def test_post_is_sent_with_image_url_and_caption(self):
        fake, posts = FakeBuffer(), []
        rows = pb.publish(self.ready(), posts, pb.BufferClient("k", fake), "chan1", "now", log=lambda *_: None)
        sent = fake.requests[0]["variables"]["input"]
        self.assertEqual((sent["channelId"], sent["assets"][0]["image"]["url"]), ("chan1", HOSTED[0]["cloudinary_url"]))
        self.assertIn("#ad", sent["text"])
        self.assertEqual((rows[0]["status"], rows[0]["buffer_post_id"]), ("queued", "p1"))

    def test_same_deal_can_never_be_queued_twice(self):
        fake, posts = FakeBuffer(), []
        client = pb.BufferClient("k", fake)
        pb.publish(self.ready(), posts, client, "c", "t1", log=lambda *_: None)
        # 1) re-planning from the ledger skips it
        ready, skipped = pb.plan([deal()], HOSTED, posts, TODAY)
        self.assertEqual(ready, [])
        self.assertIn("already queued or posted", skipped[0][1])
        # 2) even a stale plan replayed against the ledger is blocked before any API call
        stale_plan = pb.plan([deal()], HOSTED, [], TODAY)[0]
        pb.publish(stale_plan, posts, client, "c", "t2", log=lambda *_: None)
        self.assertEqual(len([r for r in fake.requests if "createPost" in r["query"]]), 1)
        self.assertEqual(len(posts), 1)

    def test_reservation_survives_errors_and_blocks_retry(self):
        for failure in ("transport", "mutation"):
            posts = []
            pb.publish(self.ready(), posts, pb.BufferClient("k", FakeBuffer(fail=failure)), "c", "t", log=lambda *_: None)
            self.assertEqual(posts[0]["status"], "error")
            self.assertEqual(pb.plan([deal()], HOSTED, posts, TODAY)[0], [])  # no automatic retry

    def test_live_post_uses_automatic_queue_without_draft_flag(self):
        fake = FakeBuffer()
        pb.publish(self.ready(), [], pb.BufferClient("k", fake), "c", "t", log=lambda *_: None)
        sent = fake.requests[0]["variables"]["input"]
        self.assertNotIn("saveToDraft", sent)
        self.assertEqual((sent["schedulingType"], sent["mode"]), ("automatic", "addToQueue"))
        self.assertEqual(sent["metadata"], {"instagram": {"type": "post", "shouldShareToFeed": True}})

    def test_sent_only_when_buffer_reports_it(self):
        posts = [{"key": "k1", "status": "queued", "buffer_post_id": "p9"}]
        pb.refresh_statuses(posts, pb.BufferClient("k", FakeBuffer(status="scheduled")))
        self.assertEqual(posts[0]["status"], "queued")
        pb.refresh_statuses(posts, pb.BufferClient("k", FakeBuffer(status="sent")))
        self.assertEqual(posts[0]["status"], "sent")

    def test_graphql_errors_raise(self):
        client = pb.BufferClient("k", lambda payload: {"errors": [{"message": "Unauthorized"}]})
        with self.assertRaises(pb.BufferError):
            client.create_post("c", "t", "https://res.cloudinary.com/x.jpg")

    def test_dry_run_approval_keeps_other_gates(self):
        amazon = deal(store="Amazon", url="https://www.amazon.com/dp/X", publication_approved=False)
        from deal_graphics import card_id
        approved = pb.apply_dry_run_approval([amazon], {card_id(amazon)})
        self.assertTrue(approved[0]["publication_approved"])
        self.assertFalse(amazon["publication_approved"])  # original data untouched
        self.assertIn("Amazon", pb.plan(approved, HOSTED, [], TODAY)[1][0][1])


if __name__ == "__main__":
    unittest.main()

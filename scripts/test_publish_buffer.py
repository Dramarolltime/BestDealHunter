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

    def test_amazon_needs_an_amazon_api_price_source(self):
        amazon = deal(store="Amazon", url="https://www.amazon.com/dp/X")
        ok, reason = self.check(amazon)
        self.assertFalse(ok)
        self.assertIn("Creators API", reason)
        for source in ("retailer_page_check", "human_browser_check", None):
            self.assertFalse(self.check(dict(amazon, evidence_method=source))[0])
        self.assertEqual(self.check(dict(amazon, evidence_method="amazon_creators_api")), (True, "eligible"))

    def test_amazon_caption_carries_the_associates_statement_and_time_stamp(self):
        text = pb.caption(deal(store="Amazon", url="https://www.amazon.com/dp/X?tag=bestdealhun0b-20",
                               auto_published_at="2026-10-10T18:05:00+00:00"))
        self.assertIn("As an Amazon Associate I earn from qualifying purchases.", text)
        self.assertIn("Price checked Oct 10, 2026, 11:05 AM PT", text)
        self.assertIn("Amazon's price at checkout applies", text)

    def test_each_gate_blocks(self):
        cases = {
            "fails validation": deal(verified_date="2026-10-01"),
            "not approved": deal(publication_approved="yes"),
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
        for expected in ("ILLUSTRATIVE TEST TV", "$299.99 at Best Buy", "57% off the comparable value of $699.99",
                         "you save $400", "bestdealhunters.com", "Link in bio", "Price checked Oct 10, 2026",
                         "Prices and stock can change", "#TechDeals"):
            self.assertIn(expected, text)
        self.assertTrue(text.startswith("ILLUSTRATIVE TEST TV"))  # owner, 2026-10-23: plain, no emoji headline
        self.assertNotIn("🔥", text)
        self.assertLessEqual(text.count("#"), 3)  # a few matching hashtags, not a wall of them
        self.assertNotIn("#ad", text)  # a direct link earns nothing: no ad / commission claim
        self.assertLessEqual(len(text), pb.CAPTION_LIMIT)

    def test_hashtags_match_the_product(self):
        self.assertIn("#TechDeals", pb.hashtags(deal()))
        self.assertIn("#KitchenDeals", pb.hashtags(deal(category="Home", title="Silicone Oven Mitt 2-Pack")))
        self.assertIn("#HomeDeals", pb.hashtags(deal(category="Home", title="Queen Bed Sheet Set")))
        self.assertIn("#FashionDeals", pb.hashtags(deal(category="Fashion")))
        self.assertIn("#DealAlerts", pb.hashtags(deal(category="Other")))
        for tags in pb.HASHTAGS.values():
            self.assertTrue(5 <= tags.count("#") <= 8, tags)

    def test_hashtags_name_the_brand_and_product_and_vary(self):
        tags = pb.hashtags(deal(store="eBay", category="Fashion", title="adidas men Supernova Ease Running Sneakers",
                                url="https://www.ebay.com/itm/1"))
        for tag in ("#Adidas", "#SneakerDeals", "#FashionDeals", "#eBayFinds", "#BestDealHunter"):
            self.assertIn(tag, tags)
        self.assertEqual(pb.brand_tag("Levi's Men's 505 Jeans"), "#Levis")
        self.assertEqual(pb.brand_tag("Mens Under Armour UA Rival Fleece"), "#UnderArmour")
        self.assertIsNone(pb.brand_tag("Generic Fleece Throw"))
        titles = ["adidas Ultraboost Running Shoes", "Levi's 505 Jeans", "Under Armour Rival Fleece Hoodie",
                  "Generic Fleece Throw", "Nike Air Max Sneakers"]
        mixes = {pb.hashtags(deal(category="Fashion", title=t, url=f"https://www.ebay.com/itm/{i}"))
                 for i, t in enumerate(titles)}
        self.assertGreater(len(mixes), 3)
        self.assertTrue(all(t.count("#") <= 5 for t in mixes))  # Instagram: at most 5 hashtags per post

    def test_every_caption_stays_within_five_hashtags_including_the_ad_tags(self):
        for d in (deal(), deal(store="eBay", url="https://www.ebay.com/itm/1?campid=5339220016&mkcid=1",
                               affiliate_url="https://www.ebay.com/itm/1?campid=5339220016&mkcid=1")):
            with self.subTest(d.get("url")):
                self.assertLessEqual(pb.caption(d).count("#"), 5)

    def test_caption_leads_with_the_product_and_its_verified_numbers(self):
        lines = pb.caption(deal()).split("\n")
        self.assertEqual(lines[0], "ILLUSTRATIVE TEST TV — NOT A REAL DEAL")
        self.assertEqual(lines[2], "$299.99 at Best Buy (57% off the comparable value of $699.99, you save $400).")

    def test_affiliate_link_gets_the_ad_disclosure(self):
        text = pb.caption(deal(url="https://www.bestbuy.com/site/test?irclickid=abc"))
        self.assertIn("#ad #affiliate", text)
        self.assertNotIn("may earn a commission", text)  # owner: tags only, no commission sentence


class PlanTests(unittest.TestCase):
    def test_plan_limits_posts_and_records_keys(self):
        deals = [deal(), deal(url="https://www.bestbuy.com/site/second")]
        hosted = HOSTED
        ready, skipped = pb.plan(deals, hosted, [], TODAY)
        self.assertEqual(len(ready), 1)
        self.assertIn("run limit", skipped[0][1])
        self.assertEqual(ready[0]["channel"], "best_dealhunter")

    def test_found_deals_post_only_with_a_picture_of_the_real_product(self):
        ebay = dict(deal(), store="eBay", url="https://www.ebay.com/itm/123", approved_by="automatic: eBay API")
        form = dict(deal(), approved_by="owner deal form: https://github.com/o/r/issues/1")
        self.assertIn("real listing photo", pb.eligibility(form, pb.hosted_urls(HOSTED), set(), TODAY)[1])
        self.assertEqual(pb.eligibility(dict(form, card_source="submitted"), pb.hosted_urls(HOSTED), set(), TODAY),
                         (True, "eligible"))  # Alpha's image, numbers confirmed by the submitter
        check = lambda d: pb.eligibility(d, pb.hosted_urls(HOSTED), set(), TODAY)
        self.assertIn("real listing photo", check(ebay)[1])
        self.assertEqual(check(dict(ebay, image_source_kind="listing_photo")), (True, "eligible"))
        # owner, 2026-10-23: only the real listing photo is product-accurate enough for Instagram
        self.assertIn("real listing photo", check(dict(ebay, image_source_kind="ai_scene_from_photo"))[1])
        self.assertIn("real listing photo", check(dict(ebay, image_source_kind="ai_scene"))[1])

    def test_one_post_a_day_and_one_deal_per_post(self):
        self.assertEqual((pb.MAX_POSTS_PER_DAY, pb.MIN_HOURS_BETWEEN_POSTS), (1, 24))
        self.assertIs(pb.RULES.get("instagram_roundups"), False)  # owner, 2026-10-23: no Top N roundups
        deals = [deal(url=f"https://www.bestbuy.com/site/t{i}") for i in range(5)]
        ready, _ = pb.plan(deals, HOSTED, [], TODAY)
        self.assertEqual((len(ready), ready[0].get("kind")), (1, None))

    def test_a_retailer_other_than_ebay_is_picked_first(self):
        ebay = deal(store="eBay", url="https://www.ebay.com/itm/1")
        ready, _ = pb.plan([ebay, deal()], HOSTED, [], TODAY)
        self.assertEqual(ready[0]["deal"]["store"], "Best Buy")
        ready, _ = pb.plan([ebay], HOSTED, [], TODAY)
        self.assertEqual(ready[0]["deal"]["store"], "eBay")  # eBay is still used when it is the only one

    def test_instagram_stays_off_until_the_owner_turns_it_on(self):
        self.assertIs(pb.RULES.get("instagram_auto_publish"), False)
        env = {"INSTAGRAM_PUBLISH_ENABLED": "true", "BUFFER_API_KEY": "x", "BUFFER_CHANNEL_ID": "c"}
        with mock.patch.dict(os.environ, env), mock.patch.object(pb, "BufferClient") as client, \
                mock.patch.object(pb.POSTS.__class__, "write_text") as write:
            self.assertEqual(pb.main(["--live"]), 0)
        client.assert_not_called()  # Buffer is never contacted
        write.assert_not_called()

    def test_daily_post_limit(self):
        from datetime import datetime, timezone
        now = datetime(2026, 10, 10, 20, 0, tzinfo=timezone.utc)
        posts = [{"key": f"{i:016x}", "status": "sent", "reserved_at": f"2026-10-10T{8 + i % 12:02d}:00:00+00:00"}
                 for i in range(pb.MAX_POSTS_PER_DAY)]
        ready, skipped = pb.plan([deal()], HOSTED, posts, TODAY, now=now)
        self.assertEqual(ready, [])
        self.assertIn("daily limit", skipped[0][1])

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

    def test_post_log_records_are_well_formed(self):
        import json
        for post in json.loads(pb.POSTS.read_text()):
            self.assertRegex(post["key"], r"^[0-9a-f]{16}$")
            self.assertIn(post["status"], {"reserved", "locked", "sent", "error", "failed", "queued", "scheduled", "sending"})


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
        self.assertIn("Price checked", sent["text"])
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

    def test_live_post_is_shared_now_without_draft_flag(self):
        fake = FakeBuffer()
        pb.publish(self.ready(), [], pb.BufferClient("k", fake), "c", "t", log=lambda *_: None)
        sent = fake.requests[0]["variables"]["input"]
        self.assertNotIn("saveToDraft", sent)
        # shareNow: posted while the price check is minutes old, not at a later queue slot
        self.assertEqual((sent["schedulingType"], sent["mode"]), ("automatic", "shareNow"))
        self.assertEqual(sent["metadata"], {"instagram": {"type": "post", "shouldShareToFeed": True}})

    def test_sent_only_when_buffer_reports_it(self):
        posts = [{"key": "k1", "status": "queued", "buffer_post_id": "p9"}]
        pb.refresh_statuses(posts, pb.BufferClient("k", FakeBuffer(status="scheduled")))
        self.assertEqual(posts[0]["status"], "queued")
        pb.refresh_statuses(posts, pb.BufferClient("k", FakeBuffer(status="sent")))
        self.assertEqual(posts[0]["status"], "sent")

    def test_status_is_asked_once_per_buffer_post_and_errors_are_final(self):
        posts = [{"key": f"k{i}", "status": "queued", "buffer_post_id": "p9"} for i in range(8)]
        fake = FakeBuffer(status="error")
        self.assertTrue(pb.refresh_statuses(posts, pb.BufferClient("k", fake), log=lambda *_: None))
        self.assertEqual(len(fake.requests), 1)
        self.assertEqual({r["status"] for r in posts}, {"failed"})
        pb.refresh_statuses(posts, pb.BufferClient("k", fake), log=lambda *_: None)
        self.assertEqual(len(fake.requests), 1)  # a failed post is never asked about again

    def test_status_checks_are_capped_and_stop_on_rate_limit(self):
        posts = [{"key": f"k{i}", "status": "queued", "buffer_post_id": f"p{i}"} for i in range(30)]
        fake = FakeBuffer(status="scheduled")
        pb.refresh_statuses(posts, pb.BufferClient("k", fake), log=lambda *_: None)
        self.assertEqual(len(fake.requests), pb.STATUS_CHECKS_PER_RUN)

        def limited(payload):
            raise OSError("HTTP Error 429: Too Many Requests")
        self.assertFalse(pb.refresh_statuses(posts, pb.BufferClient("k", limited), log=lambda *_: None))

    def test_any_failure_pauses_until_the_owner_clears_it_and_posts_are_a_day_apart(self):
        from datetime import datetime, timedelta, timezone
        now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
        def at(h):
            return (now - timedelta(hours=h)).isoformat()
        spam = "Instagram flagged this post as potential spam. Please edit the content and try again."
        posts = [{"key": "a", "status": "sent", "buffer_post_id": "p0", "reserved_at": at(400)},
                 {"key": "b", "status": "failed", "buffer_post_id": "p1", "reserved_at": at(300), "buffer_error": spam},
                 {"key": "c", "status": "locked", "reserved_at": at(1)}]
        none = datetime(2000, 1, 1, tzinfo=timezone.utc)  # no clearance
        reason = pb.paused_after_failure(posts, now, cleared_at=none)
        self.assertIn("the last 1 Instagram post attempt(s) failed", reason)  # weeks later: still paused
        self.assertIn(spam, reason)  # the exact error is reported
        self.assertIn("instagram_errors_cleared_at", reason)
        self.assertIsNone(pb.paused_after_failure(posts, now, cleared_at=now - timedelta(hours=200)))  # owner cleared
        posts.append({"key": "d", "status": "error", "error": "HTTP Error 429: Too Many Requests", "reserved_at": at(2)})
        self.assertIn("429", pb.paused_after_failure(posts, now, cleared_at=now - timedelta(hours=200)))
        posts[-1].update(status="sent", buffer_post_id="p4")
        self.assertIn("24 hours", pb.paused_after_failure(posts, now, cleared_at=none))  # one post a day
        posts[-1]["reserved_at"] = at(25)
        self.assertIsNone(pb.paused_after_failure(posts, now, cleared_at=none))

    def test_failures_in_a_row_are_counted_for_the_monitor(self):
        from datetime import datetime, timedelta, timezone
        now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
        def at(h):
            return (now - timedelta(hours=h)).isoformat()
        none = datetime(2000, 1, 1, tzinfo=timezone.utc)
        posts = [{"key": "a", "status": "sent", "buffer_post_id": "p0", "reserved_at": at(200)},
                 {"key": "b", "status": "failed", "buffer_post_id": "p1", "reserved_at": at(100)},
                 {"key": "c", "status": "failed", "buffer_post_id": "p2", "reserved_at": at(48)},
                 {"key": "d", "status": "failed", "buffer_post_id": "p2", "reserved_at": at(48)}]  # same roundup
        self.assertIn("the last 2 Instagram", pb.paused_after_failure(posts, now, cleared_at=none))

    def test_failed_post_records_bufferss_exact_error_and_is_never_retried(self):
        spam = "Instagram flagged this post as potential spam. Please edit the content and try again."
        posts = [{"key": pb.post_key(deal()), "status": "queued", "buffer_post_id": "p9"}]
        def transport(payload):
            return {"data": {"post": {"id": "p9", "status": "error", "error": {"message": spam}}}}
        logs = []
        pb.refresh_statuses(posts, pb.BufferClient("k", transport), log=logs.append)
        self.assertEqual((posts[0]["status"], posts[0]["buffer_error"]), ("failed", spam))
        self.assertIn(spam, logs[0])
        ready, skipped = pb.plan([deal()], HOSTED, posts, TODAY)
        self.assertEqual(ready, [])  # the same deal is not sent again
        self.assertIn("already queued or posted", skipped[0][1])

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
        ready, skipped = pb.plan(approved, HOSTED, [], TODAY)
        self.assertEqual(ready, [])  # approval never overrides the Amazon price-source rule
        self.assertIn("Creators API", skipped[0][1])


class DuplicateAndFreshnessTests(unittest.TestCase):
    """Owner/Alpha 2026-10-13: no duplicate Instagram posts, no stale prices on Instagram."""

    def test_same_amazon_product_under_another_link_is_the_same_post(self):
        a = deal(store="Amazon", url="https://www.amazon.com/dp/B0H876MWDM?tag=bestdealhun0b-20")
        b = deal(store="Amazon", url="https://www.amazon.com/gp/product/B0H876MWDM?th=1&psc=1")
        c = deal(store="Amazon", url="https://link.amazon/B0iYV850b", asin="B0H876MWDM")
        self.assertEqual(len({pb.post_key(a), pb.post_key(b), pb.post_key(c)}), 1)
        self.assertNotEqual(pb.post_key(a), pb.post_key(dict(a, price=89.99)))  # a new price may post again
        self.assertEqual(pb.post_key(deal()), pb.post_key(deal(url="https://bestbuy.com/site/test/?x=1")))

    def test_lock_tag_blocks_a_post_even_when_the_ledger_was_lost(self):
        fake = FakeBuffer()
        ready = pb.plan([deal()], HOSTED, [], TODAY)[0]
        rows = pb.publish(ready, [], pb.BufferClient("k", fake), "c", "t", log=lambda *_: None, lock=lambda key: False)
        self.assertEqual(fake.requests, [])  # Buffer never called
        self.assertEqual(rows[0]["status"], "locked")

    def test_git_tag_lock_never_forces(self):
        calls = []

        def run(*args, exists=False):
            calls.append(args)
            out = "abc refs/tags/x" if args[0] == "ls-remote" and exists else ""
            return type("R", (), {"stdout": out, "returncode": 0})()
        self.assertTrue(pb.git_tag_lock("k1", run=run))
        self.assertEqual(calls[-1], ("push", "origin", "refs/tags/deal-post/k1:refs/tags/deal-post/k1"))
        self.assertFalse(any("--force" in a or "-f" in a for c in calls for a in c))
        calls.clear()
        self.assertFalse(pb.git_tag_lock("k1", run=lambda *a: run(*a, exists=True)))
        self.assertEqual([c[0] for c in calls], ["ls-remote"])  # stops before tagging or pushing

    def test_auto_published_deal_needs_a_price_check_within_3_hours(self):
        from datetime import datetime, timezone
        now = datetime(2026, 10, 10, 20, 0, tzinfo=timezone.utc)
        auto = deal(approved_by="automatic: retailer product page check", auto_published_at="2026-10-10T18:30:00+00:00",
                    image_source_kind="listing_photo")
        self.assertEqual(pb.eligibility(auto, pb.hosted_urls(HOSTED), set(), TODAY, now), (True, "eligible"))
        stale = dict(auto, auto_published_at="2026-10-10T16:00:00+00:00")
        ok, reason = pb.eligibility(stale, pb.hosted_urls(HOSTED), set(), TODAY, now)
        self.assertFalse(ok)
        self.assertIn("4.0 h ago", reason)
        ok, reason = pb.eligibility(dict(auto, auto_published_at=None), pb.hosted_urls(HOSTED), set(), TODAY, now)
        self.assertEqual((ok, reason.split(";")[0]), (False, "no price-check time"))


if __name__ == "__main__":
    unittest.main()


class RoundupTests(unittest.TestCase):
    """Owner, 2026-10-15: roundup carousels, one deal per slide, with every deal's own duplicate protection."""

    def deals(self, n):
        out, hosted = [], []
        for i in range(n):
            cid = f"{i:012x}"
            out.append(deal(url=f"https://www.bestbuy.com/site/test-{i}", title=f"ILLUSTRATIVE TEST ITEM {i}",
                            price=100 - i, image=f"images/generated/deal-{cid}-square.jpg"))
            hosted.append({"image": f"images/generated/deal-{cid}-feed.jpg",
                           "cloudinary_url": f"https://res.cloudinary.com/x/image/upload/v1/bestdealhunter/{cid}.jpg"})
        return out, hosted

    def test_three_or_more_eligible_deals_become_one_carousel(self):
        deals, hosted = self.deals(10)
        ready, skipped = pb.plan(deals, hosted, [], TODAY, roundups=True)
        self.assertEqual(len(ready), 1)
        post = ready[0]
        self.assertEqual(post["kind"], "roundup")
        self.assertEqual(len(post["image_urls"]), pb.ROUNDUP_MAX)
        self.assertEqual(len(skipped), 2)  # the rest wait for the next run
        self.assertIn(f"Today's Top {pb.ROUNDUP_MAX} Deals", post["text"])
        from deal_graphics import card_facts
        for d in deals[:3]:
            self.assertIn(f"{card_facts(d)['price']} (", post["text"])
        self.assertNotIn("#ad", post["text"])  # no affiliate links on these
        self.assertEqual(pb.assets(post["image_urls"])[1], {"image": {"url": post["image_urls"][1]}})

    def test_fewer_than_three_post_singly_and_the_switch(self):
        deals, hosted = self.deals(2)
        ready, _ = pb.plan(deals, hosted, [], TODAY, roundups=True)
        self.assertEqual((len(ready), ready[0].get("kind")), (1, None))
        deals, hosted = self.deals(5)
        # switched off 2026-10-23 (owner: one verified product per post while the account recovers)
        self.assertIs(pb.RULES.get("instagram_roundups"), False)
        self.assertIsNone(pb.plan(deals, hosted, [], TODAY)[0][0].get("kind"))
        self.assertEqual(pb.plan(deals, hosted, [], TODAY, roundups=True)[0][0].get("kind"), "roundup")
        self.assertIsNone(pb.plan(deals, hosted, [], TODAY, roundups=False)[0][0].get("kind"))

    def test_affiliate_slide_adds_ad_tags(self):
        deals, hosted = self.deals(3)
        deals[1]["url"] = "https://www.ebay.com/itm/123456789012"
        deals[1]["store"] = "eBay"
        deals[1]["affiliate_url"] = "https://www.ebay.com/itm/123456789012?campid=5339220016"
        self.assertIn("#ad #affiliate", pb.roundup_caption(deals))

    def test_every_deal_is_reserved_and_locked_and_a_taken_lock_drops_the_slide(self):
        deals, hosted = self.deals(4)
        post = pb.plan(deals, hosted, [], TODAY, roundups=True)[0][0]
        sent = []

        class Client:
            def create_post(self, channel, text, images):
                sent.append((text, images))
                return {"id": "b1"}
        ledger = []
        blocked = post["items"][0]["key"]
        rows = pb.publish([post], ledger, Client(), "chan", "2026-10-10T12:00:00+00:00", log=lambda *_: None,
                          lock=lambda key: key != blocked)
        self.assertEqual(len(sent), 1)
        self.assertEqual(len(sent[0][1]), 3)  # the locked deal was left out
        self.assertEqual(sorted(r["status"] for r in rows), ["locked", "queued", "queued", "queued"])
        self.assertEqual({r.get("roundup") for r in ledger}, {post["key"]})
        again, _ = pb.plan(deals, hosted, ledger, TODAY, roundups=True)
        self.assertEqual(again, [])  # nothing on that carousel can be posted again

    def test_a_roundup_counts_once_toward_the_daily_limit(self):
        from datetime import datetime, timezone
        deals, hosted = self.deals(4)
        ledger = [{"key": f"k{i}", "roundup": "roundup-1", "reserved_at": "2026-10-10T11:00:00+00:00"}
                  for i in range(pb.MAX_POSTS_PER_DAY + 3)]
        ready, _ = pb.plan(deals, hosted, ledger, TODAY, now=datetime(2026, 10, 10, 12, tzinfo=timezone.utc),
                           roundups=True)
        self.assertEqual(ready, [])  # one roundup = one post, and the limit is now 1 a day (owner, 2026-10-23)
        ledger = ledger[:1]
        ledger[0]["reserved_at"] = "2026-10-09T11:00:00+00:00"  # more than 24 hours ago
        ready, _ = pb.plan(deals, hosted, ledger, TODAY, now=datetime(2026, 10, 10, 12, tzinfo=timezone.utc),
                           roundups=True)
        self.assertEqual(len(ready), 1)

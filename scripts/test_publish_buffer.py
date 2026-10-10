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
        }
        for expected, d in cases.items():
            ok, reason = self.check(d)
            self.assertFalse(ok, expected)
            self.assertIn(expected, reason)

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

    def test_client_cannot_post_until_implemented(self):
        with self.assertRaises(NotImplementedError):
            self.run_main({"INSTAGRAM_PUBLISH_ENABLED": "true", "BUFFER_API_KEY": "x"}, ["--live"])

    def test_post_log_starts_empty(self):
        import json
        self.assertEqual(json.loads(pb.POSTS.read_text()), [])


if __name__ == "__main__":
    unittest.main()

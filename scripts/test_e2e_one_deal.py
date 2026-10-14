"""One-deal end-to-end runner tests. No network; never writes posts.json."""
import json
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import e2e_one_deal as e2e  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEALS = json.loads((ROOT / "deals.json").read_text())
TODAY = max(date.fromisoformat(d["verified_date"]) for d in DEALS)
HOSTED = {m["image"] for m in json.loads((ROOT / "cloudinary-images.json").read_text())}
CARDS_HOSTED = any("deal-9ace7211bab6-feed.jpg" in i for i in HOSTED)


@unittest.skipUnless(CARDS_HOSTED, "branded cards not yet hosted in this checkout")
class EndToEndTests(unittest.TestCase):
    def test_test_deal_passes_every_step_and_ledger_is_untouched(self):
        before = (ROOT / "posts.json").read_bytes()
        rows, deal, post = e2e.run("9ace7211bab6", today=TODAY)
        self.assertTrue(all(ok for _, ok, _ in rows), e2e.report(rows, deal, post))
        self.assertEqual(len(rows), 5)
        self.assertTrue(post["image_url"].startswith("https://res.cloudinary.com/"))
        self.assertEqual((ROOT / "posts.json").read_bytes(), before)

    def test_default_pick_skips_amazon(self):
        _, deal, _ = e2e.run(today=TODAY)
        self.assertNotEqual(deal["store"], "Amazon")

    def test_price_change_without_new_card_fails_card_step(self):
        tv = dict(next(d for d in DEALS if d["store"] == "Best Buy"), price=279.99)
        with self.assertRaises(e2e.StepFailed):
            e2e.step_card(tv)

    def test_already_posted_deal_is_not_ready(self):
        tv = next(d for d in DEALS if d["store"] == "Best Buy")
        mapping = json.loads((ROOT / "cloudinary-images.json").read_text())
        from publish_buffer import post_key
        with self.assertRaises(e2e.StepFailed) as ctx:
            e2e.step_buffer(tv, DEALS, mapping, [{"key": post_key(tv), "status": "sent"}], TODAY)
        self.assertIn("already queued or posted", str(ctx.exception))

    def test_archived_deal_fails_validation_and_website(self):
        rows, _, _ = e2e.run("9ace7211bab6", today=date(2026, 12, 1))
        failed = {name for name, ok, _ in rows if not ok}
        self.assertIn("1. Validate price/discount", failed)
        self.assertIn("4. Website", failed)
        self.assertIn("5. Buffer/Instagram (dry run)", failed)

    def test_unknown_deal_reported(self):
        rows, deal, _ = e2e.run("ffffffffffff", today=TODAY)
        self.assertIsNone(deal)
        self.assertFalse(rows[0][1])


if __name__ == "__main__":
    unittest.main()

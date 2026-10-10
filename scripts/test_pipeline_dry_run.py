"""End-to-end dry-run orchestration tests: stage results and failure handling. No network."""
import json
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline_dry_run as pdr  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
LIVE = json.loads((ROOT / "deals.json").read_text())
TODAY = max(date.fromisoformat(d["verified_date"]) for d in LIVE) if LIVE else date.today()


def by_name(results):
    return {r["name"]: r for r in results}


class PipelineTests(unittest.TestCase):
    def test_live_repository_passes_without_failures(self):
        results = pdr.run(today=TODAY, env={})
        self.assertFalse([r for r in results if r["status"] == pdr.FAIL], pdr.report(results))
        self.assertEqual(len(results), 6)

    def test_invalid_feed_fails_but_other_stages_still_report(self):
        bad = [dict(LIVE[0], price=-1)] if LIVE else [{"title": "x"}]
        results = by_name(pdr.run(today=TODAY, env={}, root_deals=bad))
        self.assertEqual(results["Feed validation"]["status"], pdr.FAIL)
        self.assertIn("Instagram plan (Buffer, dry run)", results)

    def test_stale_feed_warns_and_posts_nothing(self):
        later = TODAY + timedelta(days=30)
        results = by_name(pdr.run(today=later, env={}))
        self.assertEqual(results["Freshness"]["status"], pdr.WARN)
        self.assertIn("0 post(s)", results["Instagram plan (Buffer, dry run)"]["summary"])

    @unittest.skipUnless(LIVE, "needs live deals")
    def test_card_out_of_date_with_price_change_fails(self):
        changed = [dict(LIVE[0], price=round(LIVE[0]["original_price"] * 0.3, 2))] + LIVE[1:]
        result = by_name(pdr.run(today=TODAY, env={}, root_deals=changed))["Branded cards"]
        self.assertEqual(result["status"], pdr.FAIL)
        self.assertIn("out of date", " ".join(result["details"]))

    @unittest.skipUnless(LIVE, "needs live deals")
    def test_publishing_safety_violation_fails(self):
        amazon = next(d for d in LIVE if d["store"] == "Amazon")
        fake = [{"title": amazon["title"], "deal_url": amazon["url"], "text": "no disclosure"}]
        with mock.patch("publish_buffer.plan", return_value=(fake, [])):
            result = by_name(pdr.run(today=TODAY, env={}))["Instagram plan (Buffer, dry run)"]
        self.assertEqual(result["status"], pdr.FAIL)
        text = " ".join(result["details"])
        for rule in ("Amazon deal", "unapproved deal", "#ad"):
            self.assertIn(rule, text)

    def test_crashing_stage_is_reported_not_hidden(self):
        with mock.patch.object(pdr, "check_hosting", side_effect=RuntimeError("boom")):
            result = by_name(pdr.run(today=TODAY, env={}))["Cloudinary hosting"]
        self.assertEqual(result["status"], pdr.FAIL)
        self.assertIn("crashed", result["summary"])

    def test_discovery_flags_never_need_secret_values(self):
        result = pdr.check_discovery({"EBAY_CONFIGURED": "true"})
        self.assertEqual(result["status"], pdr.OK)
        self.assertIn("eBay Browse API: configured", result["details"])
        self.assertEqual(pdr.check_discovery({})["status"], pdr.WARN)

    def test_exit_code_reflects_failures(self):
        with mock.patch.object(pdr, "run", return_value=[pdr.stage("x", pdr.FAIL, "bad")]), mock.patch("sys.stdout"):
            self.assertEqual(pdr.main(), 1)
        with mock.patch.object(pdr, "run", return_value=[pdr.stage("x", pdr.WARN, "meh")]), mock.patch("sys.stdout"):
            self.assertEqual(pdr.main(), 0)


if __name__ == "__main__":
    unittest.main()

"""Publication gate tests: blockers, holds and images block every route onto the site."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import approve_deal  # noqa: E402
from publication_gate import card_id, publication_problems, unresolved_blockers  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
URL = "https://www.macys.com/shop/product/haven-dinnerware?ID=22769453"
BLOCKER = "Final checkout total, shipping, taxes, ZIP-specific availability not confirmed"


def deal(**extra):
    return {"store": "Macy's", "title": "Haven 32 Pc. Dinnerware Set", "category": "Home", "url": URL,
            "price": 46.93, "original_price": 140, "verified_date": date.today().isoformat(),
            "source": "Macy's product page checked by the owner", "price_evidence": "Owner checked page and cart today",
            "image": "images/IMG_4236.jpeg", **extra}


class GateTests(unittest.TestCase):
    def test_clean_approved_candidate_passes(self):
        self.assertEqual(publication_problems(deal(), {"publication_eligible": True}), [])

    def test_feed_deal_without_candidate_only_needs_an_image(self):
        self.assertEqual(publication_problems(deal()), [])

    def test_blockers_need_a_named_human_resolution_for_the_exact_text(self):
        c = {"publication_eligible": True, "blockers": [BLOCKER]}
        self.assertEqual(unresolved_blockers(c), [BLOCKER])
        c["resolved_blockers"] = {BLOCKER: {"by": "Owner", "note": "Checked out to ZIP 10001: $46.93 + $0 shipping"}}
        self.assertEqual(unresolved_blockers(c), [])
        for bad in ({"by": "Claude", "note": "x"}, {"by": "", "note": "x"}, {"by": "Owner"}, "resolved"):
            c["resolved_blockers"] = {BLOCKER: bad}
            self.assertEqual(unresolved_blockers(c), [BLOCKER], bad)

    def test_string_blocker_is_treated_as_a_list(self):
        self.assertEqual(unresolved_blockers({"blockers": BLOCKER}), [BLOCKER])

    def test_holds(self):
        cases = {"missing flag": {}, "false": {"publication_eligible": False}, "truthy string": {"publication_eligible": "yes"},
                 "rejected": {"publication_eligible": True, "status": "rejected"},
                 "notes": {"publication_eligible": True, "notes": "Research candidate only. Do not publish."},
                 "quality": {"publication_eligible": True, "notes": "Not recommended for publication without review."}}
        for name, c in cases.items():
            with self.subTest(name):
                self.assertTrue(publication_problems(deal(), c))

    def test_image_rules(self):
        cases = {
            "missing": None,
            "remote": "https://res.cloudinary.com/x/a.jpg",
            "traversal": "images/../deals.json",
            "not on disk": "images/nope.jpg",
            "another deal's card": f"images/generated/deal-{card_id('https://other.example/x')}-square.jpg",
        }
        for name, image in cases.items():
            with self.subTest(name):
                d = deal(image=image) if image else {k: v for k, v in deal().items() if k != "image"}
                self.assertTrue(publication_problems(d), name)

    def test_own_card_may_be_pending_render_but_another_deals_card_may_not(self):
        own = f"images/generated/deal-{card_id(URL)}-square.jpg"
        self.assertTrue(publication_problems(deal(image=own)))  # not rendered yet
        self.assertEqual(publication_problems(deal(image=own), card_pending=True), [])
        other = f"images/generated/deal-{card_id('https://x.example')}-square.jpg"
        self.assertTrue(publication_problems(deal(image=other), card_pending=True))

    def test_every_repository_candidate_is_currently_held(self):
        for name in ("pending_deals.json", "deal-review-queue.json"):
            for c in json.loads((ROOT / name).read_text(encoding="utf-8")):
                with self.subTest(c["title"]):
                    self.assertTrue(publication_problems(deal(url=c["url"]), c))


class ApproveDealTests(unittest.TestCase):
    """approve_deal.py is the manual route; it must enforce the same gate."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        Path("images").mkdir()
        shutil.copy(ROOT / "images" / "IMG_4236.jpeg", "images/IMG_4236.jpeg")
        Path("deals.json").write_text("[]")

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def approve(self, candidate):
        Path("pending_deals.json").write_text(json.dumps([dict(candidate, id="c1")]))
        approve_deal.approve("c1")

    def published(self):
        return json.loads(Path("deals.json").read_text())

    def test_blocked_candidate_is_refused_and_queue_untouched(self):
        with self.assertRaisesRegex(ValueError, "unresolved blocker"):
            self.approve(deal(publication_eligible=True, blockers=[BLOCKER]))
        self.assertEqual(self.published(), [])
        self.assertEqual(len(json.loads(Path("pending_deals.json").read_text())), 1)

    def test_missing_image_is_refused(self):
        with self.assertRaisesRegex(ValueError, "no image"):
            self.approve({k: v for k, v in deal(publication_eligible=True).items() if k != "image"})

    def test_unapproved_candidate_is_refused(self):
        with self.assertRaisesRegex(ValueError, "publication_eligible"):
            self.approve(deal())

    def test_approved_candidate_is_published_without_review_fields(self):
        self.approve(deal(publication_eligible=True, blockers=[BLOCKER], notes="checked",
                          resolved_blockers={BLOCKER: {"by": "Owner", "note": "Cart total $46.93, ships to 10001"}}))
        published = self.published()
        self.assertEqual(len(published), 1)
        for field in approve_deal.REVIEW_FIELDS:
            self.assertNotIn(field, published[0])
        self.assertEqual(json.loads(Path("pending_deals.json").read_text()), [])


if __name__ == "__main__":
    unittest.main()

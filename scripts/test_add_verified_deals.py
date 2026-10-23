"""Verified-deal intake: evidence required, >=50% computed here, append-only. No network."""
import json
import sys
import tempfile
import unittest
import unittest.mock
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import add_verified_deals as intake  # noqa: E402
import approve_deal  # noqa: E402

TODAY = date(2026, 10, 13)
EXISTING = [{"url": "https://www.bestbuy.com/site/listed"}]


def record(**changes):
    r = {"store": "Target", "title": "ILLUSTRATIVE TEST THROW BLANKET", "variant": "50x60, Gray", "seller": "Target",
         "url": "https://www.target.com/p/test-throw/-/A-12345678", "category": "Home",
         "price": 12.00, "original_price": 30.00, "reference_label": "Reg.", "availability": "In stock, shipping",
         "evidence": {"method": "retailer_page_check", "checked_at": "2026-10-13T10:00:00Z",
                      "source": "https://github.com/x/y/actions/runs/1", "checked_by": "verify_listings.py"}}
    for k, v in changes.items():
        if k == "evidence":
            r["evidence"] = dict(r["evidence"], **v)
        else:
            r[k] = v
    return r


class IntakeTests(unittest.TestCase):
    def accept(self, r):
        return intake.check(r, TODAY, {"https://www.bestbuy.com/site/listed"})

    def reject(self, r, text):
        with self.assertRaises(intake.Rejected) as ctx:
            self.accept(r)
        self.assertIn(text, str(ctx.exception))

    def test_good_record_becomes_unapproved_fresh_deal(self):
        d = self.accept(record())
        self.assertEqual((d["verified_date"], d["publication_approved"]), ("2026-10-13", False))
        self.assertEqual(d["title"], "ILLUSTRATIVE TEST THROW BLANKET — 50x60, Gray")
        self.assertIn("Reg. $30.00", d["price_evidence"])
        self.assertIn("runs/1", d["price_evidence"])
        # field names the card generator (#5) and Buffer caption (#6) read
        self.assertEqual((d["reference_price_label"], d["evidence_method"]), ("Reg.", "retailer_page_check"))

    def test_discount_is_computed_and_floored(self):
        self.reject(record(price=15.01, original_price=30.00), "only 49% off")
        self.accept(record(price=15.00, original_price=30.00))  # exactly 50%

    def test_needs_seller_reference_label_and_buyable_availability(self):
        self.reject(record(seller=""), "missing seller")
        self.reject(record(reference_label=""), "missing reference_label")
        self.reject(record(availability="Out of stock"), "can be bought now")
        self.reject(record(availability="see page"), "can be bought now")

    def test_link_must_be_the_stores_own_site(self):
        self.reject(record(url="https://target.example.com/p/x"), "is not Target's site")
        self.reject(record(store="Walmart"), "is not Walmart's site")

    def test_evidence_rules(self):
        self.reject(record(evidence={"method": "blog_post"}), "evidence.method")
        self.reject(record(evidence={"checked_at": "2026-10-01T00:00:00Z"}), "too old")
        self.reject(record(evidence={"checked_at": "2026-10-14T00:00:00Z"}), "future")
        self.reject(record(evidence={"source": "trust me"}), "run or API URL")
        self.reject(record(evidence={"method": "human_browser_check", "source": "evidence/missing.png"}), "screenshot")

    def test_duplicates_rejected(self):
        self.reject(record(url="https://www.bestbuy.com/site/listed", store="Best Buy"), "already listed")
        accepted, rejected = intake.run([record(), record()], EXISTING, TODAY)
        self.assertEqual((len(accepted), len(rejected)), (1, 1))
        accepted, rejected = intake.run([record()], [], TODAY, pending=[{"url": record()["url"]}])
        self.assertEqual((len(accepted), len(rejected)), (0, 1))  # already awaiting approval

    def test_image_source_must_be_an_authorized_photo(self):
        self.reject(record(image_source="https://example.com/x.jpg"), "authorized photo")
        self.reject(record(image_source="images/source/missing.jpg"), "authorized photo")

    def test_dry_run_and_all_or_nothing_never_touch_the_feed(self):
        files = [intake.ROOT / "deals.json", intake.ROOT / "pending_deals.json"]
        before = [f.read_bytes() for f in files]
        tmp = Path(self.id().replace(".", "_") + ".json")
        try:
            tmp.write_text(json.dumps([record(url=f"https://www.target.com/p/t/-/A-{n}") for n in (1, 2)]))
            with unittest.mock.patch("sys.stdout"):
                self.assertEqual(intake.main([str(tmp)], today=TODAY), 0)          # dry run
                tmp.write_text(json.dumps([record(), record(price=29.0)]))
                self.assertEqual(intake.main([str(tmp), "--apply"], today=TODAY), 1)  # one bad -> nothing written
        finally:
            tmp.unlink(missing_ok=True)
        self.assertEqual([f.read_bytes() for f in files], before)


class StageThenApproveTests(unittest.TestCase):
    """--apply stages in pending_deals.json; only approve_deal.py puts a deal on the website."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.public = [{"store": "Best Buy", "title": "Existing", "category": "Electronics",
                        "url": "https://www.bestbuy.com/site/listed", "price": 10.0, "original_price": 40.0,
                        "verified_date": "2026-10-09", "source": "s", "price_evidence": "e"}]
        (self.dir / "deals.json").write_text(json.dumps(self.public))
        (self.dir / "pending_deals.json").write_text(json.dumps([{"title": "unverified candidate"}]))
        (self.dir / "records.json").write_text(json.dumps([record()]))

    def stage(self, today=TODAY):
        with unittest.mock.patch.object(intake, "ROOT", self.dir), unittest.mock.patch("sys.stdout"):
            return intake.main([str(self.dir / "records.json"), "--apply"], today=today)

    def read(self, name):
        return json.loads((self.dir / name).read_text())

    def approve(self, deal_id, today=TODAY):
        with unittest.mock.patch("sys.stdout"):
            approve_deal.approve(deal_id, self.dir / "pending_deals.json", self.dir / "deals.json", today)

    def test_apply_stages_without_touching_the_website_feed(self):
        self.assertEqual(self.stage(), 0)
        self.assertEqual(self.read("deals.json"), self.public)
        queue = self.read("pending_deals.json")
        self.assertEqual(queue[0], {"title": "unverified candidate"})
        self.assertEqual((queue[1]["review_status"], queue[1]["publication_approved"]), (intake.STAGING, False))
        self.assertRegex(queue[1]["id"], r"^[0-9a-f]{12}$")

    def test_approval_lists_it_on_the_website_still_not_approved_for_instagram(self):
        self.stage()
        deal_id = self.read("pending_deals.json")[1]["id"]
        self.approve(deal_id)
        feed = self.read("deals.json")
        self.assertEqual(feed[:1], self.public)  # append only
        self.assertEqual((feed[1]["url"], feed[1]["publication_approved"]), (record()["url"], False))
        self.assertNotIn("id", feed[1])
        self.assertNotIn("review_status", feed[1])
        self.assertEqual(self.read("pending_deals.json"), [{"title": "unverified candidate"}])

    def test_approval_refuses_a_price_check_that_went_stale(self):
        self.stage()
        deal_id = self.read("pending_deals.json")[1]["id"]
        with self.assertRaisesRegex(ValueError, "stale"):
            self.approve(deal_id, today=date(2026, 10, 16))
        self.assertEqual(self.read("deals.json"), self.public)

    def test_approval_refuses_rows_without_evidence(self):
        queue = [dict(self.read("deals.json")[0], id="abc", url="https://www.bestbuy.com/site/other",
                      verified_date="2026-10-13")]
        (self.dir / "pending_deals.json").write_text(json.dumps(queue))
        with self.assertRaisesRegex(ValueError, "no verification evidence"):
            self.approve("abc")

if __name__ == "__main__":
    unittest.main()

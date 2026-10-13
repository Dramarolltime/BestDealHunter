"""Verified-deal intake: evidence required, >=50% computed here, append-only. No network."""
import json
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import add_verified_deals as intake  # noqa: E402

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

    def test_dry_run_and_all_or_nothing_never_touch_the_feed(self):
        feed = intake.ROOT / "deals.json"
        before = feed.read_bytes()
        tmp = Path(self.id().replace(".", "_") + ".json")
        try:
            tmp.write_text(json.dumps([record(url=f"https://www.target.com/p/t/-/A-{n}") for n in (1, 2)]))
            with unittest.mock.patch("sys.stdout"):
                self.assertEqual(intake.main([str(tmp)], today=TODAY), 0)          # dry run
                tmp.write_text(json.dumps([record(), record(price=29.0)]))
                self.assertEqual(intake.main([str(tmp), "--apply"], today=TODAY), 1)  # one bad -> nothing written
        finally:
            tmp.unlink(missing_ok=True)
        self.assertEqual(feed.read_bytes(), before)


import unittest.mock  # noqa: E402

if __name__ == "__main__":
    unittest.main()

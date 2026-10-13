"""Automatic publishing: only a "verified" product-page check becomes a published deal. No network."""
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import auto_publish as ap  # noqa: E402

TODAY = date(2026, 10, 13)
RUN = "https://github.com/Dramarolltime/BestDealHunter/actions/runs/1"
URL = "https://www.amazon.com/dp/B000TEST01?tag=bestdealhun0b-20"


def row(**changes):
    r = {"url": URL, "store": "Amazon", "verdict": "verified", "checked_at": "2026-10-13T19:10:00+00:00",
         "title": "Amazon.com: ILLUSTRATIVE TEST Kettle, 1.7L, Black : Home & Kitchen",
         "page": {"price": 19.99, "reference": 49.99, "reference_label": "List Price", "discount": 60,
                  "buyable": True, "seller": None}}
    r.update(changes)
    return r


class SelectTests(unittest.TestCase):
    def select(self, rows, deals=(), limit=5):
        logs = []
        new = ap.select(rows, [{"url": URL, "store": "Amazon", "category": "Other"}], list(deals), RUN, TODAY, limit,
                        log=logs.append)
        return new, logs

    def test_verified_page_becomes_an_approved_deal_built_from_page_values(self):
        new, _ = self.select([row()])
        d = new[0]
        self.assertEqual((d["price"], d["original_price"], d["reference_price_label"]), (19.99, 49.99, "List Price"))
        self.assertEqual((d["title"], d["category"], d["verified_date"]), ("ILLUSTRATIVE TEST Kettle, 1.7L, Black", "Home", "2026-10-13"))
        self.assertIs(d["publication_approved"], True)
        self.assertEqual(d["seller"], "unverified")  # never claim the store itself sells it
        self.assertEqual((d["evidence_method"], d["evidence_source"]), ("retailer_page_check", RUN))

    def test_discovered_candidates_take_the_product_page_title(self):
        logs = []
        new = ap.select([row(title="Amazon.com: Samsung 34' Odyssey G5 Monitor | 165Hz, 1ms : Electronics")],
                        [{"url": URL, "store": "Amazon", "asin": "B000TEST01", "title": "Burlwood"}], [], RUN, TODAY, 5,
                        log=logs.append)
        self.assertEqual((new[0]["title"], new[0]["category"]), ("Samsung 34' Odyssey G5 Monitor", "Electronics"))

    def test_nothing_but_verified_is_published(self):
        for verdict in ("blocked", "unconfirmed", "mismatch", "below_50", "unavailable", None):
            self.assertEqual(self.select([row(verdict=verdict)])[0], [], verdict)

    def test_intake_rules_still_apply(self):
        stale = row(checked_at="2026-10-09T00:00:00+00:00")
        under = row(page=dict(row()["page"], price=25.00))  # 49% off
        new, logs = self.select([stale, under])
        self.assertEqual(new, [])
        self.assertTrue(any("too old" in m for m in logs) and any("only 49% off" in m for m in logs))

    def test_80_percent_or_more_is_held(self):
        new, logs = self.select([row(page=dict(row()["page"], price=9.99, reference=49.99))])  # 80%
        self.assertEqual(new, [])
        self.assertTrue(any(m.startswith("HOLD") for m in logs))

    def test_duplicates_and_limit(self):
        self.assertEqual(self.select([row()], deals=[{"url": URL}])[0], [])
        rows = [row(url=f"https://www.amazon.com/dp/B000TEST0{n}") for n in range(3)]
        new, logs = self.select(rows, limit=2)
        self.assertEqual(len(new), 2)
        self.assertTrue(any(m.startswith("LIMIT") for m in logs))


class MainTests(unittest.TestCase):
    def test_apply_appends_and_dry_run_does_not(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "deals.json").write_text(json.dumps([{"url": "https://www.bestbuy.com/x"}]))
        (tmp / "results.json").write_text(json.dumps([row()]))
        (tmp / "cands.json").write_text(json.dumps([{"url": URL, "store": "Amazon"}]))
        args = [str(tmp / "results.json"), str(tmp / "cands.json"), "--run-url", RUN]
        with mock.patch.object(ap, "ROOT", tmp), mock.patch("sys.stdout"):
            ap.main(args, today=TODAY)
            self.assertEqual(len(json.loads((tmp / "deals.json").read_text())), 1)
            ap.main(args + ["--apply"], today=TODAY)
        feed = json.loads((tmp / "deals.json").read_text())
        self.assertEqual((len(feed), feed[0]), (2, {"url": "https://www.bestbuy.com/x"}))  # append only


if __name__ == "__main__":
    unittest.main()

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
URL = "https://www.walmart.com/ip/illustrative-test-kettle/123456789"


def row(**changes):
    r = {"url": URL, "store": "Walmart", "verdict": "verified", "evidence_method": "retailer_api", "checked_at": "2026-10-13T19:10:00+00:00",
         "title": "ILLUSTRATIVE TEST Kettle, 1.7L, Black : Home & Kitchen",
         "page": {"price": 19.99, "reference": 49.99, "reference_label": "List Price", "discount": 60,
                  "buyable": True, "seller": None}}
    r.update(changes)
    return r


class SelectTests(unittest.TestCase):
    def select(self, rows, deals=(), limit=5):
        logs = []
        new = ap.select(rows, [{"url": URL, "store": "Walmart", "category": "Other"}], list(deals), RUN, TODAY, limit,
                        log=logs.append)
        return new, logs

    def test_verified_page_becomes_an_approved_deal_built_from_page_values(self):
        new, _ = self.select([row()])
        d = new[0]
        self.assertEqual((d["price"], d["original_price"], d["reference_price_label"]), (19.99, 49.99, "List Price"))
        self.assertEqual((d["title"], d["category"], d["verified_date"]), ("ILLUSTRATIVE TEST Kettle, 1.7L, Black", "Home", "2026-10-13"))
        self.assertIs(d["publication_approved"], True)
        self.assertEqual(d["seller"], "unverified")  # never claim the store itself sells it
        self.assertEqual((d["evidence_method"], d["evidence_source"]), ("retailer_api", RUN))

    def test_discovered_candidates_take_the_product_page_title(self):
        logs = []
        new = ap.select([row(title="Samsung 34' Odyssey G5 Monitor | 165Hz, 1ms : Electronics")],
                        [{"url": URL, "store": "Walmart", "discovered_from": "deals page", "title": "Burlwood"}], [],
                        RUN, TODAY, 5,
                        log=logs.append)
        self.assertEqual((new[0]["title"], new[0]["category"]), ("Samsung 34' Odyssey G5 Monitor", "Electronics"))

    def test_page_checks_publish_only_for_retailers_cleared_for_automated_reading(self):
        page = row(evidence_method=None)
        new, logs = self.select([page])
        self.assertEqual(new, [])
        self.assertTrue(any("not cleared" in m for m in logs))
        with mock.patch.dict(ap.RULES, {"page_check_allowed_hosts": ["walmart.com"]}):
            self.assertEqual(len(self.select([page])[0]), 1)

    def test_tracking_is_stripped_and_affiliate_redirects_are_refused(self):
        logs = []
        tracked = URL + "?irgwc=1&wmlspartner=xyz&utm_source=x&selected=true"
        new = ap.select([row(url=tracked)], [{"url": tracked, "store": "Walmart"}], [], RUN, TODAY, 5, log=logs.append)
        self.assertEqual(new[0]["url"], URL + "?selected=true")
        redirect = "https://goto.walmart.com/c/1/2/3?u=x"
        new = ap.select([row(url=redirect)], [{"url": redirect, "store": "Walmart"}], [], RUN, TODAY, 5, log=logs.append)
        self.assertEqual(new, [])
        self.assertTrue(any("affiliate redirect" in m for m in logs))

    def test_api_rows_record_the_api_as_evidence(self):
        d = self.select([row()])[0][0]
        self.assertEqual((d["evidence_method"], d["availability"]), ("retailer_api", "In stock online (retailer API)"))

    def test_amazon_page_checks_are_never_published(self):
        # Associates Program Policies: Amazon prices only from an Amazon-served link or Creators API / PA API
        amazon = row(url="https://www.amazon.com/dp/B000TEST01?tag=bestdealhun0b-20", store="Amazon")
        new, logs = self.select([amazon])
        self.assertEqual(new, [])
        self.assertTrue(any("Creators API" in m for m in logs))

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
        listed_elsewhere = {"url": URL.replace("www.", "") + "/?athbdg=L1600"}  # same product, other link form
        self.assertEqual(self.select([row()], deals=[listed_elsewhere])[0], [])
        rows = [row(url=f"https://www.walmart.com/ip/illustrative-test/{n}") for n in range(3)]
        new, logs = self.select(rows, limit=2)
        self.assertEqual(len(new), 2)
        self.assertTrue(any(m.startswith("LIMIT") for m in logs))

    def test_same_price_reconfirmed_refreshes_the_check_time_only(self):
        listed = {"url": URL, "price": 19.99, "original_price": 49.99, "approved_by": ap.APPROVED_BY,
                  "auto_published_at": "2026-10-13T01:00:00+00:00", "verified_date": "2026-10-13",
                  "image": "images/generated/deal-0123456789ab-square.jpg"}
        refreshed = []
        new = ap.select([row(checked_at="2026-10-14T07:00:00+00:00")], [], [listed], RUN, TODAY, 5,
                        log=lambda *_: None, refreshed=refreshed)
        self.assertEqual(new, [])
        self.assertEqual(len(refreshed), 1)
        self.assertEqual(listed["auto_published_at"], "2026-10-14T07:00:00+00:00")
        self.assertEqual(listed["verified_date"], "2026-10-14")
        self.assertNotIn("image", listed)  # its card is redrawn with the new "Price as of" time

    def test_changed_price_or_manual_deal_is_not_redated(self):
        for listed in ({"url": URL, "price": 24.99, "original_price": 49.99, "approved_by": ap.APPROVED_BY,
                        "auto_published_at": "2026-10-13T01:00:00+00:00"},
                       {"url": URL, "price": 19.99, "original_price": 49.99, "approved_by": "owner deal form: x",
                        "auto_published_at": "2026-10-13T01:00:00+00:00"}):
            refreshed = []
            ap.select([row(checked_at="2026-10-14T07:00:00+00:00")], [], [listed], RUN, TODAY, 5,
                      log=lambda *_: None, refreshed=refreshed)
            self.assertEqual(refreshed, [])
            self.assertEqual(listed["auto_published_at"], "2026-10-13T01:00:00+00:00")


class MainTests(unittest.TestCase):
    def test_apply_appends_and_dry_run_does_not(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "deals.json").write_text(json.dumps([{"url": "https://www.bestbuy.com/x"}]))
        (tmp / "results.json").write_text(json.dumps([row()]))
        (tmp / "cands.json").write_text(json.dumps([{"url": URL, "store": "Walmart"}]))
        args = [str(tmp / "results.json"), str(tmp / "cands.json"), "--run-url", RUN]
        with mock.patch.object(ap, "ROOT", tmp), mock.patch("sys.stdout"):
            ap.main(args, today=TODAY)
            self.assertEqual(len(json.loads((tmp / "deals.json").read_text())), 1)
            ap.main(args + ["--apply"], today=TODAY)
        feed = json.loads((tmp / "deals.json").read_text())
        self.assertEqual((len(feed), feed[0]), (2, {"url": "https://www.bestbuy.com/x"}))  # append only


if __name__ == "__main__":
    unittest.main()

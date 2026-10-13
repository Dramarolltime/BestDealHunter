"""Impact catalog connector: inactive by default, strict row filter, no secrets in output. No network."""
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import impact_catalog_candidates as impact  # noqa: E402

RUN = "https://github.com/Dramarolltime/BestDealHunter/actions/runs/1"
SID, TOKEN = "IRsecretSID123", "tok-secret-456"


def item(**changes):
    row = {"CatalogId": "77", "CatalogItemId": "A-1", "Name": "ILLUSTRATIVE TEST THROW", "Category": "Home Decor",
           "CurrentPrice": "12.00", "OriginalPrice": "30.00", "StockAvailability": "InStock",
           "Url": "https://goto.target.com/c/1/2/3?u=https%3A%2F%2Fwww.target.com%2Fp%2F-%2FA-1", "Colors": "Gray"}
    row.update(changes)
    return row


class FakeImpact:
    """Serves Impact-shaped JSON for the paths the connector requests and records them."""

    def __init__(self, catalogs, items):
        self.catalogs, self.items, self.paths = catalogs, items, []

    def __call__(self, req, timeout):
        path = req.full_url.removeprefix(impact.API)
        self.paths.append(path)
        if "/Items" in path:
            page = 2 if "Page=2" in path else 1
            chunk = self.items[(page - 1) * 2: page * 2]
            more = len(self.items) > page * 2
            body = {"Items": chunk, "@nextpageuri": path.split("?")[0] + "?PageSize=100&Page=2" if more else ""}
        else:
            body = {"Catalogs": self.catalogs, "@nextpageuri": ""}
        return io.BytesIO(json.dumps(body).encode())


class RecordFilterTests(unittest.TestCase):
    def check(self, row, store="Target"):
        return impact.record(row, store, "2026-10-13T10:00:00Z", RUN)

    def test_good_row_becomes_an_affiliate_catalog_record(self):
        rec, reason = self.check(item())
        self.assertEqual(reason, "ok")
        self.assertEqual((rec["price"], rec["original_price"], rec["seller"], rec["category"]), (12.0, 30.0, "Target", "Home"))
        self.assertEqual(rec["evidence"]["method"], "affiliate_catalog")
        self.assertEqual(rec["evidence"]["source"], RUN)
        self.assertEqual(rec["variant"], "Gray")

    def test_rejections(self):
        cases = [(item(CurrentPrice="15.01"), "under 50% off"),
                 (item(OriginalPrice=""), "no valid current/original price"),
                 (item(CurrentPrice="40"), "no valid current/original price"),
                 (item(StockAvailability="OutOfStock"), "not in stock"),
                 (item(Url="https://example.com/p/1"), "retailer's domain"),
                 (item(Url="http://goto.target.com/c/1"), "retailer's domain"),
                 (item(SellerName="Gadget Outlet LLC"), "marketplace seller"),
                 (item(Name=" "), "no product name")]
        for row, reason in cases:
            rec, why = self.check(row)
            self.assertIsNone(rec)
            self.assertIn(reason, why)

    def test_walmart_link_must_be_walmart(self):
        self.assertIsNone(self.check(item(), store="Walmart")[0])
        self.assertIsNotNone(self.check(item(Url="https://goto.walmart.com/c/1/2/3"), store="Walmart")[0])


class ConnectorTests(unittest.TestCase):
    def run_main(self, env, fake=None):
        out = Path(tempfile.mkdtemp()) / "out.json"
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            code = impact.main(["--out", str(out)], opener=fake or FakeImpact([], []), env=env)
        return code, buf.getvalue(), out

    def test_inactive_by_default_reads_nothing(self):
        fake = FakeImpact([], [])
        code, log, out = self.run_main({"IMPACT_ACCOUNT_SID": SID, "IMPACT_AUTH_TOKEN": TOKEN}, fake)
        self.assertEqual(code, 0)
        self.assertIn("inactive", log)
        self.assertEqual(fake.paths, [])
        self.assertFalse(out.exists())

    def test_only_retailer_catalogs_are_read_with_paging_and_no_secrets_logged(self):
        catalogs = [{"Id": "77", "AdvertiserName": "Target"}, {"Id": "88", "AdvertiserName": "Some Other Brand"}]
        rows = [item(), item(CatalogItemId="A-2", CurrentPrice="20"), item(CatalogItemId="A-3",
                Url="https://goto.target.com/c/1/2/9")]
        fake = FakeImpact(catalogs, rows)
        env = {"IMPACT_CATALOGS_ENABLED": "true", "IMPACT_ACCOUNT_SID": SID, "IMPACT_AUTH_TOKEN": TOKEN, "RUN_URL": RUN}
        code, log, out = self.run_main(env, fake)
        self.assertEqual(code, 0)
        self.assertFalse(any("/Catalogs/88/" in p for p in fake.paths))
        self.assertEqual(sum("/Catalogs/77/Items" in p for p in fake.paths), 2)  # followed @nextpageuri
        records = json.loads(out.read_text())
        self.assertEqual([r["catalog_item"] for r in records], ["77/A-1", "77/A-3"])
        self.assertIn("Rows read: 3; kept (>=50% off, in stock, retailer link): 2", log)
        self.assertNotIn(SID, log)
        self.assertNotIn(TOKEN, log)

    def test_no_shared_catalog_says_what_to_do(self):
        env = {"IMPACT_CATALOGS_ENABLED": "true", "IMPACT_ACCOUNT_SID": SID, "IMPACT_AUTH_TOKEN": TOKEN, "RUN_URL": RUN}
        code, log, out = self.run_main(env)
        self.assertEqual(code, 0)
        self.assertIn("Apply to their programs in Impact", log)
        self.assertEqual(json.loads(out.read_text()), [])

    def test_records_pass_the_verified_deal_intake_when_present(self):
        try:
            import add_verified_deals as intake
        except ImportError:
            self.skipTest("add_verified_deals.py (PR #25) not on this branch")
        from datetime import date
        rec, _ = impact.record(item(), "Target", "2026-10-13T10:00:00Z", RUN)
        self.assertEqual(intake.check(rec, date(2026, 10, 13), set())["price"], 12.0)


if __name__ == "__main__":
    unittest.main()

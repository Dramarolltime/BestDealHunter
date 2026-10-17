"""Alpha's deal sheet: only complete, fresh, 50%+ "Ready" rows publish, once each, with the form's rules. No network."""
import io
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sheet_intake as si  # noqa: E402
import submit_deal as sd  # noqa: E402
from validate_deals import validate  # noqa: E402

NOW = datetime(2026, 10, 15, 17, 0, tzinfo=timezone.utc)  # 10:00 AM Pacific
SHOT = "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/view?usp=sharing"
ART = "https://drive.google.com/file/d/1ZyXwVuTsRqPoNmLkJiHgFeDcBa987654/view?usp=sharing"
HEADER = ("Store,Product,Product URL,Category,Sale Price,Was Price,Was Label,In Stock,Sold By Store,Checked At,"
          "Screenshot,Alpha Image,Image Numbers Match,Status")


def line(**over):
    f = {"Store": "Target", "Product": "ILLUSTRATIVE TEST BLANKET — NOT A REAL DEAL",
         "Product URL": "https://www.target.com/p/test-blanket/-/A-12345678?clkid=abc", "Category": "Home",
         "Sale Price": "$14.99", "Was Price": "39.99", "Was Label": "Reg.", "In Stock": "Yes", "Sold By Store": "Yes",
         "Checked At": "2026-10-15 09:30", "Screenshot": SHOT, "Alpha Image": ART, "Image Numbers Match": "Yes",
         "Status": "Ready"}
    f.update(over)
    return ",".join(f'"{f[name]}"' for name in HEADER.split(","))


def sheet(*lines):
    return "\n".join([HEADER, *lines]) + "\n"


def png(size=(400, 300)):
    buf = io.BytesIO()
    Image.new("RGB", size, "#22c55e").save(buf, "PNG")
    return buf.getvalue()


class Resp:
    def __init__(self, data): self.data = data
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self, n): return self.data[:n]


class SheetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.saved = (sd.ROOT, sd.EVIDENCE, si.EVIDENCE)
        sd.ROOT, sd.EVIDENCE, si.EVIDENCE = root, root / "evidence", root / "evidence"
        import add_verified_deals
        self.saved_avd = add_verified_deals.ROOT
        add_verified_deals.ROOT = root
        self.urls = []

    def tearDown(self):
        import add_verified_deals
        add_verified_deals.ROOT = self.saved_avd
        sd.ROOT, sd.EVIDENCE, si.EVIDENCE = self.saved
        self.tmp.cleanup()

    def opener(self, req, timeout=30):
        self.urls.append(req.full_url)
        return Resp(png())

    def run_sheet(self, text, deals=(), ledger=None):
        ledger = [] if ledger is None else ledger
        return si.run(text, list(deals), ledger, NOW, self.opener, log=lambda *_: None), ledger

    def test_ready_row_becomes_a_published_deal_with_alphas_image(self):
        new, ledger = self.run_sheet(sheet(line()))
        d = new[0]
        self.assertEqual((d["store"], d["price"], d["original_price"]), ("Target", 14.99, 39.99))
        self.assertEqual(d["url"], "https://www.target.com/p/test-blanket/-/A-12345678")  # tracking stripped
        self.assertTrue(d["approved_by"].startswith("owner deal form: Alpha deal sheet row 2"))
        self.assertEqual(d["card_source"], "submitted")
        self.assertIn(d["card_theme"], __import__("deal_graphics").THEMES)
        self.assertTrue(all(u.startswith("https://drive.google.com/uc?export=download&id=") for u in self.urls))
        self.assertEqual(ledger[0]["result"], "accepted")
        validate([d], NOW.date())

    def test_each_row_is_processed_once(self):
        new, ledger = self.run_sheet(sheet(line()))
        again, ledger = self.run_sheet(sheet(line()), deals=new, ledger=ledger)
        self.assertEqual(again, [])
        self.assertEqual(len(ledger), 1)

    def test_only_ready_rows(self):
        new, ledger = self.run_sheet(sheet(line(Status="Draft"), line(Status="")))
        self.assertEqual((new, ledger), ([], []))

    def test_rejections_are_recorded_with_the_reason(self):
        cases = {
            "Amazon": line(Store="Amazon"),
            "3 hours": line(**{"Checked At": "2026-10-15 05:00"}),
            "In Stock": line(**{"In Stock": "No"}),
            "80%": line(**{"Sale Price": "5.00"}),
            "screenshot": line(Screenshot="https://example.com/x.png"),
            "affiliate": line(**{"Product URL": "https://goto.target.com/abc"}),
            "50%": line(**{"Sale Price": "29.99"}),
        }
        for reason, row in cases.items():
            with self.subTest(reason):
                new, ledger = self.run_sheet(sheet(row))
                self.assertEqual(new, [])
                self.assertEqual(ledger[0]["result"], "rejected")
                self.assertIn(reason.lower(), ledger[0]["reason"].lower())

    def test_screenshot_can_be_a_cloudinary_link_but_not_the_ai_image(self):
        cld = "https://res.cloudinary.com/j9g6uest/image/upload/v1/BestDealHunter/evidence/target-1.png"
        new, ledger = self.run_sheet(sheet(line(Screenshot=cld)))
        self.assertEqual(ledger[0]["result"], "accepted")
        self.assertIn(cld, self.urls)
        new, ledger = self.run_sheet(sheet(line(Screenshot=ART)))
        self.assertEqual(ledger[0]["result"], "rejected")
        self.assertIn("not the ai image", ledger[0]["reason"].lower())

    def test_unticked_image_uses_the_standard_card(self):
        new, _ = self.run_sheet(sheet(line(**{"Image Numbers Match": "No"})))
        self.assertNotIn("card_source", new[0])

    def test_dormant_without_the_sheet_link(self):
        def boom(*a, **k):
            raise AssertionError("no network without the sheet link")
        self.assertEqual(si.main([], env={}, opener=boom), 0)
        self.assertEqual(si.main([], env={"DEALS_SHEET_CSV_URL": "https://evil.example.com/x.csv"}, opener=boom), 1)

    def test_share_link_becomes_the_csv_export(self):
        sid = "1OExDVOjUYZP5eDucyDv-tsJAMXeHZexmzVePnkKmuMw"
        self.assertEqual(si.csv_url(f"https://docs.google.com/spreadsheets/d/{sid}/edit"),
                         f"https://docs.google.com/spreadsheets/d/{sid}/export?format=csv")
        self.assertEqual(si.csv_url(f"https://docs.google.com/spreadsheets/d/{sid}/edit?usp=sharing#gid=123"),
                         f"https://docs.google.com/spreadsheets/d/{sid}/export?format=csv&gid=123")
        self.assertIsNone(si.csv_url("https://evil.example.com/spreadsheets/d/x"))

    def test_drive_links_and_times(self):
        self.assertIsNone(si.drive_download_url("https://example.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWx/view"))
        self.assertEqual(si.checked_time("10/15/2026 9:30:00").utcoffset().total_seconds(), -7 * 3600)
        self.assertEqual(si.checked_time("2026-10-15T16:30:00+00:00"), datetime(2026, 10, 15, 16, 30, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()


class AlphaHeaderTests(unittest.TestCase):
    """Alpha's own sheet layout (2026-10-16) maps onto the same fields and the same checks."""

    HEADER = ("Deal ID,Store,Product,Sale Price (USD),Reference Price (USD),Discount %,Product URL,Price Evidence URL,"
              "Verified At (PT),Availability,AI Image Drive URL,GitHub Image Path,Status,Claude Notes,Published URL")

    def test_alpha_columns_are_understood(self):
        row = ('t-1,Target,Test Kit,6,19.99,69.98,https://www.target.com/p/-/A-94918812,https://e-catalog.com/x,'
               '2026-10-15 15:53 PDT,In stock,,,Needs Claude review,,')
        r = si.rows(self.HEADER + "\n" + row + "\n")[0]
        self.assertEqual((r["price"], r["original"], r["status"]), ("6", "19.99", "Needs Claude review"))
        self.assertEqual(si.checked_time(r["checked_at"]).utcoffset().total_seconds(), -7 * 3600)
        self.assertIn(si.norm(r["status"]), si.READY)
        with self.assertRaises(si.Rejected):  # no In Stock / Sold By Store confirmations, no screenshot
            si.record(r, datetime(2026, 10, 15, 23, 30, tzinfo=timezone.utc))

"""Deal-submission form tests: only trusted, complete, 50%+ submissions with a screenshot publish. No network."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import submit_deal as sd  # noqa: E402
from add_verified_deals import Rejected  # noqa: E402
from validate_deals import validate  # noqa: E402

NOW = datetime(2026, 10, 14, 17, 0, tzinfo=timezone.utc)
SHOT = "https://github.com/user-attachments/assets/0f1e2d3c-aaaa-bbbb-cccc-123456789abc"


def body(**over):
    f = {"Store": "Target", "Product name": "ILLUSTRATIVE TEST BLANKET — NOT A REAL DEAL",
         "Product link": "https://www.target.com/p/test-blanket/-/A-12345678?clkid=abc&utm_source=x",
         "Sale price": "$14.99", "Was price": "39.99", "Was price label": "Reg.", "Category": "Home",
         "Checks": "- [X] It is in stock online\n- [X] It is sold by the store itself (not a marketplace seller)\n"
                   "- [X] I saw this price on the store page within the last hour",
         "Screenshot": f"![page]({SHOT})"}
    f.update(over)
    return "\n\n".join(f"### {k}\n\n{v}" for k, v in f.items())


def issue(**over):
    i = {"number": 42, "title": "Deal: blanket", "author_association": "OWNER", "user": {"login": "owner"},
         "html_url": "https://github.com/o/r/issues/42", "created_at": "2026-10-14T16:50:00Z", "body": body()}
    i.update(over)
    return i


class SubmitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (sd.ROOT, sd.EVIDENCE)
        root = Path(self.tmp.name)
        sd.ROOT, sd.EVIDENCE = root, root / "evidence"
        import add_verified_deals
        self.saved_avd = add_verified_deals.ROOT
        add_verified_deals.ROOT = root

    def tearDown(self):
        import add_verified_deals
        add_verified_deals.ROOT = self.saved_avd
        sd.ROOT, sd.EVIDENCE = self.saved
        self.tmp.cleanup()

    def fetch(self, url, number):
        self.assertEqual(url, SHOT)
        path = sd.EVIDENCE / f"deal-issue-{number}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG fake")
        return path

    def submit(self, i=None, deals=()):
        return sd.submit(i or issue(), list(deals), NOW, fetch=self.fetch)

    def test_good_submission_becomes_a_published_deal(self):
        d = self.submit()
        self.assertEqual((d["store"], d["price"], d["original_price"]), ("Target", 14.99, 39.99))
        self.assertEqual(d["url"], "https://www.target.com/p/test-blanket/-/A-12345678")  # tracking stripped
        self.assertEqual(d["reference_price_label"], "Reg.")
        self.assertTrue(d["publication_approved"])
        self.assertEqual(d["evidence_method"], "human_browser_check")
        self.assertEqual(d["evidence_source"], "evidence/deal-issue-42.png")
        self.assertEqual(d["price_checked_at"], "2026-10-14T16:50:00+00:00")
        validate([d], NOW.date())

    def test_only_owner_or_collaborators(self):
        for who in ("NONE", "CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR"):
            with self.assertRaises(Rejected):
                self.submit(issue(author_association=who))

    def test_rejections(self):
        cases = {
            "under 50%": body(**{"Sale price": "20.01"}),
            "80%+ held": body(**{"Sale price": "7.99"}),
            "Amazon": body(Store="Amazon", **{"Product link": "https://www.amazon.com/dp/B000000000"}),
            "wrong store's site": body(**{"Product link": "https://www.walmart.com/ip/123"}),
            "affiliate redirect": body(**{"Product link": "https://goto.target.com/abc"}),
            "unticked box": body(Checks="- [X] It is in stock online\n- [ ] It is sold by the store itself"),
            "no screenshot": body(Screenshot="forgot it"),
            "bad price": body(**{"Sale price": "about 15"}),
            "unknown category": body(Category="Toys"),
        }
        for name, b in cases.items():
            with self.subTest(name), self.assertRaises((Rejected, ValueError)):
                self.submit(issue(body=b))
        self.assertFalse(any(sd.EVIDENCE.glob("*")) if sd.EVIDENCE.exists() else False)  # no stray screenshots

    def test_product_already_listed(self):
        first = self.submit()
        with self.assertRaises(Rejected):
            self.submit(issue(number=43), deals=[first])

    def test_screenshot_must_be_an_image(self):
        class Resp:
            def __init__(self, data): self.data = data
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self, n): return self.data[:n]
        with self.assertRaises(Rejected):
            sd.fetch_screenshot(SHOT, 1, opener=lambda req, timeout: Resp(b"<html>not an image</html>"))
        path = sd.fetch_screenshot(SHOT, 2, opener=lambda req, timeout: Resp(b"\xff\xd8\xff jpeg"))
        self.assertEqual(path.name, "deal-issue-2.jpg")

    def test_main_reports_and_writes_only_when_accepted(self):
        event = Path(self.tmp.name) / "event.json"
        event.write_text(json.dumps({"issue": issue(author_association="NONE")}))
        feed = Path(self.tmp.name) / "deals.json"
        feed.write_text("[]")
        saved = sd.FEED
        sd.FEED = feed
        try:
            self.assertEqual(sd.main(["--event", str(event), "--apply"], now=NOW), 1)
        finally:
            sd.FEED = saved
        self.assertEqual(json.loads(feed.read_text()), [])

    def test_form_and_workflow_match_the_parser(self):
        root = Path(__file__).resolve().parents[1]
        form = (root / ".github/ISSUE_TEMPLATE/submit-deal.yml").read_text()
        for label in ("Store", "Product name", "Product link", "Sale price", "Was price", "Was price label",
                      "Category", "Checks", "Screenshot"):
            self.assertIn(f"label: {label}\n", form)
        self.assertNotIn("- Amazon", form)
        flow = (root / ".github/workflows/submit-deal.yml").read_text()
        self.assertIn("author_association", flow)
        self.assertIn("'Deal:'", flow)


if __name__ == "__main__":
    unittest.main()

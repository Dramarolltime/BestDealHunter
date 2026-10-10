"""Maintenance monitor tests; no network, GitHub or secrets required."""
import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "maintenance"))
import monitor  # noqa: E402

TODAY = date(2026, 10, 10)


def deal(verified, **extra):
    return {"store": "Best Buy", "title": "TV", "verified_date": verified, **extra}


class FreshnessTests(unittest.TestCase):
    def test_fresh_deals_pass(self):
        results = monitor.check_freshness([deal("2026-10-09")], TODAY)
        self.assertTrue(results[0]["ok"])
        self.assertEqual(len(results), 1)

    def test_stale_deal_flagged_for_unpublishing(self):
        result = monitor.check_freshness([deal("2026-10-07")], TODAY)[0]
        self.assertFalse(result["ok"])
        self.assertIn("Would unpublish", result["details"])

    def test_past_expiry_flagged_even_if_recently_verified(self):
        result = monitor.check_freshness([deal("2026-10-10", expires_date="2026-10-09")], TODAY)[0]
        self.assertFalse(result["ok"])
        self.assertIn("expired 2026-10-09", result["details"])

    def test_missing_date_is_stale(self):
        self.assertFalse(monitor.check_freshness([{"title": "x"}], TODAY)[0]["ok"])

    def test_warning_one_day_before_stale(self):
        results = monitor.check_freshness([deal("2026-10-08")], TODAY)
        self.assertTrue(results[0]["ok"])
        self.assertEqual((results[1]["key"], results[1]["severity"]), ("expiring-deals", "warning"))


class SiteAndImageTests(unittest.TestCase):
    def test_site_ok(self):
        self.assertTrue(monitor.check_site(200, "text/html", b"<h1>BestDealHunter</h1>", "u")["ok"])

    def test_site_down_or_wrong_content(self):
        self.assertFalse(monitor.check_site(0, "", b"", "u")["ok"])
        self.assertFalse(monitor.check_site(200, "text/html", b"Site not found", "u")["ok"])

    def test_public_feed(self):
        self.assertTrue(monitor.check_public_feed(200, b"[]", "u")[0]["ok"])
        self.assertFalse(monitor.check_public_feed(200, b"{oops", "u")[0]["ok"])
        self.assertFalse(monitor.check_public_feed(404, b"", "u")[0]["ok"])

    def test_hosted_images(self):
        hosted = [{"title": "A", "cloudinary_url": "https://res.cloudinary.com/x/a.jpg"},
                  {"title": "B", "cloudinary_url": "https://res.cloudinary.com/x/b.jpg"},
                  {"title": "C", "cloudinary_url": "https://evil.example/c.jpg"}]
        calls = []
        def head(url):
            calls.append(url)
            return (200, "image/jpeg") if url.endswith("a.jpg") else (404, "text/html")
        result = monitor.check_hosted_images(hosted, head)
        self.assertFalse(result["ok"])
        self.assertIn("B", result["details"])
        self.assertEqual(len(calls), 2)  # non-Cloudinary URL is never fetched

    def test_unhosted_images_warn(self):
        result = monitor.check_unhosted([{"title": "T", "image": "images/a.jpg"}], [])
        self.assertEqual((result["ok"], result["severity"]), (False, "warning"))


class WorkflowTests(unittest.TestCase):
    def run_(self, path, conclusion, created, status="completed"):
        return {"path": path, "name": path, "conclusion": conclusion, "status": status,
                "created_at": created, "event": "schedule", "html_url": "h"}

    def test_latest_run_decides(self):
        runs = [self.run_("a.yml", "failure", "2026-10-10T01:00:00Z"),
                self.run_("a.yml", "success", "2026-10-10T02:00:00Z"),
                self.run_("b.yml", "success", "2026-10-10T01:00:00Z"),
                self.run_("b.yml", "timed_out", "2026-10-10T03:00:00Z"),
                self.run_("b.yml", None, "2026-10-10T04:00:00Z", status="in_progress")]
        results = {r["key"]: r["ok"] for r in monitor.check_workflows(runs)}
        self.assertEqual(results, {"workflow:a.yml": True, "workflow:b.yml": False})

    def test_cancelled_is_not_failure(self):
        results = monitor.check_workflows([self.run_("a.yml", "cancelled", "2026-10-10T01:00:00Z")])
        self.assertTrue(results[0]["ok"])


class FakeGitHub:
    def __init__(self, issues=None):
        self.issues = issues or []
        self.calls = []

    def ensure_label(self, *args):
        pass

    def open_issues(self, label):
        return [i for i in self.issues if label in i["labels"] and i["state"] == "open"]

    def call(self, method, path, payload=None, query=None):
        self.calls.append((method, path, payload))
        if method == "POST" and path == "/issues":
            issue = {"number": 100 + len(self.issues), "state": "open", **payload}
            self.issues.append(issue)
            return issue
        if method == "PATCH":
            number = int(path.split("/")[2])
            next(i for i in self.issues if i["number"] == number).update(payload)
        return {}


class IssueSyncTests(unittest.TestCase):
    def results(self, ok):
        return [monitor.check("site", "Website unavailable", ok, "details"),
                monitor.check("expiring-deals", "Soon stale", False, "x", severity="warning")]

    def sync(self, gh, ok, digest=False):
        body = monitor.render_status(self.results(ok), datetime(2026, 10, 10, tzinfo=timezone.utc), "u", "")
        monitor.sync_issues(gh, self.results(ok), body, digest, "", log=lambda *_: None)

    def titles(self, gh, state="open"):
        return sorted(i["title"] for i in gh.issues if i["state"] == state)

    def test_opens_incident_and_status_once(self):
        gh = FakeGitHub()
        self.sync(gh, ok=False)
        self.sync(gh, ok=False)  # second hourly run must not duplicate
        self.assertEqual(self.titles(gh), ["BestDealHunter maintenance status", "[maintenance] Website unavailable"])

    def test_warnings_never_open_incidents(self):
        gh = FakeGitHub()
        self.sync(gh, ok=True)
        self.assertEqual(self.titles(gh), ["BestDealHunter maintenance status"])

    def test_recovery_closes_incident(self):
        gh = FakeGitHub()
        self.sync(gh, ok=False)
        self.sync(gh, ok=True)
        self.assertEqual(self.titles(gh, "closed"), ["[maintenance] Website unavailable"])
        self.assertTrue(any(m == "POST" and p.endswith("/comments") for m, p, _ in gh.calls))

    def test_digest_comments_on_status_issue(self):
        gh = FakeGitHub()
        self.sync(gh, ok=True, digest=True)
        status = next(i for i in gh.issues if monitor.STATUS_LABEL in i["labels"])
        self.assertIn(("POST", f"/issues/{status['number']}/comments"), [(m, p) for m, p, _ in gh.calls])

    def test_status_report_headline(self):
        now = datetime(2026, 10, 10, tzinfo=timezone.utc)
        self.assertIn("🔴", monitor.render_status(self.results(False), now, "u", ""))
        self.assertIn("🟡", monitor.render_status(self.results(True), now, "u", ""))


if __name__ == "__main__":
    unittest.main()

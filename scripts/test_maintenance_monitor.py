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
        results = {r["key"]: r for r in monitor.check_freshness([deal("2026-10-09")], TODAY)}
        self.assertTrue(results["stale-deals"]["ok"])
        self.assertIn("1 active (0 with the price labelled unverified), 0 archived", results["stale-deals"]["details"])
        self.assertNotIn("expiring-deals", results)
        self.assertNotIn("unverified-prices", results)

    def test_old_check_stays_listed_with_unverified_price(self):
        results = {r["key"]: r for r in monitor.check_freshness([deal("2026-10-07"), deal("2026-10-10")], TODAY)}
        self.assertTrue(results["stale-deals"]["ok"])
        self.assertIn("2 active (1 with the price labelled unverified), 0 archived", results["stale-deals"]["details"])
        self.assertEqual(results["unverified-prices"]["severity"], "warning")
        self.assertNotIn("no-active-deals", results)

    def test_past_expiry_is_archived(self):
        details = monitor.check_freshness([deal("2026-10-10", expires_date="2026-10-09")], TODAY)[0]["details"]
        self.assertIn("offer ended 2026-10-09", details)
        self.assertIn("never deleted", details)

    def test_warning_one_day_before_price_label_changes(self):
        results = {r["key"]: r for r in monitor.check_freshness([deal("2026-10-08")], TODAY)}
        self.assertEqual(results["expiring-deals"]["severity"], "warning")

    def test_no_active_deals_is_an_incident_only_when_every_offer_ended(self):
        results = {r["key"]: r for r in monitor.check_freshness([deal("2026-10-01")], TODAY)}
        self.assertNotIn("no-active-deals", results)  # old check alone keeps it listed
        results = {r["key"]: r for r in monitor.check_freshness([deal("2026-10-01", expires_date="2026-10-05")], TODAY)}
        # an incident: every workflow can be green while the site shows no deals
        self.assertEqual((results["no-active-deals"]["ok"], results["no-active-deals"]["severity"]), (False, "error"))

    def test_empty_feed_has_no_active_deals(self):
        results = {r["key"]: r for r in monitor.check_freshness([], TODAY)}
        self.assertFalse(results["no-active-deals"]["ok"])

    def test_invalid_feed_is_an_incident(self):
        results = {r["key"]: r for r in monitor.check_freshness([deal("2026-10-09")], TODAY)}
        self.assertFalse(results["feed-valid"]["ok"])  # test deal lacks url/prices/evidence
        self.assertEqual(results["feed-valid"]["severity"], "error")


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


class QueueTests(unittest.TestCase):
    def test_counts_both_queues(self):
        results = {r["key"]: r for r in monitor.check_queues(
            {"pending_deals.json": [{}], "deal-review-queue.json": [{}, {}, {}]})}
        self.assertTrue(results["pending-queue"]["ok"] and results["review-queue"]["ok"])
        self.assertIn("4 candidate(s) awaiting review", results["review-backlog"]["details"])

    def test_malformed_or_missing_queue_is_an_incident(self):
        for bad in ({"not": "a list"}, None):
            results = {r["key"]: r for r in monitor.check_queues({"pending_deals.json": [], "deal-review-queue.json": bad})}
            self.assertEqual((results["review-queue"]["ok"], results["review-queue"]["severity"]), (False, "error"))

    def test_repository_queues_are_both_counted(self):
        queues = {name: monitor.load_json(name) for name in monitor.REVIEW_QUEUES}
        total = sum(len(q) for q in queues.values())
        details = {r["key"]: r for r in monitor.check_queues(queues)}["review-backlog"]["details"]
        self.assertIn(f"{total} candidate(s)", details)


class InstagramTests(unittest.TestCase):
    """Uses the publisher's own pause rule, so the monitor and publish_buffer.py always agree."""
    NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)

    def rows(self, statuses):
        return [{"status": st, "buffer_post_id": f"p{i}", "reserved_at": f"2026-10-20T0{i}:00:00+00:00"}
                for i, st in enumerate(statuses)]

    def run_check(self, statuses):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from publish_buffer import paused_after_failure
        return monitor.check_instagram(self.rows(statuses), self.NOW, paused_after_failure)

    def test_healthy_when_last_post_sent(self):
        self.assertTrue(self.run_check(["failed", "sent"])["ok"])
        self.assertTrue(self.run_check([])["ok"])

    def test_one_failure_pause_is_a_warning(self):
        result = self.run_check(["sent", "failed"])
        self.assertEqual((result["ok"], result["severity"]), (False, "warning"))
        self.assertIn("paused until", result["details"])

    def test_three_failures_in_a_row_is_an_incident(self):
        result = self.run_check(["sent", "failed", "failed", "failed"])
        self.assertEqual((result["ok"], result["severity"]), (False, "error"))

    def test_unreadable_ledger_does_not_crash(self):
        self.assertTrue(monitor.check_instagram(None, self.NOW, lambda posts, now: None)["ok"])


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

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
        self.assertIn("1 active, 0 archived", results["stale-deals"]["details"])
        self.assertNotIn("expiring-deals", results)

    def test_stale_deal_is_archived_not_an_incident(self):
        results = {r["key"]: r for r in monitor.check_freshness([deal("2026-10-07"), deal("2026-10-10")], TODAY)}
        self.assertTrue(results["stale-deals"]["ok"])
        self.assertIn("1 active, 1 archived", results["stale-deals"]["details"])
        self.assertIn("never deleted", results["stale-deals"]["details"])

    def test_past_expiry_is_archived(self):
        details = monitor.check_freshness([deal("2026-10-10", expires_date="2026-10-09")], TODAY)[0]["details"]
        self.assertIn("offer ended 2026-10-09", details)

    def test_warning_one_day_before_archive(self):
        results = {r["key"]: r for r in monitor.check_freshness([deal("2026-10-08")], TODAY)}
        self.assertEqual(results["expiring-deals"]["severity"], "warning")

    def test_no_active_deals_is_an_incident(self):
        # Hourly workflows can all pass while the site shows nothing: that must open an issue.
        results = {r["key"]: r for r in monitor.check_freshness([deal("2026-10-01")], TODAY)}
        self.assertEqual((results["no-active-deals"]["ok"], results["no-active-deals"]["severity"]), (False, "error"))
        self.assertIn("no workflow re-checks retailer prices", results["no-active-deals"]["details"])

    def test_empty_feed_has_no_active_deals(self):
        results = {r["key"]: r for r in monitor.check_freshness([], TODAY)}
        self.assertFalse(results["no-active-deals"]["ok"])

    def test_active_deal_clears_incident(self):
        results = {r["key"]: r for r in monitor.check_freshness([deal("2026-10-01"), deal("2026-10-10")], TODAY)}
        self.assertNotIn("no-active-deals", results)

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
    def queues(self, pending, review):
        return {"pending_deals.json": pending, "deal-review-queue.json": review}

    def test_counts_both_queues(self):
        pending = [{"status": "needs_checkout_verification", "publication_eligible": False}]
        review = [{"status": "needs_verification"}, {"status": "needs_checkout_verification", "expiration_date": "2026-10-09"},
                  {"review_status": "unverified"}]
        results = {r["key"]: r for r in monitor.check_queues(self.queues(pending, review), TODAY)}
        self.assertTrue(results["pending-queue"]["ok"] and results["review-queue"]["ok"])
        details = results["review-backlog"]["details"]
        self.assertIn("**4 candidate(s) awaiting review**", details)
        self.assertIn("`pending_deals.json`: 1", details)
        self.assertIn("`deal-review-queue.json`: 3", details)
        self.assertIn("needs_checkout_verification: 2", details)
        self.assertIn("unverified: 1", details)
        self.assertIn("Past their offer end date: 1.", details)

    def test_malformed_queue_is_an_incident_but_other_still_counted(self):
        results = {r["key"]: r for r in monitor.check_queues(self.queues([{}], {"not": "a list"}), TODAY)}
        self.assertFalse(results["review-queue"]["ok"])
        self.assertEqual(results["review-queue"]["severity"], "error")
        self.assertIn("**1 candidate(s)", results["review-backlog"]["details"])

    def test_missing_queue_file_is_reported(self):
        results = {r["key"]: r for r in monitor.check_queues(self.queues([], None), TODAY)}
        self.assertFalse(results["review-queue"]["ok"])

    def test_repository_queues_are_both_counted(self):
        queues = {name: monitor.load_json(name) for name in monitor.REVIEW_QUEUES}
        expected = sum(len(q) for q in queues.values())
        details = {r["key"]: r for r in monitor.check_queues(queues, TODAY)}["review-backlog"]["details"]
        self.assertIn(f"**{expected} candidate(s)", details)


class ReverificationAndBufferTests(unittest.TestCase):
    def test_empty_source_warns(self):
        result = monitor.check_reverification_source([])
        self.assertEqual((result["ok"], result["severity"]), (False, "warning"))
        self.assertTrue(monitor.check_reverification_source([{"title": "x"}])["ok"])

    def test_stuck_or_failed_buffer_rows_are_incidents(self):
        posts = [{"title": "A", "status": "sent"}, {"title": "B", "status": "reserved"},
                 {"title": "C", "status": "error", "error": "timeout"},
                 {"title": "D", "status": "queued", "buffer_status": "error"}]
        result = monitor.check_buffer_ledgers(posts, [])[0]
        self.assertFalse(result["ok"])
        self.assertEqual(result["severity"], "error")
        for title in ("B", "C", "D"):
            self.assertIn(f"- {title}:", result["details"])
        self.assertNotIn("- A:", result["details"])

    def test_clean_ledger_and_missing_files_pass(self):
        self.assertEqual([r["ok"] for r in monitor.check_buffer_ledgers([], [])], [True])
        self.assertEqual([r["ok"] for r in monitor.check_buffer_ledgers([], None)], [True])

    def test_corrupt_live_ledger_is_an_incident(self):
        result = monitor.check_buffer_ledgers(None, [])[0]
        self.assertFalse(result["ok"])
        self.assertIn("unreadable", result["details"])

    def test_undeleted_test_draft_warns(self):
        drafts = [{"buffer_post_id": "abc", "status": "awaiting_cleanup"}, {"buffer_post_id": "old", "status": "deleted"}]
        results = {r["key"]: r for r in monitor.check_buffer_ledgers([], drafts)}
        self.assertEqual(results["buffer-test-drafts"]["severity"], "warning")
        self.assertIn("abc", results["buffer-test-drafts"]["details"])
        self.assertNotIn("old", results["buffer-test-drafts"]["details"])


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

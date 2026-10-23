"""buffer_ops safety tests: a fake Buffer GraphQL endpoint; no network, secrets or real posts."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import buffer_ops  # noqa: E402
from buffer_ops import BufferClient, BufferOpsError, run, verify_hosted_image  # noqa: E402

CHANNEL = "6ac834646a5c39ccb65a1157"
NOW = "2026-10-12T00:00:00+00:00"


def nn(kind, name):
    return {"kind": "NON_NULL", "name": None, "ofType": {"kind": kind, "name": name}}


INPUT_SCHEMA = {
    "mutation_fields": [{"name": "createPost", "args": [], "type": nn("UNION", "PostActionPayload")},
                        {"name": "deletePost", "args": [{"name": "input", "type": nn("INPUT_OBJECT", "DeletePostInput")}],
                         "type": nn("UNION", "DeletePostPayload")}],
    "input_fields": {"DeletePostInput": [{"name": "id", "type": nn("SCALAR", "ID")}]},
}


class FakeBuffer:
    """Minimal api.buffer.com stand-in that records every request."""

    def __init__(self, posts=None, schema=INPUT_SCHEMA, delete_works=True):
        self.posts = {p["id"]: dict(p) for p in posts or []}
        self.schema, self.delete_works, self.requests = schema, delete_works, []

    def __call__(self, payload):
        self.requests.append(payload)
        q, v = payload["query"], payload["variables"]
        if 'name: "Mutation"' in q:
            return {"data": {"__type": {"fields": self.schema["mutation_fields"]}}}
        if "InputFields" in q:
            return {"data": {"__type": {"inputFields": self.schema["input_fields"].get(v["name"], [])}}}
        if "GetPost" in q:
            post = self.posts.get(v["input"]["id"])
            if post is None:
                return {"data": {"post": None}, "errors": [{"message": "Post not found", "extensions": {"code": "NOT_FOUND"}}]}
            return {"data": {"post": post}}
        if "deletePost" in q:
            value = v["v"]
            post_id = value if isinstance(value, str) else (value.get("id") or value.get("postId"))
            if self.delete_works:
                self.posts.pop(post_id, None)
            return {"data": {"deletePost": {"__typename": "DeletePostSuccess"}}}
        raise AssertionError(f"unexpected query {q}")

    def deletes(self):
        return [r for r in self.requests if "deletePost(" in r["query"]]


def draft(post_id="p1", **extra):
    return {"id": post_id, "status": "draft", "channelId": CHANNEL, "sentAt": None, "dueAt": None, **extra}


def row(post_id="p1", **extra):
    return {"buffer_post_id": post_id, "channel_id": CHANNEL, "title": "TV", "status": "awaiting_cleanup", **extra}


class CleanupTests(unittest.TestCase):
    def go(self, mode, ledger, fake):
        return run(mode, ledger, BufferClient("key", transport=fake), CHANNEL, NOW, log=lambda *_: None)

    def test_verify_is_read_only(self):
        fake = FakeBuffer([draft()])
        ledger = [row()]
        code, results = self.go("verify", ledger, fake)
        self.assertEqual((code, results[0]["outcome"]), (0, "exists_as_draft"))
        self.assertEqual(fake.deletes(), [])
        self.assertEqual(ledger[0]["status"], "awaiting_cleanup")

    def test_deletes_confirmed_draft_and_proves_it_is_gone(self):
        fake = FakeBuffer([draft()])
        ledger = [row()]
        code, results = self.go("delete", ledger, fake)
        self.assertEqual((code, results[0]["outcome"]), (0, "deleted"))
        self.assertEqual(len(fake.deletes()), 1)
        self.assertEqual(fake.deletes()[0]["variables"], {"v": {"id": "p1"}})
        self.assertEqual(ledger[0]["status"], "deleted")
        self.assertEqual(fake.requests[-1]["variables"], {"input": {"id": "p1"}})  # re-read after delete

    def test_never_deletes_non_drafts(self):
        for state in ({"status": "scheduled"}, {"status": "sent", "sentAt": "2026-10-11T01:00:00Z"},
                      {"status": "draft", "channelId": "other"}, {"status": "draft", "sentAt": "x"}):
            with self.subTest(state=state):
                fake = FakeBuffer([draft(**state)])
                ledger = [row()]
                code, results = self.go("delete", ledger, fake)
                self.assertEqual((code, results[0]["outcome"]), (1, "refused"))
                self.assertEqual(fake.deletes(), [])
                self.assertEqual(ledger[0]["status"], "awaiting_cleanup")

    def test_only_ledger_rows_on_the_configured_channel(self):
        fake = FakeBuffer([draft()])
        code, results = self.go("delete", [row(channel_id="someone-else")], fake)
        self.assertEqual((code, results[0]["outcome"]), (1, "refused"))
        self.assertEqual(fake.requests, [])  # refused before contacting Buffer

    def test_already_deleted_rows_are_skipped(self):
        fake = FakeBuffer([draft()])
        code, results = self.go("delete", [row(status="deleted")], fake)
        self.assertEqual((code, results), (0, []))
        self.assertEqual(fake.requests, [])

    def test_absent_post_counts_as_cleaned_up(self):
        fake = FakeBuffer([])
        ledger = [row()]
        code, results = self.go("delete", ledger, fake)
        self.assertEqual((code, results[0]["outcome"]), (0, "absent"))
        self.assertEqual(fake.deletes(), [])
        self.assertEqual(ledger[0]["deletion_outcome"], "absent")

    def test_delete_that_does_not_stick_fails(self):
        fake = FakeBuffer([draft()], delete_works=False)
        ledger = [row()]
        code, results = self.go("delete", ledger, fake)
        self.assertEqual((code, results[0]["outcome"]), (1, "refused"))
        self.assertEqual(ledger[0]["status"], "awaiting_cleanup")

    def test_unexpected_delete_schema_stops_before_deleting(self):
        bad = {"mutation_fields": [{"name": "deletePost", "args": [{"name": "input", "type": nn("INPUT_OBJECT", "X")}],
                                    "type": nn("UNION", "P")}],
               "input_fields": {"X": [{"name": "id", "type": nn("SCALAR", "ID")},
                                      {"name": "organizationId", "type": nn("SCALAR", "ID")}]}}
        fake = FakeBuffer([draft()], schema=bad)
        code, results = self.go("delete", [row()], fake)
        self.assertEqual((code, results[0]["outcome"]), (1, "error"))
        self.assertEqual(fake.deletes(), [])

    def test_post_id_input_field_and_scalar_return(self):
        schema = {"mutation_fields": [{"name": "deletePost",
                                       "args": [{"name": "input", "type": nn("INPUT_OBJECT", "DeletePostInput")}],
                                       "type": nn("SCALAR", "Boolean")}],
                  "input_fields": {"DeletePostInput": [{"name": "postId", "type": nn("SCALAR", "String")}]}}
        fake = FakeBuffer([draft()], schema=schema)
        code, _ = self.go("delete", [row()], fake)
        self.assertEqual(code, 0)
        query = fake.deletes()[0]["query"]
        self.assertNotIn("__typename", query)
        self.assertEqual(fake.deletes()[0]["variables"], {"v": {"postId": "p1"}})

    def test_other_graphql_errors_are_not_treated_as_absent(self):
        def transport(payload):
            return {"errors": [{"message": "Unauthorized", "extensions": {"code": "UNAUTHENTICATED"}}]}
        ledger = [row()]
        code, results = run("delete", ledger, BufferClient("key", transport=transport), CHANNEL, NOW, log=lambda *_: None)
        self.assertEqual((code, results[0]["outcome"]), (1, "error"))
        self.assertEqual(ledger[0]["status"], "awaiting_cleanup")

    def test_missing_credentials_refuse(self):
        self.assertEqual(buffer_ops.main(["delete"], env={}), 2)
        self.assertEqual(buffer_ops.main(["publish"], env={"BUFFER_API_KEY": "k", "BUFFER_CHANNEL_ID": CHANNEL}), 2)
        with self.assertRaises(BufferOpsError):
            BufferClient("")

    def test_repository_ledger_lists_the_pr17_draft(self):
        ledger = json.loads((buffer_ops.LEDGER).read_text(encoding="utf-8"))
        ids = {r["buffer_post_id"] for r in ledger}
        self.assertIn("6acadf46ed92ed8fa5e85a01", ids)
        self.assertTrue(all(r["channel_id"] == CHANNEL for r in ledger))

    def test_module_never_mentions_publishing_switches(self):
        source = Path(buffer_ops.__file__).read_text(encoding="utf-8")
        for forbidden in ("createPost", "shareNow", "addToQueue"):
            self.assertNotIn(forbidden, source)


class NoAccidentalPublishingTests(unittest.TestCase):
    """The cleanup tool and the monitor have no route to an Instagram post."""
    ROOT = Path(__file__).resolve().parents[1]

    @staticmethod
    def code(path):
        """Workflow text without comment lines (comments may say what a job never does)."""
        return "\n".join(l for l in path.read_text(encoding="utf-8").splitlines() if not l.lstrip().startswith("#"))

    def test_cleanup_workflow_cannot_publish(self):
        code = self.code(self.ROOT / ".github" / "workflows" / "buffer-draft-cleanup.yml")
        for forbidden in ("INSTAGRAM_PUBLISH_ENABLED", "publish_buffer", "--live", "contents: write"):
            self.assertNotIn(forbidden, code)
        self.assertIn("contents: read", code)

    def test_monitor_gets_no_buffer_credentials(self):
        self.assertNotIn("BUFFER_API_KEY", self.code(self.ROOT / ".github" / "workflows" / "maintenance-monitor.yml"))
        source = (self.ROOT / "scripts" / "maintenance" / "monitor.py").read_text(encoding="utf-8")
        for forbidden in ("api.buffer.com", "BUFFER_API_KEY", "createPost", "BufferClient"):
            self.assertNotIn(forbidden, source)


class HostedImageTests(unittest.TestCase):
    def test_requires_reachable_cloudinary_jpeg(self):
        ok = "https://res.cloudinary.com/x/image/upload/v1/bestdealhunter/a.jpg"
        self.assertEqual(verify_hosted_image(ok, head=lambda u: (200, "image/jpeg")), (True, "ok"))
        self.assertFalse(verify_hosted_image(ok, head=lambda u: (404, "text/html"))[0])
        self.assertFalse(verify_hosted_image(ok, head=lambda u: (200, "image/png"))[0])
        calls = []
        self.assertFalse(verify_hosted_image("https://evil.example/a.jpg", head=lambda u: calls.append(u))[0])
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()

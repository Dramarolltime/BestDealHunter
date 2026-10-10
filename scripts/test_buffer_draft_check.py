"""Draft-check safety tests: fail-closed, never reaches live posting. No network."""
import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish_buffer as pb  # noqa: E402
from deal_graphics import card_id  # noqa: E402

TODAY = date(2026, 10, 10)
CARD = "images/generated/deal-0123456789ab-feed.jpg"
URL = "https://res.cloudinary.com/x/image/upload/v1/bestdealhunter/abc.jpg"
HOSTED = [{"image": CARD, "cloudinary_url": URL}]
CHANNEL = "6ac834646a5c39ccb65a1157"


def deal(**changes):
    d = {"store": "Best Buy", "title": "ILLUSTRATIVE TEST TV — NOT A REAL DEAL", "category": "Electronics",
         "url": "https://www.bestbuy.com/site/test", "price": 299.99, "original_price": 699.99,
         "verified_date": "2026-10-10", "source": "Test", "price_evidence": "Test", "image": CARD}
    d.update(changes)
    return d


DEAL = deal()
CID = card_id(DEAL)
GOOD_POST = {"id": "d1", "status": "draft", "channelId": CHANNEL, "schedulingType": "notification",
             "shareMode": "addToQueue", "sharedNow": False, "sentAt": None, "dueAt": None}


class FakeBuffer:
    def __init__(self, result=None, errors=None, raise_exc=None):
        self.requests = []
        self.result = {"__typename": "PostActionSuccess", "post": dict(GOOD_POST)} if result is None else result
        self.errors, self.raise_exc = errors, raise_exc

    def __call__(self, payload):
        self.requests.append(payload)
        if self.raise_exc:
            raise self.raise_exc
        if self.errors:
            return {"errors": [{"message": self.errors}]}
        return {"data": {"createPost": self.result}}


def check(fake, drafts=None, deals=(DEAL,), card=CID):
    drafts = [] if drafts is None else drafts
    client = pb.BufferClient("k", fake)
    return pb.draft_check(card, list(deals), HOSTED, drafts, client, CHANNEL, "now", TODAY, log=lambda *_: None), drafts


class DraftRequestTests(unittest.TestCase):
    def test_request_is_a_non_publishing_draft(self):
        fake = FakeBuffer()
        row, _ = check(fake)
        self.assertEqual(len(fake.requests), 1)
        sent = fake.requests[0]["variables"]["input"]
        self.assertIs(sent["saveToDraft"], True)
        self.assertEqual(sent["schedulingType"], "notification")  # Buffer workers never auto-send
        self.assertEqual(sent["mode"], "addToQueue")                # required by schema; least immediate
        self.assertNotIn("dueAt", sent)
        self.assertEqual(sent["channelId"], CHANNEL)
        self.assertEqual(sent["assets"], [{"image": {"url": URL}}])
        # Buffer requires Instagram metadata; a feed post, never a story or reel
        self.assertEqual(sent["metadata"], {"instagram": {"type": "post", "shouldShareToFeed": True}})
        self.assertEqual(set(sent), {"text", "channelId", "saveToDraft", "schedulingType", "mode", "assets", "metadata"})
        self.assertEqual((row["status"], row["buffer_post_id"]), ("draft", "d1"))

    def test_only_create_post_mutation_is_sent(self):
        fake = FakeBuffer()
        check(fake)
        q = fake.requests[0]["query"]
        self.assertIn("createPost(input: $input)", q)
        for forbidden in ("editPost", "movePostInQueue", "promoteContentItemDraftToPosts", "shareNow"):
            self.assertNotIn(forbidden, q)

    def test_test_approval_does_not_need_or_change_publication_approved(self):
        unapproved = deal(publication_approved=False)
        row, _ = check(FakeBuffer(), deals=[unapproved])
        self.assertEqual(row["status"], "draft")
        self.assertIs(unapproved["publication_approved"], False)

    def test_other_gates_still_apply(self):
        amazon = deal(store="Amazon", url="https://www.amazon.com/dp/X")
        fake = FakeBuffer()
        with self.assertRaises(pb.DraftCheckFailed):
            check(fake, deals=[amazon], card=card_id(amazon))
        with self.assertRaises(pb.DraftCheckFailed):
            check(fake, deals=[deal(verified_date="2026-10-01")])
        self.assertEqual(fake.requests, [])


class FailClosedTests(unittest.TestCase):
    def assert_stops(self, fake):
        with self.assertRaises(pb.DraftCheckFailed):
            check(fake)
        drafts = []
        with self.assertRaises(pb.DraftCheckFailed):
            check(fake, drafts=drafts)
        self.assertEqual(drafts[0]["status"], "error")
        return drafts

    def test_unexpected_or_ambiguous_responses_stop(self):
        bad_posts = {
            "scheduled instead of draft": dict(GOOD_POST, status="scheduled"),
            "sending": dict(GOOD_POST, status="sending"),
            "sent": dict(GOOD_POST, status="sent", sentAt="2026-10-10T08:00:00Z"),
            "needs approval": dict(GOOD_POST, status="needs_approval"),
            "status missing": {k: v for k, v in GOOD_POST.items() if k != "status"},
            "automatic scheduling": dict(GOOD_POST, schedulingType="automatic"),
            "shared now": dict(GOOD_POST, sharedNow=True),
            "wrong channel": dict(GOOD_POST, channelId="other"),
            "no id": dict(GOOD_POST, id=None),
            "sentAt missing": {k: v for k, v in GOOD_POST.items() if k != "sentAt"},
        }
        for name, post in bad_posts.items():
            with self.subTest(name):
                self.assert_stops(FakeBuffer(result={"__typename": "PostActionSuccess", "post": post}))

    def test_error_union_members_stop(self):
        for kind in ("InvalidInputError", "UnauthorizedError", "LimitReachedError", "RestProxyError",
                     "UnexpectedError", "NotFoundError", "SomethingNew"):
            with self.subTest(kind):
                self.assert_stops(FakeBuffer(result={"__typename": kind, "message": "nope"}))

    def test_missing_typename_or_empty_result_stops(self):
        self.assert_stops(FakeBuffer(result={"post": dict(GOOD_POST)}))
        self.assert_stops(FakeBuffer(result={}))
        self.assert_stops(FakeBuffer(result=[]))

    def test_graphql_and_transport_errors_stop(self):
        self.assert_stops(FakeBuffer(errors="Field 'saveToDraft' is not defined"))
        self.assert_stops(FakeBuffer(raise_exc=TimeoutError("timeout")))

    def test_failed_or_done_draft_blocks_a_repeat(self):
        drafts = self.assert_stops(FakeBuffer(result={"__typename": "UnexpectedError", "message": "x"}))
        fake = FakeBuffer()
        with self.assertRaises(pb.DraftCheckFailed):
            check(fake, drafts=drafts)
        self.assertEqual(fake.requests, [])  # blocked before any API call
        _, ok = check(FakeBuffer())
        with self.assertRaises(pb.DraftCheckFailed):
            check(fake, drafts=ok)
        self.assertEqual(fake.requests, [])

    def test_invalid_or_unknown_card_id_stops_before_api(self):
        fake = FakeBuffer()
        for card in ("../../etc", "ffffffffffff", ""):
            with self.subTest(card), self.assertRaises(pb.DraftCheckFailed):
                check(fake, card=card)
        self.assertEqual(fake.requests, [])


class NeverReachesLiveTests(unittest.TestCase):
    """Run main(--draft-check) end to end with the live functions booby-trapped."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.dir.name) / "buffer-draft-checks.json"
        self.posts_before = pb.POSTS.read_bytes()

    def tearDown(self):
        self.dir.cleanup()
        self.assertEqual(pb.POSTS.read_bytes(), self.posts_before)  # live ledger untouched

    def run_main(self, fake, env_extra=None):
        env = {"BUFFER_API_KEY": "k", "BUFFER_CHANNEL_ID": CHANNEL, "INSTAGRAM_PUBLISH_ENABLED": "true"}
        env.update(env_extra or {})
        boom = mock.Mock(side_effect=AssertionError("live path reached"))
        real_client = pb.BufferClient
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(pb, "publish", boom), \
                mock.patch.object(pb, "refresh_statuses", boom), \
                mock.patch.object(pb.BufferClient, "create_post", boom), \
                mock.patch.object(pb, "BufferClient", lambda key: real_client(key, fake)), \
                mock.patch.object(pb, "DRAFT_CHECKS", self.tmp), \
                mock.patch.object(pb, "load", side_effect=lambda path, default: (
                    [DEAL] if path.name == "deals.json" else HOSTED if path.name == "cloudinary-images.json"
                    else json.loads(path.read_text()) if path.exists() else default)), \
                mock.patch.object(pb, "date", mock.Mock(today=lambda: TODAY)):
            code = pb.main(["--draft-check", CID])
        boom.assert_not_called()
        return code

    def test_success_with_kill_switch_on_still_only_drafts(self):
        fake = FakeBuffer()
        self.assertEqual(self.run_main(fake), 0)
        self.assertEqual(len(fake.requests), 1)
        self.assertIs(fake.requests[0]["variables"]["input"]["saveToDraft"], True)
        self.assertEqual(json.loads(self.tmp.read_text())[0]["status"], "draft")

    def test_ambiguous_response_exits_nonzero_and_records_error(self):
        fake = FakeBuffer(result={"__typename": "PostActionSuccess", "post": dict(GOOD_POST, status="scheduled")})
        self.assertEqual(self.run_main(fake), 1)
        self.assertEqual(json.loads(self.tmp.read_text())[0]["status"], "error")

    def test_refused_without_channel(self):
        fake = FakeBuffer()
        self.assertEqual(self.run_main(fake, {"BUFFER_CHANNEL_ID": ""}), 2)
        self.assertEqual(fake.requests, [])


if __name__ == "__main__":
    unittest.main()

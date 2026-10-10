"""One-time guard tests with REAL git repositories (a local bare repo stands in for GitHub).

Each "run" is a fresh clone, like a separate GitHub Actions job. No network, no Buffer.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import buffer_draft_once as once  # noqa: E402
import publish_buffer as pb  # noqa: E402

CARD = "9ace7211bab6"
CHANNEL = "6ac834646a5c39ccb65a1157"
GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def sh(*args, cwd=None):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class Remote:
    """A bare 'origin' plus a helper that makes a fresh clone per simulated workflow run."""

    def __init__(self, root):
        self.root = Path(root)
        self.bare = self.root / "origin.git"
        sh("init", "--bare", "-q", "-b", "main", str(self.bare))
        seed = self.root / "seed"
        sh("init", "-q", "-b", "main", str(seed))
        (seed / "README").write_text("x")
        sh("add", "README", cwd=seed)
        sh("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init", cwd=seed)
        sh("push", "-q", str(self.bare), "HEAD:refs/heads/main", cwd=seed)
        self.runs = 0

    def new_run(self):
        self.runs += 1
        clone = self.root / f"run{self.runs}"
        sh("clone", "-q", str(self.bare), str(clone))
        return clone

    def delete_lock(self):
        sh("push", "-q", str(self.bare), f":refs/tags/{once.TAG_PREFIX}{CARD}", cwd=self.root / "seed")


class FakeBuffer:
    def __init__(self):
        self.requests = []

    def __call__(self, payload):
        self.requests.append(payload)
        return {"data": {"createPost": {"__typename": "PostActionSuccess", "post": {
            "id": f"d{len(self.requests)}", "status": "draft", "channelId": CHANNEL, "schedulingType": "notification",
            "shareMode": "addToQueue", "sharedNow": False, "sentAt": None, "dueAt": None}}}}


class OnceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, GIT_ENV)
        self.env.start()
        self.remote = Remote(self.tmp.name)
        self.buffer = FakeBuffer()
        self.drafts_file = Path(self.tmp.name) / "drafts.json"

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def real_draft(self, card, env):
        """The real run_draft_check with Buffer replaced by the fake (fresh ledger per run, as in CI)."""
        self.drafts_file.unlink(missing_ok=True)
        client = pb.BufferClient
        with mock.patch.object(pb, "BufferClient", lambda key: client(key, self.buffer)), \
                mock.patch.object(pb, "DRAFT_CHECKS", self.drafts_file):
            return pb.run_draft_check(card, env, log=lambda *_: None)

    def run_job(self, draft=None):
        env = {"BUFFER_API_KEY": "k", "BUFFER_CHANNEL_ID": CHANNEL, "GITHUB_RUN_ID": str(self.remote.runs + 1)}
        lines = []
        code = once.main([CARD], env, draft or self.real_draft, remote="origin", cwd=self.remote.new_run(),
                         log=lines.append)
        return code, "\n".join(lines)

    def lock_exists(self):
        return subprocess.run(["git", "ls-remote", "--exit-code", "--tags", str(self.remote.bare),
                               f"refs/tags/{once.TAG_PREFIX}{CARD}"], capture_output=True).returncode == 0

    def test_second_run_cannot_create_another_draft(self):
        first, _ = self.run_job()
        self.assertEqual(first, 0)
        self.assertEqual(len(self.buffer.requests), 1)
        self.assertTrue(self.lock_exists())
        # a separate later run (e.g. label removed and re-added) is refused before any Buffer call
        second, text = self.run_job()
        self.assertEqual(second, once.REFUSED)
        self.assertIn("already exists", text)
        self.assertEqual(len(self.buffer.requests), 1)

    def test_many_reruns_still_one_draft(self):
        codes = [self.run_job()[0] for _ in range(5)]
        self.assertEqual(codes, [0] + [once.REFUSED] * 4)
        self.assertEqual(len(self.buffer.requests), 1)

    def test_racing_runs_only_one_gets_the_lock(self):
        # Both runs pass the pre-check before either pushes; the remote still accepts only one tag.
        a, b = self.remote.new_run(), self.remote.new_run()
        with mock.patch.object(once, "remote_has", return_value=False):
            got_a, _ = once.acquire(CARD, "a", "origin", a)
            got_b, why_b = once.acquire(CARD, "b", "origin", b)
        self.assertEqual((got_a, got_b), (True, False))
        self.assertIn("not acquired", why_b)

    def test_failed_draft_keeps_the_lock(self):
        failing = mock.Mock(return_value=1)
        self.assertEqual(self.run_job(draft=failing)[0], 1)
        self.assertTrue(self.lock_exists())
        second = mock.Mock()
        self.assertEqual(self.run_job(draft=second)[0], once.REFUSED)
        second.assert_not_called()

    def test_lock_is_taken_before_buffer_is_called(self):
        seen = {}

        def draft(card, env):
            seen["locked"] = self.lock_exists()
            return 0

        self.run_job(draft=draft)
        self.assertTrue(seen["locked"])

    def test_unreachable_remote_fails_closed(self):
        draft = mock.Mock()
        lines = []
        code = once.main([CARD], {}, draft, remote="https://invalid.invalid/x.git", cwd=self.remote.new_run(),
                         log=lines.append)
        self.assertEqual(code, once.REFUSED)
        draft.assert_not_called()

    def test_only_a_deliberate_tag_deletion_allows_a_retry(self):
        self.run_job()
        self.remote.delete_lock()
        self.assertEqual(self.run_job()[0], 0)
        self.assertEqual(len(self.buffer.requests), 2)

    def test_invalid_card_id_refused(self):
        draft = mock.Mock()
        self.assertEqual(once.main(["../x"], {}, draft, cwd=self.remote.new_run(), log=lambda *_: None),
                         once.REFUSED)
        draft.assert_not_called()

    def test_never_force_pushes(self):
        calls = []
        real = once.git

        def spy(args, cwd=None):
            calls.append(args)
            return real(args, cwd)

        with mock.patch.object(once, "git", spy):
            self.run_job()
        self.assertTrue(calls)
        for args in calls:
            self.assertFalse({"-f", "--force", "--force-with-lease", "--delete", "-d"} & set(args), args)


if __name__ == "__main__":
    unittest.main()

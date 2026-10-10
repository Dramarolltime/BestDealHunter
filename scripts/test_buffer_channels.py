"""Buffer channel lookup tests: read-only queries, key never printed. No network."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import buffer_channels as bc  # noqa: E402

KEY = "secret-key-123"


class Fake:
    def __init__(self, channels=None, errors=None):
        self.requests, self.errors = [], errors
        self.channels = channels if channels is not None else [
            {"id": "ch_fb", "name": "BestDealHunter", "displayName": "BestDealHunter", "service": "facebook"},
            {"id": "ch_ig", "name": "best_dealhunter", "displayName": "best_dealhunter", "service": "instagram"}]

    def __call__(self, payload):
        self.requests.append(payload)
        if self.errors:
            return {"errors": [{"message": self.errors}]}
        if "organizations" in payload["query"]:
            return {"data": {"account": {"organizations": [{"id": "org1", "name": "Org"}]}}}
        return {"data": {"channels": self.channels}}


def run(fake):
    lines = []
    code = bc.main({"BUFFER_API_KEY": KEY}, fake, lines.append)
    return code, "\n".join(lines)


class LookupTests(unittest.TestCase):
    def test_finds_instagram_channel_id(self):
        fake = Fake()
        code, text = run(fake)
        self.assertEqual(code, 0)
        self.assertIn("BUFFER_CHANNEL_ID for best_dealhunter: `ch_ig`", text)
        self.assertEqual(fake.requests[1]["variables"], {"input": {"organizationId": "org1"}})

    def test_only_queries_are_sent(self):
        fake = Fake()
        run(fake)
        for req in fake.requests:
            self.assertNotIn("mutation", req["query"].lower())
        with self.assertRaises(bc.LookupError_):
            bc.call(fake, "mutation { deletePost }")

    def test_key_never_printed_even_if_echoed_in_errors(self):
        code, text = run(Fake(errors=f"bad token {KEY}"))
        self.assertEqual(code, 1)
        self.assertNotIn(KEY, text)
        self.assertIn("bad token ***", text)

    def test_no_match_fails(self):
        code, text = run(Fake(channels=[{"id": "x", "name": "other", "displayName": "o", "service": "instagram"}]))
        self.assertEqual(code, 1)
        self.assertIn("No channel named best_dealhunter", text)

    def test_missing_key(self):
        lines = []
        self.assertEqual(bc.main({}, Fake(), lines.append), 2)


if __name__ == "__main__":
    unittest.main()

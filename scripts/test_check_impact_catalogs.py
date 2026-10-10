"""Impact diagnostics tests: redaction and parsing. No network or credentials."""
import io
import json
import os
import sys
import unittest
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_impact_catalogs as ic  # noqa: E402

SID, TOKEN = "IRsecretSID123", "tokSECRET456"


def opener_for(responses):
    def opener(request, timeout=25):
        resource = "Campaigns" if "/Campaigns" in request.full_url else "Catalogs"
        result = responses[resource]
        if isinstance(result, Exception):
            raise result
        return io.BytesIO(json.dumps(result).encode())
    return opener


def run(responses):
    out = io.StringIO()
    with mock.patch.dict(os.environ, {"IMPACT_ACCOUNT_SID": SID, "IMPACT_AUTH_TOKEN": TOKEN}), redirect_stdout(out):
        code = ic.main(opener=opener_for(responses))
    return code, out.getvalue()


class SummaryTests(unittest.TestCase):
    def test_parses_list_and_nested_shapes(self):
        self.assertEqual(ic.summarize({"Catalogs": [{"Id": 1}, {"Id": 2}]}, "Catalogs", "Catalog")["count"], 2)
        self.assertEqual(ic.summarize({"Catalogs": {"Catalog": {"Id": 1}}}, "Catalogs", "Catalog")["count"], 1)
        self.assertEqual(ic.summarize({"@total": "0"}, "Catalogs", "Catalog")["paging"], {"@total": "0"})

    def test_status_counts(self):
        s = ic.summarize({"Campaigns": [{"ContractStatus": "Active"}, {"ContractStatus": "Active"}, {}]},
                         "Campaigns", "Campaign", "ContractStatus")
        self.assertEqual(s["by_status"], {"Active": 2, "unknown": 1})


class MainTests(unittest.TestCase):
    def test_zero_catalogs_is_diagnosed_and_redacted(self):
        code, out = run({"Catalogs": {"Catalogs": [], "@total": "0"},
                         "Campaigns": {"Campaigns": [{"ContractStatus": "Active", "CampaignName": "Secret Brand"}]}})
        self.assertEqual(code, 0)
        self.assertIn("Catalogs: 0", out)
        self.assertIn("Joined campaigns (brands): 1", out)
        self.assertIn("no catalogs are shared", out)
        for secret in (SID, TOKEN, "Secret Brand", "api.impact.com"):
            self.assertNotIn(secret, out)

    def test_auth_failure_is_explained_without_secrets(self):
        err = urllib.error.HTTPError("https://api.impact.com/x", 401, "Unauthorized", {}, None)
        code, out = run({"Catalogs": err, "Campaigns": err})
        self.assertEqual(code, 1)
        self.assertIn("HTTP 401 (credentials rejected)", out)
        self.assertNotIn(SID, out)

    def test_campaign_endpoint_failure_does_not_fail_check(self):
        err = urllib.error.HTTPError("https://api.impact.com/x", 403, "Forbidden", {}, None)
        code, out = run({"Catalogs": {"Catalogs": [{"Id": 1}]}, "Campaigns": err})
        self.assertEqual(code, 0)
        self.assertIn("Campaigns returned HTTP 403", out)

    def test_missing_secret_named_not_valued(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"IMPACT_AUTH_TOKEN": TOKEN}, clear=False), redirect_stdout(out):
            os.environ.pop("IMPACT_ACCOUNT_SID", None)
            self.assertEqual(ic.main(opener=opener_for({})), 2)
        self.assertIn("IMPACT_ACCOUNT_SID", out.getvalue())
        self.assertNotIn(TOKEN, out.getvalue())


if __name__ == "__main__":
    unittest.main()

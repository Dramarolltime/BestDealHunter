"""Read-only Impact connectivity and catalog diagnostics. No deals are imported or published.

Prints only redacted, aggregate information: HTTP status, top-level response keys,
paging totals, and counts (catalogs, joined campaigns by status). It never prints the
account SID, token, request URLs or brand names, so the log is safe in a public repo.
"""
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from collections import Counter

API = "https://api.impact.com/Mediapartners/{sid}/{resource}?PageSize={size}"
PAGING_KEYS = ("@page", "@numpages", "@pagesize", "@total")


def items(payload, plural, singular):
    """Impact list payloads appear as {"Catalogs": [...]} or {"Catalogs": {"Catalog": [...]}}."""
    value = payload.get(plural, payload.get(plural.lower(), []))
    if isinstance(value, dict):
        value = value.get(singular, [])
    if isinstance(value, dict):
        value = [value]
    return value if isinstance(value, list) else []


def summarize(payload, plural, singular, status_field=None):
    """Aggregate, non-identifying summary of an Impact list response."""
    rows = items(payload, plural, singular)
    summary = {
        "keys": sorted(k for k in payload if not k.startswith("@")),
        "paging": {k: payload[k] for k in PAGING_KEYS if k in payload},
        "count": len(rows),
    }
    if status_field:
        summary["by_status"] = dict(Counter(str(r.get(status_field, "unknown")) for r in rows if isinstance(r, dict)))
    return summary


def fetch(sid, token, resource, size=100, opener=urllib.request.urlopen):
    credentials = base64.b64encode(f"{sid}:{token}".encode()).decode()
    request = urllib.request.Request(API.format(sid=sid, resource=resource, size=size), headers={
        "Authorization": f"Basic {credentials}",
        "Accept": "application/json",
        "User-Agent": "BestDealHunter-catalog-check/1.1",
    })
    with opener(request, timeout=25) as response:
        return json.load(response)


def main(opener=urllib.request.urlopen):
    sid = os.environ.get("IMPACT_ACCOUNT_SID")
    token = os.environ.get("IMPACT_AUTH_TOKEN")
    if not sid or not token:
        missing = [n for n, v in (("IMPACT_ACCOUNT_SID", sid), ("IMPACT_AUTH_TOKEN", token)) if not v]
        print(f"Missing Impact secret(s): {', '.join(missing)}.")
        return 2
    try:
        catalogs = summarize(fetch(sid, token, "Catalogs", opener=opener), "Catalogs", "Catalog")
    except urllib.error.HTTPError as exc:
        hint = {401: "credentials rejected", 403: "token lacks catalog scope"}.get(exc.code, "see Impact API status")
        print(f"Impact Catalogs returned HTTP {exc.code} ({hint}).")
        return 1
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        print(f"Impact catalog check failed ({type(exc).__name__}).")
        return 1
    print("Impact catalog API responded successfully (HTTP 200).")
    print(f"Catalogs: {catalogs['count']} · response keys: {catalogs['keys']} · paging: {catalogs['paging']}")
    try:
        campaigns = summarize(fetch(sid, token, "Campaigns", opener=opener), "Campaigns", "Campaign", "ContractStatus")
        print(f"Joined campaigns (brands): {campaigns['count']} · by contract status: {campaigns['by_status']}"
              f" · paging: {campaigns['paging']}")
    except urllib.error.HTTPError as exc:
        print(f"Impact Campaigns returned HTTP {exc.code}; campaign count unavailable.")
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        print(f"Impact campaign check failed ({type(exc).__name__}).")
    if catalogs["count"] == 0:
        print("Diagnosis: authentication works but no catalogs are shared with this account. "
              "Catalogs appear after brands approve the partnership and enable product catalogs.")
    print("No products imported or published.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

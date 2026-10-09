"""Read-only Impact catalog availability check. No deals are published."""
import json
import os
import sys
import urllib.error
import urllib.request

def main():
    sid = os.environ.get("IMPACT_ACCOUNT_SID")
    token = os.environ.get("IMPACT_AUTH_TOKEN")
    if not sid or not token:
        print("Missing Impact credentials in GitHub Actions secrets.")
        return 2
    import base64
    credentials = base64.b64encode(f"{sid}:{token}".encode()).decode()
    url = f"https://api.impact.com/Mediapartners/{sid}/Catalogs?PageSize=10"
    request = urllib.request.Request(url, headers={
        "Authorization": f"Basic {credentials}",
        "Accept": "application/json",
        "User-Agent": "BestDealHunter-catalog-check/1.0",
    })
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            payload = json.load(response)
        catalogs = payload.get("Catalogs", payload.get("catalogs", []))
        if isinstance(catalogs, dict):
            catalogs = catalogs.get("Catalog", [])
        if isinstance(catalogs, dict):
            catalogs = [catalogs]
        print(f"Impact catalog API responded successfully. Catalogs in response: {len(catalogs) if isinstance(catalogs, list) else 'unknown'}.")
        print("No products imported or published.")
        return 0
    except urllib.error.HTTPError as exc:
        print(f"Impact API returned HTTP {exc.code}. Check credentials, scopes and account authorization.")
        return 1
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        print(f"Impact catalog check failed ({type(exc).__name__}).")
        return 1

if __name__ == "__main__":
    sys.exit(main())

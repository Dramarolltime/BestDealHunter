"""Read-only eBay Production access check. Prints statuses and eBay's own error ids/messages only;
never prints keys, tokens or listing data, and stores nothing."""
import base64, json, os, urllib.error, urllib.parse, urllib.request

cid, sec = os.environ["EBAY_CLIENT_ID"].strip(), os.environ["EBAY_CLIENT_SECRET"].strip()
print("Keyset:", "Production (PRD)" if "-PRD-" in cid else "Sandbox (SBX)" if "-SBX-" in cid else "unknown")


def call(req):
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def errors(body):
    if "error" in body:  # OAuth style
        return [f"{body.get('error')}: {body.get('error_description', '')}"]
    return [f"{e.get('errorId')} {e.get('domain', '')}/{e.get('category', '')}: {e.get('message', '')}"
            for e in body.get("errors", [])]


auth = base64.b64encode(f"{cid}:{sec}".encode()).decode()
status, body = call(urllib.request.Request(
    "https://api.ebay.com/identity/v1/oauth2/token", method="POST",
    data=urllib.parse.urlencode({"grant_type": "client_credentials",
                                 "scope": "https://api.ebay.com/oauth/api_scope"}).encode(),
    headers={"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"}))
print("1. Application token (Production):", status, "OK" if status == 200 else errors(body))
if status != 200:
    raise SystemExit(0)
token = body["access_token"]
hdr = {"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": "EBAY_US"}

status, body = call(urllib.request.Request(
    "https://api.ebay.com/developer/analytics/v1_beta/rate_limit/?api_context=buy&api_name=browse", headers=hdr))
print("2. eBay's record of this app's Browse API access (Developer Analytics):", status)
if status == 200:
    found = False
    for rl in body.get("rateLimits", []):
        for res in rl.get("resources", []):
            for r in res.get("rates", []):
                found = True
                print(f"   {rl.get('apiName')} {rl.get('apiVersion')} {res.get('name')}: limit {r.get('limit')}"
                      f" per {r.get('timeWindow')}s, remaining {r.get('remaining')}")
    if not found:
        print("   no Browse API resources listed for this app")
else:
    print("  ", errors(body))

status, body = call(urllib.request.Request(
    "https://api.ebay.com/buy/browse/v1/item_summary/search?q=headphones&limit=1", headers=hdr))
print("3. One Browse API search (limit 1, result discarded):", status,
      "OK, access granted" if status == 200 else errors(body))

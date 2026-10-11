#!/usr/bin/env python3
"""Read-only Buffer lookup: list connected channels so BUFFER_CHANNEL_ID can be set.

Sends only GraphQL *queries* (never mutations) to api.buffer.com with BUFFER_API_KEY
from the environment. Prints organization and channel ids, names and services; never
prints the key or request headers, and scrubs the key from any error text.

  BUFFER_API_KEY=... python scripts/buffer_channels.py
"""
import json
import os
import sys
from urllib import error, request

BUFFER_API = "https://api.buffer.com"
TARGET = "best_dealhunter"
ORGS = "query { account { organizations { id name } } }"
CHANNELS = """query Channels($input: ChannelsInput!) {
  channels(input: $input) { id name displayName service }
}"""


class LookupError_(Exception):
    pass


def http_transport(api_key):
    def send(payload):
        req = request.Request(BUFFER_API, data=json.dumps(payload).encode(), method="POST", headers={
            "Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
            "User-Agent": "BestDealHunter/1.0"})
        try:
            with request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except error.HTTPError as exc:
            body = exc.read()[:500].decode("utf-8", "replace")
            raise LookupError_(f"HTTP {exc.code}: {body}") from None
    return send


def call(transport, query, variables=None):
    if query.lstrip().startswith("mutation"):
        raise LookupError_("refusing to send a mutation")
    data = transport({"query": query, "variables": variables or {}})
    if data.get("errors"):
        raise LookupError_("; ".join(str(e.get("message", e)) for e in data["errors"]))
    return data.get("data") or {}


def lookup(transport):
    orgs = (call(transport, ORGS).get("account") or {}).get("organizations") or []
    found = []
    for org in orgs:
        for ch in call(transport, CHANNELS, {"input": {"organizationId": org["id"]}}).get("channels") or []:
            found.append({**ch, "organization": org.get("name") or org["id"]})
    return orgs, found


def is_target(ch):
    return any(str(ch.get(k) or "").lower().lstrip("@") == TARGET for k in ("name", "displayName"))


def report(orgs, channels):
    lines = ["## Buffer channels (read-only lookup)", "", f"Organizations: {len(orgs)}", "",
             "| Channel id | Service | Name | Display name | Organization |", "|---|---|---|---|---|"]
    lines += [f"| `{c.get('id')}` | {c.get('service')} | {c.get('name')} | {c.get('displayName')} | {c['organization']} |"
              for c in channels]
    match = [c for c in channels if is_target(c)]
    insta = [c for c in match if str(c.get("service", "")).lower() == "instagram"] or match
    lines.append("")
    if len(insta) == 1:
        lines.append(f"**BUFFER_CHANNEL_ID for {TARGET}: `{insta[0]['id']}`** ({insta[0].get('service')})")
    elif insta:
        lines.append(f"Several channels match {TARGET}; pick the Instagram one above.")
    else:
        lines.append(f"No channel named {TARGET} found; check the list above.")
    lines.append("")
    lines.append("Only read queries were sent. Nothing was created, scheduled or published.")
    return "\n".join(lines), (insta[0]["id"] if len(insta) == 1 else None)


def main(env=None, transport=None, out=print):
    env = os.environ if env is None else env
    key = env.get("BUFFER_API_KEY", "")
    if not key:
        out("BUFFER_API_KEY is not configured")
        return 2
    scrub = (lambda s: s.replace(key, "***")) if key else (lambda s: s)
    try:
        orgs, channels = lookup(transport or http_transport(key))
    except Exception as exc:  # report safely, never the key
        out(scrub(f"Buffer lookup failed: {type(exc).__name__}: {exc}"))
        return 1
    text, channel_id = report(orgs, channels)
    out(scrub(text))
    summary = env.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(scrub(text) + "\n")
    return 0 if channel_id else 1


if __name__ == "__main__":
    sys.exit(main())

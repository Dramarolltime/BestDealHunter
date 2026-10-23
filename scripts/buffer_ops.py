#!/usr/bin/env python3
"""Buffer operations that keep the Cloudinary -> Buffer pipeline clean. Never publishes.

  python scripts/buffer_ops.py verify            # read-only: report each test draft's state in Buffer
  python scripts/buffer_ops.py delete            # delete test drafts that Buffer confirms are drafts

Only posts listed in buffer-test-drafts.json (status not "deleted") are ever touched. A post is
deleted only when Buffer itself reports status "draft", the expected channel, and no sentAt; any
other state (scheduled, sending, sent, error, wrong channel) is refused and reported for a person.
Buffer's deletePost input shape is read by schema introspection first, because sources disagree
on its field name; an unexpected shape stops the run before anything is deleted. Deletion counts
as done only when a re-read shows the post is gone.

Needs BUFFER_API_KEY (secret) and BUFFER_CHANNEL_ID (variable). Never reads INSTAGRAM_PUBLISH_ENABLED.
Results are printed and written to buffer-cleanup-result.json; the ledger file is updated in the
workspace only (the workflow uploads it as an artifact; a person commits the change in a PR).

verify_hosted_image() is the pre-send check the live publisher calls on a Cloudinary card URL.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, request

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "buffer-test-drafts.json"
RESULT = ROOT / "buffer-cleanup-result.json"
BUFFER_API = "https://api.buffer.com"
CLOUDINARY_PREFIX = "https://res.cloudinary.com/"

GET_POST = "query GetPost($input: PostInput!) { post(input: $input) { id status channelId sentAt dueAt } }"
MUTATION_FIELDS = """{ __type(name: "Mutation") { fields { name
  args { name type { kind name ofType { kind name ofType { kind name } } } }
  type { kind name ofType { kind name } } } } }"""
INPUT_FIELDS = """query InputFields($name: String!) { __type(name: $name) { inputFields { name
  type { kind name ofType { kind name ofType { kind name } } } } } }"""
ID_FIELD_NAMES = ("id", "postId")


class BufferOpsError(Exception):
    """Stop: Buffer's answer or schema was not what we can act on safely."""


def base_type(t):
    """(non_null, kind, name) of a GraphQL type ref, unwrapping NON_NULL (lists are rejected)."""
    non_null = t.get("kind") == "NON_NULL"
    if non_null:
        t = t.get("ofType") or {}
    return non_null, t.get("kind"), t.get("name")


class BufferClient:
    def __init__(self, api_key, transport=None):
        if not api_key:
            raise BufferOpsError("BUFFER_API_KEY is not configured")
        self.api_key, self.transport = api_key, transport or self._http

    def _http(self, payload):
        req = request.Request(BUFFER_API, data=json.dumps(payload).encode(), method="POST", headers={
            "Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
            "User-Agent": "BestDealHunter/1.0"})
        try:
            with request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except error.HTTPError as exc:  # report the status only; never echo headers or the key
            raise BufferOpsError(f"Buffer HTTP {exc.code}") from None

    def call(self, query, variables=None):
        """Return (data, errors). Callers decide which errors mean 'not found'."""
        data = self.transport({"query": query, "variables": variables or {}})
        return data.get("data") or {}, data.get("errors") or []

    def get_post(self, post_id):
        """The post dict, or None when Buffer says it does not exist. Any other error raises."""
        data, errors = self.call(GET_POST, {"input": {"id": post_id}})
        if errors:
            if all(is_not_found(e) for e in errors):
                return None
            raise BufferOpsError("; ".join(str(e.get("message", e)) for e in errors))
        return data.get("post")

    def delete_shape(self):
        """Introspect deletePost. Returns (query, variables_builder). Raises on any unexpected shape."""
        data, errors = self.call(MUTATION_FIELDS)
        if errors:
            raise BufferOpsError(f"schema introspection failed: {errors[0].get('message', errors[0])}")
        fields = ((data.get("__type") or {}).get("fields")) or []
        field = next((f for f in fields if f.get("name") == "deletePost"), None)
        if not field:
            raise BufferOpsError("Buffer schema has no deletePost mutation")
        args = field.get("args") or []
        required = [a for a in args if base_type(a["type"])[0]]
        if len(required) != 1:
            raise BufferOpsError(f"deletePost has {len(required)} required arguments; expected 1")
        arg = required[0]
        _, kind, type_name = base_type(arg["type"])
        if kind == "INPUT_OBJECT":
            data, errors = self.call(INPUT_FIELDS, {"name": type_name})
            if errors:
                raise BufferOpsError(f"could not read {type_name}: {errors[0].get('message', errors[0])}")
            inner = ((data.get("__type") or {}).get("inputFields")) or []
            inner_required = [f for f in inner if base_type(f["type"])[0]]
            if len(inner_required) != 1 or inner_required[0]["name"] not in ID_FIELD_NAMES \
                    or base_type(inner_required[0]["type"])[2] not in ("ID", "String"):
                raise BufferOpsError(f"{type_name} is not a single required id field: "
                                     f"{[f['name'] for f in inner_required]}")
            id_field = inner_required[0]["name"]
            build = lambda post_id: {"v": {id_field: post_id}}  # noqa: E731
            arg_type = f"{type_name}!"
        elif kind == "SCALAR" and arg["name"] in ID_FIELD_NAMES and type_name in ("ID", "String"):
            build = lambda post_id: {"v": post_id}  # noqa: E731
            arg_type = f"{type_name}!"
        else:
            raise BufferOpsError(f"unexpected deletePost argument {arg['name']}: {kind} {type_name}")
        _, ret_kind, _ = base_type(field.get("type") or {})
        selection = "" if ret_kind in ("SCALAR", "ENUM") else " { __typename }"
        query = f"mutation DeletePost($v: {arg_type}) {{ deletePost({arg['name']}: $v){selection} }}"
        return query, build

    def delete_post(self, post_id):
        query, build = self.delete_shape()
        data, errors = self.call(query, build(post_id))
        if errors:
            raise BufferOpsError("deletePost failed: " + "; ".join(str(e.get("message", e)) for e in errors))
        result = data.get("deletePost")
        if isinstance(result, dict) and result.get("__typename", "").endswith("Error"):
            raise BufferOpsError(f"deletePost returned {result['__typename']}")
        if result is False:
            raise BufferOpsError("deletePost returned false")
        return result


def is_not_found(err):
    code = str(((err.get("extensions") or {}).get("code")) or "").upper()
    return code == "NOT_FOUND" or "not found" in str(err.get("message", "")).lower()


def handle(row, client, channel_id, delete, now):
    """Process one ledger row. Returns an outcome dict; never deletes anything but a confirmed draft."""
    post_id = row.get("buffer_post_id")
    out = {"buffer_post_id": post_id, "title": row.get("title"), "checked_at": now}
    if not post_id:
        return {**out, "outcome": "refused", "reason": "ledger row has no buffer_post_id"}
    if row.get("channel_id") != channel_id:
        return {**out, "outcome": "refused",
                "reason": f"ledger channel {row.get('channel_id')!r} != BUFFER_CHANNEL_ID {channel_id!r}"}
    post = client.get_post(post_id)
    if post is None:
        return {**out, "outcome": "absent", "reason": "Buffer reports no such post (already deleted)"}
    state = {"status": post.get("status"), "channelId": post.get("channelId"), "sentAt": post.get("sentAt")}
    out["buffer_state"] = state
    problems = [f"{k}={v!r}" for k, v, want in (("status", state["status"], "draft"),
                                                 ("channelId", state["channelId"], channel_id),
                                                 ("sentAt", state["sentAt"], None)) if v != want]
    if post.get("id") != post_id:
        problems.insert(0, f"id={post.get('id')!r}")
    if problems:
        return {**out, "outcome": "refused",
                "reason": "not a confirmed draft (" + ", ".join(problems) + "); check Buffer by hand"}
    if not delete:
        return {**out, "outcome": "exists_as_draft"}
    client.delete_post(post_id)
    if client.get_post(post_id) is not None:
        return {**out, "outcome": "refused", "reason": "deletePost returned but the post still exists"}
    return {**out, "outcome": "deleted"}


def run(mode, ledger, client, channel_id, now, log=print):
    """Returns (exit_code, results). Exit 0 only if every row ended in the expected state."""
    rows = [r for r in ledger if isinstance(r, dict) and r.get("status") != "deleted"]
    if not rows:
        log("No undeleted test drafts in the ledger; nothing to do.")
        return 0, []
    results, failed = [], False
    for row in rows:
        try:
            result = handle(row, client, channel_id, mode == "delete", now)
        except BufferOpsError as exc:
            result = {"buffer_post_id": row.get("buffer_post_id"), "outcome": "error", "reason": str(exc)}
        results.append(result)
        log(f"{result['buffer_post_id']}: {result['outcome']}" + (f" ({result['reason']})" if result.get("reason") else ""))
        if result["outcome"] in ("deleted", "absent"):
            row.update(status="deleted", deleted_confirmed_at=now, deletion_outcome=result["outcome"])
        failed |= result["outcome"] not in (("deleted", "absent") if mode == "delete" else ("exists_as_draft", "absent"))
    return (1 if failed else 0), results


def verify_hosted_image(url, head=None):
    """Pre-send check for the live publisher: the card must be a reachable Cloudinary JPEG right now."""
    if not isinstance(url, str) or not url.startswith(CLOUDINARY_PREFIX):
        return False, "image is not hosted on res.cloudinary.com"
    status, ctype = (head or _head)(url)
    if status != 200 or not ctype.startswith("image/jpeg"):
        return False, f"Cloudinary image not reachable as JPEG (HTTP {status or 'network error'}, {ctype or 'no type'})"
    return True, "ok"


def _head(url):
    try:
        with request.urlopen(request.Request(url, method="HEAD", headers={"User-Agent": "BestDealHunter/1.0"}),
                             timeout=20) as resp:
            return resp.status, resp.headers.get("Content-Type", "")
    except error.HTTPError as exc:
        return exc.code, ""
    except (error.URLError, TimeoutError, OSError):
        return 0, ""


def main(argv=None, env=None):
    argv = sys.argv[1:] if argv is None else argv
    env = os.environ if env is None else env
    if len(argv) != 1 or argv[0] not in ("verify", "delete"):
        print("usage: buffer_ops.py verify|delete")
        return 2
    channel = env.get("BUFFER_CHANNEL_ID", "")
    if not env.get("BUFFER_API_KEY") or not channel:
        print("Refused: BUFFER_API_KEY secret and BUFFER_CHANNEL_ID variable must both be configured.")
        return 2
    ledger = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    code, results = run(argv[0], ledger, BufferClient(env["BUFFER_API_KEY"]), channel, now)
    RESULT.write_text(json.dumps({"mode": argv[0], "results": results}, indent=2) + "\n", encoding="utf-8")
    LEDGER.write_text(json.dumps(ledger, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":
    sys.exit(main())

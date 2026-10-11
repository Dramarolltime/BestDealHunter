#!/usr/bin/env python3
"""Read-only Buffer schema check: what createPost accepts and how drafts are expressed.

Uses only GraphQL introspection *queries* (no mutation is ever sent; buffer_channels.call
refuses one). Prints the mutation names, the createPost arguments, every input/enum type
reachable from them (e.g. CreatePostInput, SchedulingType, ShareMode) and the Post type
with its status enum, so draft-check can be built on Buffer's real schema. The key is
never printed and is scrubbed from any error text.

  BUFFER_API_KEY=... python scripts/buffer_schema.py
"""
import os
import sys

from buffer_channels import LookupError_, call, http_transport

TYPE_REF = "fragment R on __Type { kind name ofType { kind name ofType { kind name ofType { kind name } } } }"
ROOTS = """query Roots {
  __schema {
    queryType { name fields { name args { name type { ...R } } type { ...R } } }
    mutationType { name fields { name description args { name type { ...R } } type { ...R } } }
  }
}
""" + TYPE_REF
TYPE = """query Type($name: String!) {
  __type(name: $name) {
    name kind description
    fields { name description args { name type { ...R } } type { ...R } }
    inputFields { name description defaultValue type { ...R } }
    enumValues { name description }
    possibleTypes { name }
  }
}
""" + TYPE_REF
WATCH_MUTATIONS = ("post", "draft", "idea", "queue", "schedule")
MAX_TYPES = 60
# Response types draft-check must recognise: post status values and createPost error members.
PRIORITY = ["InstagramPostMetadataInput", "PostTypeInstagram", "InstagramPostType", "PostType", "PostStatus", "PostActionPayload", "MutationError", "InvalidInputError", "LimitReachedError",
            "RestProxyError", "UnexpectedError", "NotFoundError", "UnauthorizedError", "PostActionSuccess"]
BUILTIN = {"String", "Int", "Float", "Boolean", "ID", "DateTime"}


def type_str(t):
    if not t:
        return "?"
    if t["kind"] == "NON_NULL":
        return type_str(t.get("ofType")) + "!"
    if t["kind"] == "LIST":
        return "[" + type_str(t.get("ofType")) + "]"
    return t.get("name") or "?"


def named(t):
    while t and not t.get("name"):
        t = t.get("ofType")
    return t.get("name") if t else None


def explore(transport):
    roots = call(transport, ROOTS)["__schema"]
    mutations = (roots.get("mutationType") or {}).get("fields") or []
    queries = (roots.get("queryType") or {}).get("fields") or []
    relevant = [m for m in mutations if any(w in m["name"].lower() for w in WATCH_MUTATIONS)]
    todo = []
    for field in relevant + [q for q in queries if q["name"] in ("post", "posts")]:
        todo += [named(a["type"]) for a in field.get("args") or []] + [named(field["type"])]
    todo = PRIORITY + todo + ["Post"]
    seen, types = set(), []
    while todo and len(types) < MAX_TYPES:
        name = todo.pop(0)
        if not name or name in seen or name in BUILTIN or name.startswith("__"):
            continue
        seen.add(name)
        t = call(transport, TYPE, {"name": name}).get("__type")
        if not t:
            continue
        types.append(t)
        if t["kind"] in ("INPUT_OBJECT", "UNION", "INTERFACE") or name in ("Post",) or name.endswith("Success") or name.endswith("Error"):
            for f in (t.get("inputFields") or []) + (t.get("fields") or []):
                todo.append(named(f["type"]))
            todo += [p["name"] for p in t.get("possibleTypes") or []]
    return mutations, relevant, queries, types


def render(mutations, relevant, queries, types):
    out = ["## Buffer schema check (read-only introspection)", "",
           f"Mutations in schema ({len(mutations)}): " + ", ".join(f"`{m['name']}`" for m in mutations), "",
           "Queries: " + ", ".join(f"`{q['name']}`" for q in queries), "", "### Post-related mutations", ""]
    for m in relevant:
        args = ", ".join(f"{a['name']}: {type_str(a['type'])}" for a in m.get("args") or [])
        out.append(f"- `{m['name']}({args}): {type_str(m['type'])}`" + (f": {m['description']}" if m.get("description") else ""))
    for t in types:
        out += ["", f"### {t['kind'].lower()} `{t['name']}`" + (f": {t['description']}" if t.get("description") else ""), ""]
        for f in t.get("inputFields") or []:
            default = f" = {f['defaultValue']}" if f.get("defaultValue") is not None else ""
            out.append(f"- `{f['name']}: {type_str(f['type'])}{default}`" + (f": {f['description']}" if f.get("description") else ""))
        for f in t.get("fields") or []:
            out.append(f"- `{f['name']}: {type_str(f['type'])}`" + (f": {f['description']}" if f.get("description") else ""))
        for e in t.get("enumValues") or []:
            out.append(f"- `{e['name']}`" + (f": {e['description']}" if e.get("description") else ""))
        if t.get("possibleTypes"):
            out.append("- one of: " + ", ".join(f"`{p['name']}`" for p in t["possibleTypes"]))
    out += ["", "Only introspection queries were sent. Nothing was created, drafted, scheduled or published."]
    return "\n".join(out)


def main(env=None, transport=None, out=print):
    env = os.environ if env is None else env
    key = env.get("BUFFER_API_KEY", "")
    if not key:
        out("BUFFER_API_KEY is not configured")
        return 2
    scrub = lambda s: s.replace(key, "***")  # noqa: E731
    try:
        text = render(*explore(transport or http_transport(key)))
    except Exception as exc:  # report safely, never the key
        out(scrub(f"Buffer schema check failed: {type(exc).__name__}: {exc}"))
        return 1
    out(scrub(text))
    summary = env.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(scrub(text) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

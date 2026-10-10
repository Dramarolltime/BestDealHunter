#!/usr/bin/env python3
"""BestDealHunter hourly maintenance monitor (phase 1: monitoring and reporting only).

Checks the public website, hosted Cloudinary images, deal freshness, the pending
queue and recent GitHub Actions runs on main. It then:
  * opens one GitHub issue per new problem (label `maintenance`), closes it with a
    comment when the problem clears, and never duplicates an open incident;
  * rewrites the body of a single status issue (label `maintenance-status`);
  * optionally posts a daily digest comment on that status issue.

It never edits deals, uploads images, publishes posts, or calls paid APIs.
Only the workflow's GITHUB_TOKEN is used; no other credentials are read.
"""
import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib import error, parse, request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from validate_deals import RULES  # noqa: E402

MARKER = "<!-- bdh-maintenance:{key} -->"
INCIDENT_LABEL = "maintenance"
STATUS_LABEL = "maintenance-status"
FAILED = {"failure", "timed_out", "startup_failure"}
USER_AGENT = "BestDealHunter-maintenance/1.0"


def check(key, title, ok, details, severity="error"):
    return {"key": key, "title": title, "ok": ok, "details": details, "severity": severity}


# ---------------------------------------------------------------- HTTP helpers

def http_get(url, method="GET", timeout=20, attempts=3):
    """Return (status, content_type, body_bytes); status 0 means network error."""
    last = (0, "", b"")
    for _ in range(attempts):
        req = request.Request(url, method=method, headers={"User-Agent": USER_AGENT})
        try:
            with request.urlopen(req, timeout=timeout) as resp:
                body = resp.read(5_000_000) if method == "GET" else b""
                return resp.status, resp.headers.get("Content-Type", ""), body
        except error.HTTPError as exc:
            last = (exc.code, exc.headers.get("Content-Type", "") if exc.headers else "", b"")
            if exc.code < 500:
                return last
        except (error.URLError, TimeoutError, OSError):
            last = (0, "", b"")
    return last


class GitHub:
    def __init__(self, repo, token, api="https://api.github.com"):
        self.repo, self.token, self.api = repo, token, api.rstrip("/")

    def call(self, method, path, payload=None, query=None):
        url = f"{self.api}/repos/{self.repo}{path}"
        if query:
            url += "?" + parse.urlencode(query)
        data = json.dumps(payload).encode() if payload is not None else None
        req = request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        })
        with request.urlopen(req, timeout=30) as resp:
            body = resp.read()
        return json.loads(body) if body else None

    def ensure_label(self, name, color, description):
        try:
            self.call("POST", "/labels", {"name": name, "color": color, "description": description})
        except error.HTTPError as exc:
            if exc.code != 422:  # 422 = already exists
                raise

    def open_issues(self, label):
        issues = self.call("GET", "/issues", query={"labels": label, "state": "open", "per_page": 100}) or []
        return [i for i in issues if "pull_request" not in i]


# ---------------------------------------------------------------- checks (pure)

def check_site(status, content_type, body, site_url):
    ok = status == 200 and b"BestDealHunter" in body
    return check("site", "Website unavailable", ok,
                 f"`{site_url}` returned HTTP {status or 'network error'}"
                 + ("" if ok or status != 200 else " without the expected page content"))


def check_public_feed(status, body, url):
    try:
        deals = json.loads(body) if status == 200 else None
    except ValueError:
        deals = None
    ok = isinstance(deals, list)
    return check("public-feed", "Published deals.json unreadable", ok,
                 f"`{url}` returned HTTP {status or 'network error'}" + ("" if ok else "; expected a JSON array")), deals


def deal_age_days(deal, today):
    try:
        return (today - date.fromisoformat(deal["verified_date"])).days
    except (KeyError, TypeError, ValueError):
        return None


def check_freshness(deals, today, max_age=RULES["max_verification_age_days"]):
    """Stale = older than the validator allows (should be unpublished or re-verified)."""
    stale, expiring = [], []
    for deal in deals:
        if not isinstance(deal, dict):
            continue
        age = deal_age_days(deal, today)
        expires = deal.get("expires_date")
        past_expiry = isinstance(expires, str) and expires < today.isoformat()
        label = f"{deal.get('store', '?')}: {deal.get('title', 'Untitled')} (verified {deal.get('verified_date', 'unknown')})"
        if age is None or age > max_age or past_expiry:
            stale.append(label + (f", expired {expires}" if past_expiry else ""))
        elif age == max_age:
            expiring.append(label)
    results = [check("stale-deals", "Published deals are stale or expired", not stale,
                     "Would unpublish (re-verify or remove):\n" + "\n".join(f"- {s}" for s in stale) if stale
                     else f"All {len(deals)} published deals verified within {max_age} days.")]
    if expiring:
        results.append(check("expiring-deals", "Deals go stale within 24h", False,
                             "\n".join(f"- {s}" for s in expiring), severity="warning"))
    return results


def check_hosted_images(hosted, head):
    """`head(url)` -> (status, content_type). Only Cloudinary URLs are checked."""
    broken = []
    for entry in hosted if isinstance(hosted, list) else []:
        url = entry.get("cloudinary_url", "") if isinstance(entry, dict) else ""
        if not url.startswith("https://res.cloudinary.com/"):
            continue
        status, ctype = head(url)
        if status != 200 or not ctype.startswith("image/"):
            broken.append(f"- {entry.get('title', url)}: HTTP {status or 'network error'} `{url}`")
    return check("cloudinary", "Cloudinary-hosted images unreachable", not broken,
                 "\n".join(broken) if broken else f"{len(hosted or [])} hosted image(s) reachable.")


def check_unhosted(deals, hosted):
    hosted_paths = {h.get("image") for h in hosted or [] if isinstance(h, dict)}
    missing = [f"- {d.get('title')} (`{d.get('image')}`)" for d in deals
               if isinstance(d, dict) and isinstance(d.get("image"), str)
               and d["image"].startswith("images/") and d["image"] not in hosted_paths]
    return check("unhosted-images", "Deal images not hosted on Cloudinary", not missing,
                 "\n".join(missing) if missing else "Every local deal image has a Cloudinary URL.",
                 severity="warning")


def check_pending(queue):
    ok = isinstance(queue, list) and all(isinstance(d, dict) for d in queue)
    return check("pending-queue", "pending_deals.json is malformed", ok,
                 f"{len(queue)} deal(s) awaiting review." if ok else "Expected a JSON array of objects.")


def latest_runs(runs):
    """Latest completed run per workflow, newest first."""
    latest = {}
    for run in sorted(runs, key=lambda r: r.get("created_at", ""), reverse=True):
        if run.get("status") != "completed":
            continue
        latest.setdefault(run.get("path") or run.get("name"), run)
    return latest


def check_workflows(runs):
    results = []
    for path, run in sorted(latest_runs(runs).items()):
        name = run.get("name", path)
        ok = run.get("conclusion") not in FAILED
        results.append(check(f"workflow:{path}", f"Workflow failing: {name}", ok,
                             f"Latest run on main: **{run.get('conclusion')}** ({run.get('event')}, "
                             f"{run.get('created_at')}) {run.get('html_url', '')}"))
    return results


# ---------------------------------------------------------------- reporting

def render_status(results, now, site_url, run_url):
    problems = [r for r in results if not r["ok"] and r["severity"] == "error"]
    warnings = [r for r in results if not r["ok"] and r["severity"] == "warning"]
    healthy = [r for r in results if r["ok"]]
    head = "🔴 Problems detected" if problems else ("🟡 Warnings" if warnings else "🟢 All checks passing")
    lines = [MARKER.format(key="status"), f"## {head}", "",
             f"Last checked: **{now:%Y-%m-%d %H:%M} UTC** · Site: {site_url}" + (f" · [Run log]({run_url})" if run_url else ""),
             "", "Phase 1 monitor: reporting only. No deals edited, nothing published, no paid APIs called.", ""]
    for heading, group, icon in (("Problems", problems, "❌"), ("Warnings", warnings, "⚠️"), ("Healthy", healthy, "✅")):
        if group:
            lines += [f"### {heading}", ""]
            for r in group:
                lines.append(f"<details><summary>{icon} {r['title'] if not r['ok'] else r['key']}</summary>\n\n{r['details']}\n</details>")
            lines.append("")
    lines.append("_Updated hourly by `.github/workflows/maintenance-monitor.yml`._")
    return "\n".join(lines)


def plan_incidents(results, open_issues):
    """Return (to_open, to_close) without touching the network."""
    by_key = {}
    for issue in open_issues:
        for r in results:
            if MARKER.format(key=r["key"]) in (issue.get("body") or ""):
                by_key[r["key"]] = issue
    # Incidents whose check no longer reports (e.g. a deleted workflow) are left for a human to close.
    to_open = [r for r in results if not r["ok"] and r["severity"] == "error" and r["key"] not in by_key]
    to_close = [(by_key[r["key"]], r) for r in results if r["ok"] and r["key"] in by_key]
    return to_open, to_close


def incident_body(result, run_url):
    return "\n".join([
        MARKER.format(key=result["key"]),
        f"**Detected by the maintenance monitor** ({run_url or 'local run'}).", "",
        result["details"], "",
        "This issue closes automatically when the check passes again.",
        "Phase 1: no automated fix is attempted. To request an AI investigation once phase 2 is approved, "
        "a maintainer adds the `claude-investigate` label.",
    ])


def sync_issues(gh, results, status_body, digest, run_url, log=print):
    gh.ensure_label(INCIDENT_LABEL, "d93f0b", "Opened automatically by the maintenance monitor")
    gh.ensure_label(STATUS_LABEL, "0e8a16", "Rolling BestDealHunter health report")
    to_open, to_close = plan_incidents(results, gh.open_issues(INCIDENT_LABEL))
    for r in to_open:
        issue = gh.call("POST", "/issues", {"title": f"[maintenance] {r['title']}",
                                            "body": incident_body(r, run_url), "labels": [INCIDENT_LABEL]})
        log(f"Opened incident #{issue['number']}: {r['title']}")
    for issue, r in to_close:
        gh.call("POST", f"/issues/{issue['number']}/comments",
                {"body": f"✅ Check passing again: {r['details']}\n\nClosing automatically ({run_url or 'local run'})."})
        gh.call("PATCH", f"/issues/{issue['number']}", {"state": "closed", "state_reason": "completed"})
        log(f"Closed recovered incident #{issue['number']}")
    status = gh.open_issues(STATUS_LABEL)
    if status:
        number = status[0]["number"]
        gh.call("PATCH", f"/issues/{number}", {"body": status_body})
    else:
        number = gh.call("POST", "/issues", {"title": "BestDealHunter maintenance status",
                                             "body": status_body, "labels": [STATUS_LABEL]})["number"]
    log(f"Updated status issue #{number}")
    if digest:
        gh.call("POST", f"/issues/{number}/comments", {"body": "### Daily digest\n\n" + status_body.split("\n", 1)[1]})
        log(f"Posted daily digest on #{number}")


# ---------------------------------------------------------------- main

def run_checks(site_url, gh, today):
    base = site_url.rstrip("/") + "/"
    status, ctype, body = http_get(base)
    results = [check_site(status, ctype, body, base)]
    f_status, _, f_body = http_get(base + "deals.json")
    feed_check, deals = check_public_feed(f_status, f_body, base + "deals.json")
    results.append(feed_check)
    if deals is None:  # fall back to the repository copy so freshness is still reported
        deals = json.loads((ROOT / "deals.json").read_text(encoding="utf-8"))
    results += check_freshness(deals, today)
    hosted = json.loads((ROOT / "cloudinary-images.json").read_text(encoding="utf-8"))
    results.append(check_hosted_images(hosted, lambda u: http_get(u, method="HEAD")[:2]))
    results.append(check_unhosted(deals, hosted))
    results.append(check_pending(json.loads((ROOT / "pending_deals.json").read_text(encoding="utf-8"))))
    if gh:
        since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
        runs = gh.call("GET", "/actions/runs", query={"branch": "main", "per_page": 100,
                                                      "created": f">={since}", "exclude_pull_requests": "true"})
        results += check_workflows((runs or {}).get("workflow_runs", []))
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--site-url", default=os.environ.get("SITE_URL") or "https://dramarolltime.github.io/BestDealHunter/")
    parser.add_argument("--dry-run", action="store_true", help="report only; never write GitHub issues")
    parser.add_argument("--digest", action="store_true", help="also post the daily digest comment")
    args = parser.parse_args(argv)

    repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    gh = GitHub(repo, token) if repo and token else None
    run_url = (f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{repo}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
               if repo and os.environ.get("GITHUB_RUN_ID") else "")
    now = datetime.now(timezone.utc)
    results = run_checks(args.site_url, gh, now.date())
    status_body = render_status(results, now, args.site_url, run_url)
    print(status_body)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(status_body + "\n")
    if args.dry_run or not gh:
        to_open, _ = plan_incidents(results, [])
        print(f"\nDry run: would open/keep {len(to_open)} incident(s); no GitHub issues written.")
        return 0
    sync_issues(gh, results, status_body, args.digest, run_url)
    return 0


if __name__ == "__main__":
    sys.exit(main())

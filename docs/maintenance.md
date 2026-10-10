# BestDealHunter 24/7 maintenance system

Goal: keep the site, deal feed, image hosting and (later) Instagram publishing healthy while nobody is watching, and make every action reviewable by Alpha and the owner in GitHub.

The system is rolled out in **gated phases**. Each phase after phase 1 needs explicit owner approval before it's enabled. Until then it exists only as a design in this document.

| Phase | Scope | Status | Spends money? | Writes to `main`? |
|---|---|---|---|---|
| **1. Monitor and report** | Hourly checks, incident issues, status issue, daily digest | **This PR** | No | No |
| 2. Claude investigation | Claude Code (Pro subscription) investigates an incident and opens a PR with tests | Interactive sessions in use; Action design only, needs approval | No (included in Claude Pro) | No (PRs only, no auto-merge) |
| 3. Deal expiry | Hide or remove stale deals from publication | Design only, needs approval | No | Via PR or a guarded bot commit |
| 4. Cloudinary upkeep | Re-upload missing or broken hosted images, single uploader | Design only, needs approval | Free tier | Via PR |
| 5. Instagram via Buffer | Publish verified deals with dedupe and a kill switch | Design only, needs approval | No (existing Buffer connection) | Post log via PR/commit |

---

## Phase 1: monitoring and reporting (implemented)

`.github/workflows/maintenance-monitor.yml` runs `scripts/maintenance/monitor.py` **hourly** (`:07`) and posts a **daily digest** (13:03 UTC).

### Checks

| Check | Key | Fails when | Opens an issue? |
|---|---|---|---|
| Website | `site` | Site URL not HTTP 200 or page content missing (3 attempts) | Yes |
| Published feed | `public-feed` | Live `deals.json` unreachable or not a JSON array | Yes |
| Deal freshness | `stale-deals` | Never fails: reports active vs archived deals. Archived deals (stale, past `expires_date`, or `status: archived`) stay on the site under **Past Deals** labelled "Expired · price not verified" (archive policy, #1) | No |
| Feed validity | `feed-valid` | Published `deals.json` fails `validate_feed` (structure, evidence, future dates) | Yes |
| No active deals | `no-active-deals` | Every published deal is archived | No (warning) |
| Archiving soon | `expiring-deals` | A deal moves to Past Deals within 24h | No (warning) |
| Cloudinary | `cloudinary` | A URL in `cloudinary-images.json` isn't HTTP 200 `image/*` | Yes |
| Unhosted images | `unhosted-images` | A deal's local image has no Cloudinary URL | No (warning) |
| Pending queue | `pending-queue` | `pending_deals.json` isn't an array of objects | Yes |
| Workflows | `workflow:<file>` | The **latest completed** run on `main` (last 7 days) is `failure`, `timed_out` or `startup_failure`. `cancelled` is ignored. | Yes, one per workflow |

The job also runs the full unit-test suite first, so a broken monitor or validator fails loudly in the Actions tab.

### Issue behavior
- **One issue per problem.** Incidents carry the `maintenance` label and a hidden marker `<!-- bdh-maintenance:<key> -->`. An already-open incident is never duplicated, so hourly runs don't spam.
- **Auto-close.** When a check passes again, the monitor comments and closes the incident.
- **Status issue.** A single `maintenance-status` issue, *BestDealHunter maintenance status*, has its body rewritten every hour. This is where Alpha reviews health.
- **Daily digest.** One comment per day on the status issue, which leaves a dated history.
- **Warnings** appear in the status issue only.

### Safety
- Permissions: `contents: read`, `actions: read`, `issues: write`. The workflow cannot push code.
- Uses only the built-in `GITHUB_TOKEN`. No secrets are read or printed.
- Runs from branches, pull requests and manual runs default to **dry-run**: the report goes to the job summary and no issues are written. Issues are written only by scheduled runs on `main`, or by a manual run on `main` with *dry_run* unchecked.
- Limitation: if GitHub Actions itself is down, nothing runs. An optional free external uptime monitor (e.g. UptimeRobot) can cover that independently.

### Configuration
- Optional repository **variable** `SITE_URL` (Settings → Secrets and variables → Actions → Variables). Default: `https://dramarolltime.github.io/BestDealHunter/`.

### Expected first findings once merged
- `Hourly deal checks` failing on main (Pillow), until PR #2 merges.
- From **2026-10-12** the 2026-10-09 deals move to Past Deals. That's reported in the status issue and is not an incident.
- `unhosted-images` warning for the Delsey illustration. This is intentional: the uploader skips `images/generated/`.

---

## Phase 2: Claude Code investigation (design, needs approval)

**Decision (owner + Alpha, 2026-10-10): use the existing $20/month Claude Pro subscription only.** No Anthropic API key, no paid API usage and no new subscriptions. Every option below runs on the Pro plan's included Claude Code usage and adds no charges. When the plan's usage limit is reached, work pauses until the limit resets rather than billing more. Keep any optional pay-as-you-go "extra usage" setting in the Claude account **turned off**, so reaching the limit can never create a charge.

**Option A: interactive Claude Code sessions (in use now, recommended default).** The owner opens Claude Code (web, desktop or CLI) and points it at an open `maintenance` incident. Claude reproduces the failure, fixes it on a `claude/*` branch, runs the tests and opens a PR for Alpha. This needs no secrets in the repository.

**Option B: Claude Code GitHub Action with the subscription token (optional, later).** The documented `anthropics/claude-code-action@v1` supports Pro subscriptions through `claude_code_oauth_token`. Its docs say that with an OAuth token, "runs use your Claude subscription instead of API billing." Setup is done **by the owner** and never by Claude:
1. Run `claude setup-token` locally.
2. Save the token as the repository secret `CLAUDE_CODE_OAUTH_TOKEN`.
3. Confirm the Claude GitHub App is installed on the repo.

Runs share the same Pro usage limits as interactive sessions, so each one is capped with `--max-turns` and only starts on a human-applied label.

**Trigger (option B):** a maintainer adds the `claude-investigate` label to a `maintenance` incident. The label is a deliberate human gate. It also works around a GitHub limitation: issues opened by the monitor's `GITHUB_TOKEN` cannot trigger other workflows, so investigation can never start on its own.

**Flow (both options):**
1. Reproduce the failure.
2. Make the smallest fix.
3. Run `python -m unittest discover -s scripts -p 'test_*.py'` and `python scripts/validate_deals.py`. **No PR is opened unless they pass.**
4. Open a PR (draft for option B) from a `claude/*` branch that links the incident, and comment a summary on the incident.

**Guardrails:**
- No auto-merge. Alpha or the owner approves and merges.
- Branch protection on `main`: require PR plus passing checks.
- Never an `ANTHROPIC_API_KEY`.
- `--max-turns` cap, a 30-minute job timeout, and one concurrent run.
- The job gets no Cloudinary, Buffer or Impact secrets.

**Draft workflow for option B (not active; kept out of `.github/workflows/`):**
```yaml
on:
  issues:
    types: [labeled]
permissions: { contents: write, pull-requests: write, issues: write, actions: read, id-token: write }
concurrency: { group: claude-investigate, cancel-in-progress: false }
jobs:
  investigate:
    if: github.event.label.name == 'claude-investigate'
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4
      - uses: anthropics/claude-code-action@v1   # pin to a commit SHA when enabling
        with:
          claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}   # Pro subscription, not API billing
          prompt: |
            Investigate maintenance incident #${{ github.event.issue.number }}.
            Reproduce, make the minimal fix, run the unit tests and validator,
            and open a draft PR only if they pass. Never touch secrets, deal
            prices, verification dates, or publishing settings. Do not merge.
          claude_args: --max-turns 25
```

## Phase 3: deal archive (implemented, owner policy in #1)
- Published deals are **never deleted**. Once stale, past `expires_date`, or marked `status: "archived"`, a deal moves to the site's **Past Deals (expired)** tab. There it shows "Expired · price not verified" and "Price at time of posting", and it's excluded from the active tabs, filters and Instagram eligibility.
- `validate_feed` accepts archived entries, so the hourly check doesn't fail because history is kept. The strict `validate` (approvals, refreshes, cards, posts) still requires active deals.
- Verification dates are never refreshed automatically. Reactivating a deal requires real re-verification.

## Phase 4: Cloudinary and website upkeep (design, needs approval)
- Consolidate the three uploaders into one: content-hash `public_id`, include `images/generated/`, and **merge** results into `cloudinary-images.json` instead of overwriting it.
- On a `cloudinary` incident, re-upload the missing asset from the repo copy and open a PR updating the mapping.
- Keep all pushes in a single orchestrating workflow with one concurrency group (see the audit: bot pushes don't chain).

## Phase 5: Instagram publishing via Buffer (design, needs approval)
Per Alpha: reuse the existing Buffer connection (Instagram channel `best_dealhunter`).
- **Kill switch.** Repo variable `INSTAGRAM_PUBLISH_ENABLED` must be `true`. Default and missing both mean off. The job also uses a protected GitHub **environment** `instagram` with required reviewers until hands-off posting is approved.
- **Eligibility.** Only deals that pass `validate_deals.py`, are `checkout_verified`, have a branded graphic on Cloudinary, and use no Amazon screenshots.
- **Dry-run first.** Build the exact Buffer payload (image URL, caption with FTC disclosure, "price may change") and write it to the job summary. No API call.
- **Duplicate prevention.** `posted.json` records deal id + URL hash → Buffer update id + status. A deal already present is never re-queued, and a concurrency group prevents parallel posting.
- **Success = Buffer reports `sent`.** The job re-checks status later and records `sent`/`error`. It never reports a post as published from the create call alone.
- Authentication method for GitHub Actions → Buffer is **to be confirmed by Alpha** (the token is stored only as a secret).

---

## Expected monthly costs

| Item | Assumption | Estimated cost / month |
|---|---|---|
| GitHub Actions: monitor (phase 1) | 720 hourly runs + 30 digests, ~1 billed min each | **$0** on a public repo (unlimited minutes); ~750 of the 2,000 free minutes on a private Free plan |
| GitHub Actions: existing workflows | hourly checks + publication gate + 6-hourly jobs (~2,300 short runs) | $0 public. Private: may exceed 2,000 free min → ~$0.008/min overage (verify current rates) |
| GitHub Pages | Static site | $0 |
| Cloudinary | A few dozen images, low bandwidth | $0 on the free plan (credit-based allowance) |
| Claude Code (phase 2) | Existing Claude Pro subscription; interactive sessions, optionally the GitHub Action via `CLAUDE_CODE_OAUTH_TOKEN` | **$0 extra** (already paying $20/month). Work pauses at the plan's usage limit; no API billing. |
| Buffer (phase 5) | Existing Buffer connection, 1 Instagram channel | $0 on the current plan. **No upgrade will be purchased**; if the free plan's queue limit is hit, posts wait. |
| AI image provider | Not used. Deterministic templates per Alpha | $0 (future: capped budget) |
| External uptime monitor (optional) | 1 HTTP check, 5-min interval | $0 (free tiers) |
| **Total** | Phase 1 only | **$0** |
| | Phases 1–5 | **$0 beyond the existing Claude Pro subscription** |

Policy: no paid API services, no new subscriptions, no plan upgrades. Re-check Buffer, Cloudinary and GitHub free-tier limits before enabling a phase. If a limit would be exceeded, the system degrades (waits or skips) instead of buying capacity.

## Credentials (names only; values live in GitHub Actions secrets, never in code, logs or issues)

| Name | Type | Used by | Status |
|---|---|---|---|
| `GITHUB_TOKEN` | automatic | monitor (phase 1) | built in, nothing to configure |
| `SITE_URL` | variable (optional) | monitor | optional |
| `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET` | secrets | uploaders (phase 4) | already configured |
| `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`, Pro subscription) | secret | optional Claude Code Action (phase 2, option B) | **owner creates it only if option B is approved**; `ANTHROPIC_API_KEY` is not used |
| Buffer access token (name TBD, e.g. `BUFFER_ACCESS_TOKEN`) | secret | publisher (phase 5) | **auth method to confirm** |
| `INSTAGRAM_PUBLISH_ENABLED` | variable (kill switch) | publisher | create as `false` |
| `IMPACT_ACCOUNT_SID`, `IMPACT_AUTH_TOKEN`, `DEAL_FEED_URL` | secrets | discovery | existing; retailer access not yet approved |

Rules: scripts read secrets only from environment variables, print only success or HTTP status codes, and the monitor job is never given secrets beyond `GITHUB_TOKEN`.

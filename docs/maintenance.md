# BestDealHunter 24/7 maintenance system

Goal: keep the site, deal feed, image hosting and (later) Instagram publishing healthy while nobody is watching, and make every action reviewable by Alpha and the owner in GitHub.

The system is rolled out in **gated phases**. Each phase after phase 1 needs explicit owner approval before it's enabled. Until then it exists only as a design in this document.

| Phase | Scope | Status | Spends money? | Writes to `main`? |
|---|---|---|---|---|
| **1. Monitor and report** | Hourly checks, incident issues, status issue, daily digest | **This PR** | No | No |
| 2. Claude investigation | Claude Code investigates a labeled incident and opens a **draft PR** with tests | Design only, needs approval | Yes (API usage) | No (PRs only, no auto-merge) |
| 3. Deal expiry | Hide or remove stale deals from publication | Design only, needs approval | No | Via PR or a guarded bot commit |
| 4. Cloudinary upkeep | Re-upload missing or broken hosted images, single uploader | Design only, needs approval | Free tier | Via PR |
| 5. Instagram via Buffer | Publish verified deals with dedupe and a kill switch | Design only, needs approval | Possibly (Buffer plan) | Post log via PR/commit |

---

## Phase 1: monitoring and reporting (implemented)

`.github/workflows/maintenance-monitor.yml` runs `scripts/maintenance/monitor.py` **hourly** (`:07`) and posts a **daily digest** (13:03 UTC).

### Checks

| Check | Key | Fails when | Opens an issue? |
|---|---|---|---|
| Website | `site` | Site URL not HTTP 200 or page content missing (3 attempts) | Yes |
| Published feed | `public-feed` | Live `deals.json` unreachable or not a JSON array | Yes |
| Deal freshness | `stale-deals` | Any published deal is older than `max_verification_age_days` (`scripts/deal_rules.json`) or past `expires_date`. Lists what **would be unpublished**. | Yes |
| Going stale soon | `expiring-deals` | A deal reaches the freshness limit within 24h | No (warning) |
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
- Runs from branches and manual runs default to **dry-run**: the report goes to the job summary and no issues are written. Issues are written only by scheduled runs on `main`, or by a manual run on `main` with *dry_run* unchecked.
- Limitation: if GitHub Actions itself is down, nothing runs. An optional free external uptime monitor (e.g. UptimeRobot) can cover that independently.

### Configuration
- Optional repository **variable** `SITE_URL` (Settings → Secrets and variables → Actions → Variables). Default: `https://dramarolltime.github.io/BestDealHunter/`.

### Expected first findings once merged
- `Hourly deal checks` failing on main (Pillow), until PR #2 merges.
- `stale-deals` from **2026-10-12**, when the 2026-10-09 verifications pass the 2-day limit.
- `unhosted-images` warning for the Delsey illustration. This is intentional: the uploader skips `images/generated/`.

---

## Phase 2: Claude Code investigation (design, needs approval)

**Trigger:** a maintainer adds the `claude-investigate` label to a `maintenance` incident. The label is a deliberate human gate. It also works around a GitHub limitation: issues opened by the monitor's `GITHUB_TOKEN` cannot trigger other workflows, so investigation can never start on its own.

**Flow:** the `anthropics/claude-code-action` workflow (`on: issues: [labeled]`, if label = `claude-investigate`) reads the incident, the linked run logs and the repo, then:
1. reproduces the failure locally in the runner;
2. makes the smallest fix;
3. runs `python -m unittest discover -s scripts -p 'test_*.py'` and `python scripts/validate_deals.py`. **No PR is opened unless they pass**;
4. opens a **draft PR** to a `claude/fix-*` branch that links the incident, and comments a summary on the incident.

**Guardrails:**
- No auto-merge. A human approves and merges.
- Branch protection on `main`: require PR plus passing checks.
- `--max-turns` cap and a hard monthly spend limit set in the Anthropic Console.
- Restricted tools: no secrets in the prompt, and no access to the Cloudinary, Buffer or Impact secrets in that job.
- At most one concurrent investigation (`concurrency` group).

**Draft workflow (not active; kept out of `.github/workflows/`).** Pin and verify the action's current inputs before enabling:
```yaml
on:
  issues:
    types: [labeled]
permissions: { contents: write, pull-requests: write, issues: write, actions: read }
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
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          prompt: |
            Investigate maintenance incident #${{ github.event.issue.number }}.
            Reproduce, make the minimal fix, run the unit tests and validator,
            and open a draft PR only if they pass. Never touch secrets, deals
            prices, or publishing settings. Do not merge.
          claude_args: --max-turns 30
```

## Phase 3: deal expiry (design, needs approval)
- Simplest and safest: make `index.html` hide deals older than the freshness limit or past `expires_date` (client-side, no data deletion). The data stays for re-verification.
- Alternative: a scheduled job moves stale deals from `deals.json` to `expired_deals.json` and opens a PR (or commits, once trusted).
- Never deletes evidence. Re-verification needs approved retailer data access, which isn't established yet.

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
| Claude Code investigations (phase 2) | ~20 incidents/month, ~0.5M input + 20K output tokens each | **~$15–25** with Claude Sonnet 5.5 ($2/$10 per M tokens). **~$30–50** with Claude Opus 5.5 ($4/$20). Prompt caching lowers this. Set a hard Console spend limit (e.g. $50). |
| Buffer (phase 5) | 1 Instagram channel | $0 on the free plan if its scheduled-post limit suffices; otherwise a paid per-channel plan (verify current Buffer pricing) |
| AI image provider | Not used. Deterministic templates per Alpha | $0 (future: capped budget) |
| External uptime monitor (optional) | 1 HTTP check, 5-min interval | $0 (free tiers) |
| **Total** | Phase 1 only | **$0** |
| | Phases 1–5 with Sonnet 5.5 | roughly **$15–40** |

Token prices are Anthropic list prices as of 2026-10. Re-check Buffer, Cloudinary and GitHub pricing pages before enabling a phase.

## Credentials (names only; values live in GitHub Actions secrets, never in code, logs or issues)

| Name | Type | Used by | Status |
|---|---|---|---|
| `GITHUB_TOKEN` | automatic | monitor (phase 1) | built in, nothing to configure |
| `SITE_URL` | variable (optional) | monitor | optional |
| `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET` | secrets | uploaders (phase 4) | already configured |
| `ANTHROPIC_API_KEY` (or the Claude GitHub App + `CLAUDE_CODE_OAUTH_TOKEN`) | secret | Claude investigation (phase 2) | **needs owner approval** |
| Buffer access token (name TBD, e.g. `BUFFER_ACCESS_TOKEN`) | secret | publisher (phase 5) | **auth method to confirm** |
| `INSTAGRAM_PUBLISH_ENABLED` | variable (kill switch) | publisher | create as `false` |
| `IMPACT_ACCOUNT_SID`, `IMPACT_AUTH_TOKEN`, `DEAL_FEED_URL` | secrets | discovery | existing; retailer access not yet approved |

Rules: scripts read secrets only from environment variables, print only success or HTTP status codes, and the monitor job is never given secrets beyond `GITHUB_TOKEN`.

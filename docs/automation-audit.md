# Automation audit: verified deal → branded graphic → Cloudinary → website → Instagram

Audit date: 2026-10-10 · Audited commit: `b733b23` (main)

## Current status (updated 2026-10-10 06:40 UTC, `main` at `ad56888`)
The findings below the line are the **original audit**, kept for history. This table shows where each stage stands now. Open PRs are green but not merged; nothing has been published, and spend is $0.

| Stage | Original state | Now | Where |
|---|---|---|---|
| CI health | 🔴 Hourly check failing (no Pillow) | 🟢 Fixed. Hourly checks pass on `main`; monitor incident #7 opened and **auto-closed** | #2 (merged), #3 (merged) |
| Validation | 🟠 Two divergent validators, store/category drift | 🟢 One rules file + validator. Archived (stale/expired) deals kept as **Past Deals**; strict validation for anything new or outgoing | #2, #9 (merged) |
| Monitoring | none | 🟢 Hourly monitor + status issue #8 + incident issues | #3 (merged) |
| Find deals | 🔴 No source | 🟠 **eBay** Browse adapter built, dormant until eBay approves Buy API access; **Impact** auth now works but 0 catalogs shared; `DEAL_FEED_URL` unset | #10 (open), Impact diagnostics PR (open) |
| Verify deals | 🟠 Manual only | 🟠 Still a human step by design: discovered deals land in the review queue (`needs_review`), and posting needs `publication_approved` | #10, #6 |
| Generate graphic | 🟠 Never triggered | 🟢 Deterministic branded cards (square for site, 4:5 for Instagram); live deals switched with owner approval | #5 (open) |
| Upload to Cloudinary | 🟡 Skipped generated cards; 3 uploaders | 🟢 One uploader; content-hash ids; merges mapping; hosts square + feed cards incl. Delsey | #4 (open) |
| Update website | 🟡 Category drift | 🟢 Categories aligned; Past Deals tab; discount badges round down | #2, #9 (merged) |
| Publish to Instagram | 🔴 Missing | 🟠 Buffer **dry run** with gates, dedupe and kill switch; no API client until the Buffer API/plan is confirmed | #6 (open) |
| End-to-end | none | 🟠 Orchestrated dry run in progress | upcoming PR |

**Remaining external blockers (owner, all free):**
1. eBay Buy API approval and keys.
2. Impact brand partnerships with catalogs.
3. Buffer API confirmation and the `BUFFER_API_KEY` secret.

---

*Original audit (2026-10-10, `b733b23`) follows.*

Target pipeline:

```
find verified deals → generate branded graphic → upload to Cloudinary → update website → publish to Instagram
```

Summary: the pieces exist as **disconnected, manually triggered fragments**. Several are switched off on purpose. Nothing publishes to Instagram. The table below rates each stage.

| Stage | State | Main blocker |
|---|---|---|
| Find deals | 🔴 Not working | `DEAL_FEED_URL` is unset or empty, `deals-source.json` is `[]`, and the Impact check is read-only. No retailer API supplies price and reference price. |
| Verify deals | 🟠 Manual only | Nothing ever sets `status: "verified"` / `checkout_verified: true`. `approve_deal.py` needs an `id` that no pending deal has. |
| Generate graphic | 🟠 Exists, never triggers | `generate_deal_images.py` needs `images/source/`, which doesn't exist, and skips any deal that already has `image`. AI generation is explicitly disabled. |
| Upload to Cloudinary | 🟡 Works for raw photos | `upload_cloudinary_images.py` **skips `images/generated/`**, so branded graphics are never uploaded. There are 3 divergent upload implementations. |
| Update website | 🟡 Works via commits | Category values don't match the site filters. The workflows that push to `main` don't chain. |
| Publish to Instagram | 🔴 Missing | No code, no workflow, no secrets, no caption/queue/post-log. |
| CI health | 🔴 Red | `Hourly deal checks` has failed every run: `ModuleNotFoundError: No module named 'PIL'`. |

---

## 1. GitHub Actions

| Workflow | Trigger | Notes |
|---|---|---|
| `hourly-deal-check.yml` | cron hourly | **Failing.** Runs `unittest discover` (which imports `test_deal_images.py` → Pillow) without installing Pillow. Fix: add `pip install 'Pillow>=10,<13'`. |
| `refresh-deals.yml` | cron 6h, push main | Merges `deals-source.json` (always `[]`), uploads raw photos, commits to main. |
| `generate-deal-images.yml` | push main (`deals.json`) | Never fires from bot commits (see 1a). It also does nothing because of the `images/source/` precondition. |
| `generate-demo-image.yml` | push/dispatch | Hard-coded hand-drawn Delsey illustration. This is the "unprofessional" graphic currently live on the site. Should be retired. |
| `host-deal-image.yml` | push (any branch), dispatch | Uploads one image and prints the URL, but **never persists it** (`contents: read`). The URL is lost after the run. |
| `test-cloudinary-upload.yml` | push main, dispatch | Third inline uploader. Signs `upload_preset=bestdealhunter`, which must exist as a signed preset or the upload fails. |
| `publication-gate.yml` | cron hourly | Writes `approved-deals.json` **to the runner only**. It isn't committed or saved as an artifact, so it's discarded. No consumer exists. |
| `collect-candidates.yml` | cron 6h | No-ops without `DEAL_FEED_URL`. |
| `validate-deals.yml` | push/PR on `deals.json` | Node validator only. It skips the Python tests and the staleness check. |
| `impact-catalog-check.yml` | dispatch | Read-only catalog count. It imports nothing. |

### 1a. Workflows can't chain
Commits pushed with the default `GITHUB_TOKEN` **do not trigger other workflows**. So `refresh-deals` → push `deals.json` will never start `generate-deal-images`, and nothing starts an uploader or publisher. Fix: put the whole pipeline in **one orchestrating workflow** with sequential jobs, or use `workflow_run` / `workflow_call`.

### 1b. Push races
Three workflows (`refresh-deals`, `generate-deal-images`, `collect-candidates`) commit to `main` with plain `git push` and no shared concurrency group or `pull --rebase`. Overlapping runs will fail with non-fast-forward errors.

### 1c. Maintenance
Node 20 deprecation warnings appear on `actions/checkout@v4` / `setup-python@v5`. Pillow is pinned differently (`==11.3.0` vs `>=10,<13`).

## 2. Graphic generation

* `scripts/generate_deal_images.py` is a decent **Pillow template**: a 1080×1080 card with header, photo, title, price, "Was", % badge and disclaimer. It's the right foundation, but:
  * It only reads `image_source` under `images/source/`. That directory doesn't exist and no deal has `image_source`.
  * It skips deals that already have `image`. All 5 live deals do, so it never runs.
  * It only runs over `deals.json` (already public). Pending deals can't get a graphic.
  * **Chicken-and-egg:** `prepare-publication.cjs` requires an existing image before a deal is eligible, but images are only generated after a deal is public.
* There's **no AI image integration** (no OpenAI/Gemini/Stability/Replicate/Cloudinary-AI calls, no API-key secret). Repo comments explicitly forbid it ("Do not generate cartoon-like graphics", "No AI product fabrication").
* Live images are raw phone screenshots (`images/IMG_*.jpeg`) or the hand-drawn Delsey placeholder. `images/generated/bose-…png` isn't referenced anywhere.
* Only one 1080×1080 format exists. Instagram feed also benefits from 1080×1350 (4:5), and Stories need 1080×1920.

**Recommendation:** keep the real product photo authentic and use AI only for the *design layer*: background/scene, layout polish, copy. Composite the authorized product image onto the AI or brand background with Pillow (or Cloudinary transformations), then render price, badge and disclosure deterministically from the validated data. Never let a model render prices, product appearance or logos. This keeps the existing "no fabricated product" policy and still looks professional.

## 3. Cloudinary

* Three separate uploaders with different behavior:
  * `scripts/upload_cloudinary_images.py`: filename-based `public_id`, `overwrite=false`, **skips `images/generated/`**.
  * `scripts/upload-deal-image.cjs`: content-hash `public_id`, verifies with a public HEAD request. This is the best one.
  * The inline Python in `test-cloudinary-upload.yml`: random id, upload preset.
* `upload_cloudinary_images.py` **rewrites `cloudinary-images.json` from successes only**. One transient failure deletes that deal's hosted URL from the website mapping.
* Filename-based `public_id` + `overwrite=false` means a regenerated graphic with the same name keeps serving the old asset.
* `index.html` hard-codes the cloud name `j9g6uest` in its URL allow-list. This is fine (cloud names are public) but needs updating if the account changes.
* Cloudinary already returns `https://res.cloudinary.com/...` JPEG URLs. That is exactly what the Instagram Graph API needs for `image_url`, so this stage is the strongest link.

**Recommendation:** use one uploader (the `.cjs` content-hash approach, or a Python port). Merge results into `cloudinary-images.json` keyed by deal ID instead of overwriting it, and upload `images/generated/` branded cards.

## 4. Deal validation

* There are **two validators with different rules**:
  * `validate_deals.py` requires ≥50% off and `verified_date` within 2 days. It doesn't know affiliate sections.
  * `validate-deals.cjs` allows <50% for the affiliate section, checks duplicates and image paths, and has **no staleness check**.
* `refresh_deals.py` uses its own store list **without Macy's**.
* **Staleness time-bomb:** every live deal has `verified_date: 2026-10-09`. On 2026-10-12 `validate_deals.py` will fail, with no automated re-verification to refresh them.
* There are two queues with different schemas: `pending_deals.json` (`status`, `publication_eligible`, `blockers`) and `deal-review-queue.json` (`listed_price`, `retailer_comparable_value`). `collect_candidates.py` writes yet another shape (`review_status`).
* No pending item has the `id` field that `approve_deal.py` requires.
* `prepare-publication.cjs` requires `verified_date === today` in UTC, so a deal verified in the US evening is already "stale" for that check.
* Category drift: deals use `Home & Kitchen`, `Clothing`, `Computers`, but the site filter offers `Home`, `Fashion`, `Other` etc. These deals are invisible when a category filter is selected.
* "Checkout verified" can't be honestly automated by scraping. Full automation needs **authorized price data**: Best Buy Products API, Amazon Creators/PA-API (requires qualifying sales), Impact/CJ/Rakuten catalogs (Macy's, etc.). Any one of them gives current price and list price with timestamps. Without that, a human verification step must remain. An "approve" step via a GitHub issue/label or workflow_dispatch input keeps it to one click.

## 5. Instagram publishing: not implemented

Missing:
* An **Instagram professional (Business/Creator) account** connected to a Facebook Page, a Meta app with `instagram_basic` + `instagram_content_publish` (+ `pages_read_engagement`), and a **long-lived access token** with refresh. Store these as secrets, e.g. `IG_USER_ID` and `IG_ACCESS_TOKEN`.
* Publisher code for the two-step Graph API flow: `POST /{ig-user-id}/media` with `image_url` (Cloudinary JPEG) + `caption` → poll container `status_code` → `POST /{ig-user-id}/media_publish`. Also check `content_publishing_limit` before posting.
* Caption builder with an FTC disclosure (`#ad` / "affiliate link") and "price may change" wording. Feed captions can't contain clickable links, so a link-in-bio page or deep link on the site is needed.
* Idempotency: a `posted.json` log (deal ID → IG media ID, timestamp) so cron re-runs never double-post. Also a kill switch (`INSTAGRAM_PUBLISH_ENABLED` repo variable) and a dry-run mode.
* Compliance: Amazon Associates restricts reuse of Amazon product images and pricing outside its API, including in social posts. The current Amazon screenshots should **not** be posted to Instagram. Use PA-API/Creators-API images or manufacturer-licensed images.
* Buffer is referenced in comments but isn't integrated. Pick one: Graph API directly (recommended, no extra cost) or Buffer.

## 6. Proposed architecture (for discussion)

One workflow, `pipeline.yml` (cron + dispatch, `concurrency: pipeline`), with sequential jobs:

1. **collect**: authorized APIs → `pending_deals.json` (single schema, stable `id`).
2. **verify**: re-price via API; set `verified_at` (timestamp); auto-reject <50% or stale items; optionally require human approval label.
3. **render**: generate 1080×1350 + 1080×1080 cards for *approved* deals (product photo + AI/brand background + deterministic text).
4. **host**: upload via a single uploader; merge URLs into `cloudinary-images.json` by deal `id`.
5. **site**: promote to `deals.json`; expire stale deals; one commit, `pull --rebase` + retry.
6. **instagram**: gated by `INSTAGRAM_PUBLISH_ENABLED` + `environment: instagram` (required reviewers optional); idempotent post log.

Suggested first PRs, small and safe:
1. Fix hourly check (install Pillow), unify validators, fix categories.
2. Single Cloudinary uploader that includes `images/generated/` and merges results.
3. Unified deal schema + `id` + render step for pending/approved deals.
4. Instagram publisher in **dry-run mode** (builds container payloads, never calls `media_publish`) behind the kill switch.
5. AI background generation (provider TBD) behind a feature flag.

## Secrets/config needed (names only, never values)

`CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET` (exist) · `IG_USER_ID`, `IG_ACCESS_TOKEN` (new) · AI image provider key (new, provider TBD) · retailer API credentials, e.g. `BESTBUY_API_KEY` and the existing `IMPACT_*` · repo variable `INSTAGRAM_PUBLISH_ENABLED=false`.

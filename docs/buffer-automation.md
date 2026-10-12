# Permanent Cloudinary → Buffer automation

Status (2026-10-12): **prepared, not live.** Instagram live publishing stays disabled. Nothing in this repository can publish until the owner turns on every gate below. This plan builds on the open PR stack. It does not replace it.

## What already exists (open PRs, unmerged)

| PR | Provides |
|---|---|
| Dramarolltime/BestDealHunter#4 | One Cloudinary uploader with a content-hash `public_id` that merges into `cloudinary-images.json` and verifies URLs |
| Dramarolltime/BestDealHunter#5 | Deterministic branded cards (4:5 feed + square) |
| Dramarolltime/BestDealHunter#6 | `publish_buffer.py`: dry run, `posts.json` reserve-before-send ledger (dedupe on URL + price), kill switch `INSTAGRAM_PUBLISH_ENABLED`, at most 1 post per run, `sent` only when Buffer says so |
| Dramarolltime/BestDealHunter#13, Dramarolltime/BestDealHunter#14 | Pipeline dry run and one-deal end-to-end proof |
| Dramarolltime/BestDealHunter#16 | Separate fail-closed `--draft-check`, required Instagram metadata, manual `buffer-publish.yml` in the `instagram` environment |

Merge order, if approved: #4 → #5 → #6 → #13 → #14 → #16. Each PR is based on the one before it. Dramarolltime/BestDealHunter#17 is a temporary one-off and is never merged.

## What this PR adds

1. **Error monitoring** (`scripts/maintenance/monitor.py`, hourly):
   - `buffer-ledger` opens an incident when a `posts.json` row is stuck in `reserved` or `error`, or when Buffer reports `error`. A stuck reservation can mean a post exists in Buffer that we did not record, so a person checks Buffer before clearing the row.
   - `buffer-test-drafts` warns while any test draft hasn't been confirmed deleted.
   - `no-active-deals` is now an incident, so the publisher never runs on an empty site without anyone noticing.
2. **Test-draft cleanup** (`scripts/buffer_ops.py`, `.github/workflows/buffer-draft-cleanup.yml`):
   - It only touches posts listed in `buffer-test-drafts.json`, on `BUFFER_CHANNEL_ID`.
   - It deletes a post only after Buffer confirms `status: draft`, the right channel and no `sentAt`.
   - It reads the `deletePost` input shape by introspection first, because public sources disagree on whether the field is `id` or `postId`. An unexpected shape stops the run before anything is deleted.
   - Deletion counts as done only when a re-read shows the post is gone.
   - It has no create, queue or share code paths, and a test enforces that.
3. **Pre-send image check**: `buffer_ops.verify_hosted_image(url)` requires an HTTP 200 `image/jpeg` from `res.cloudinary.com` right before sending. `publish()` in #16 should call it per post and skip (not fail the run) on a miss. This is a two-line follow-up on that PR.

## Safe publishing controls (all must hold before anything goes live)

| Control | Where | Default |
|---|---|---|
| Kill switch `INSTAGRAM_PUBLISH_ENABLED == "true"` | repo variable | **off** |
| Manual trigger only (`workflow_dispatch`), `instagram` environment with required reviewer | `buffer-publish.yml` (#16) | required |
| `publication_approved: true` per deal, in a reviewed PR | `deals.json` | none approved |
| Strict `validate()` today: ≥50% off, evidence, verified ≤2 days, not archived | `validate_deals.py` | enforced |
| Not Amazon (no approved data access) | `publish_buffer.py` | enforced |
| 4:5 card hosted on Cloudinary and reachable now | `cloudinary-images.json` + `verify_hosted_image` | enforced |
| Dedupe: one ledger row per URL + price, reserved **before** the API call | `posts.json` | enforced |
| ≤1 post per run, single `buffer-publisher` concurrency group | workflow | enforced |
| `sent` recorded only from Buffer's own status | `refresh_statuses()` | enforced |
| Stuck/error rows open an incident | maintenance monitor | this PR |

## Owner steps (none are taken by Claude)
1. Delete test draft `6acadf46ed92ed8fa5e85a01` in Buffer. Either use the Buffer UI, or add the label `buffer-draft-cleanup-approved` to this PR to run the cleanup workflow. To check without deleting first, use the label `buffer-draft-verify`.
2. Record the result: set the row in `buffer-test-drafts.json` to `"status": "deleted"`. The workflow artifact contains the updated file. Then close #17 without merging.
3. Review and merge the stack in order, if approved.
4. Going live later is a separate decision: approve one deal, set the kill switch for one supervised run, then turn it off again.

Costs: $0. Buffer is on its current plan with no upgrade, and Cloudinary is on the free tier. No paid APIs are used.

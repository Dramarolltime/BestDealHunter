# Instagram publishing via Buffer

Status: the pipeline is **ready to post up to Buffer**. A real Buffer client exists, but live posting is locked behind four switches, and none of them is set. Nothing has been published.

## One deal end-to-end (`scripts/e2e_one_deal.py`)
Runs one deal through every step and proves each one. It never publishes, and it never writes `posts.json`:

1. **Validate:** strict validation for today (≥50% off, evidence, fresh, allowed store).
2. **Card:** re-renders the square and 4:5 cards and byte-compares them with the committed files.
3. **Cloudinary:** both cards appear in `cloudinary-images.json` with matching sha256. With `--verify-urls` (CI), each URL is fetched and must return HTTP 200 `image/jpeg`.
4. **Website:** reads `index.html`'s own rules (stores, freshness, Cloudinary prefix) and confirms the deal shows under Explore Deals with its Cloudinary card.
5. **Buffer dry run:** plans the exact post (4:5 Cloudinary JPEG + caption with `#ad` and disclosures). It then runs the real `publish()` against an **in-memory fake Buffer** and a **copy** of the ledger to prove a second attempt is blocked.

Run it with `python scripts/e2e_one_deal.py --deal <card id>`. It also runs in the *Pipeline dry run* workflow.

## Duplicate protection
- `posts.json` is a ledger keyed by deal URL + price.
- A row is written **before** Buffer is called. Buffer's `createPost` has no idempotency key, so a run that dies mid-call still leaves a `reserved`/`error` row. That row blocks any retry until a person checks Buffer and clears it.
- Every existing row blocks its key, both at planning time and again inside `publish()`, so even a stale plan can't send twice.
- At most 1 post per run, and the workflow concurrency group prevents parallel runs.
- A post is marked `sent` only when Buffer's own status says so.

## Gates for anything live
| Gate | Where |
|---|---|
| `INSTAGRAM_PUBLISH_ENABLED` = `true` | repo variable (kill switch; off by default) |
| `BUFFER_API_KEY` | repo secret (owner creates it in Buffer; never in code or chat) |
| `BUFFER_CHANNEL_ID` | repo variable (id of the `best_dealhunter` Instagram channel) |
| `publication_approved: true` | per deal, set in a reviewed PR |
| strict validation + not Amazon + hosted 4:5 card + not already in the ledger | automatic |

`--draft-check` creates a Buffer **draft**, which is never published. It verifies the key, the channel, the image URL and the mutation schema before the first real post. It needs the key and channel, but not the kill switch.

## Is Buffer the blocker? Exact technical status
**No code blocker remains on our side.** What's missing is account access, plus one confirmation that I couldn't do from the build sandbox:

1. **No `BUFFER_API_KEY`** exists in the repo, so nothing can authenticate.
2. **No channel id** for `best_dealhunter` has been recorded.
3. **Schema confirmation:** `developers.buffer.com` is unreachable from my sandbox. The client follows Buffer's published examples, seen via search:
   - GraphQL at `https://api.buffer.com` with a Bearer personal API key;
   - `createPost(input: {text, channelId, schedulingType, mode: addToQueue, assets: [{image: {url}}]})`;
   - a `PostActionSuccess` / `MutationError` union, with errors returned as HTTP 200.
   
   The exact enum values (e.g. `schedulingType`), the post-status query and any required Instagram `metadata` still need to be confirmed. The `--draft-check` run does that safely.

Buffer's help pages say the GraphQL API is available on **all plans including Free**. One 2026 third-party guide calls it a public beta with personal keys only, which is fine here because we post to our own account.

### Fix Buffer, or switch to direct Instagram?
| | Buffer (recommended) | Direct Instagram Graph API |
|---|---|---|
| Account setup | Already connected to `best_dealhunter` | Needs a Meta developer app, an Instagram professional account linked to the app, and permissions `instagram_business_basic` + `instagram_business_content_publish` |
| Credentials | 1 personal API key, created in Buffer settings | Long-lived user token (expires in **60 days**; needs a refresh job, or the owner must re-authenticate) |
| Review | None for our own account | Sources disagree on whether App Review is needed for your own account; Advanced Access needs Business Verification |
| Our remaining work | Paste key + channel id, run `--draft-check`, adjust field names if needed | New client (create container → poll → publish), token refresh, new tests |
| Cost | $0 (Free plan; queue limits apply) | $0 |

**Buffer is faster:** about 10 minutes of owner setup plus one draft check. Direct Instagram is a fallback only if Buffer's API turns out to be unavailable on the current plan.

## Owner steps to go live later (none needed for the dry run)
1. In Buffer, create a personal API key and save it as the GitHub secret `BUFFER_API_KEY`.
2. Find the `best_dealhunter` channel id and save it as the variable `BUFFER_CHANNEL_ID`.
3. Run the publisher with `--draft-check`. A draft appears in Buffer and nothing is posted. Delete the draft afterwards.
4. Approve one deal (`publication_approved: true`) in a PR, then set `INSTAGRAM_PUBLISH_ENABLED=true` for a single supervised run.

Sources: [Buffer: Create Image Post](https://developers.buffer.com/examples/create-image-post.html), [Buffer: Posts & Scheduling](https://developers.buffer.com/guides/posts-and-scheduling.html), [Buffer: Create Draft Post](https://developers.buffer.com/examples/create-draft-post.html), [Buffer Help: API](https://support.buffer.com/article/859-does-buffer-have-an-api), [Zernio: Buffer API 2026](https://zernio.com/blog/buffer-api), [Meta: Instagram Platform](https://developers.facebook.com/documentation/instagram-platform/overview), [bundle.social: Instagram Graph API](https://bundle.social/blog/instagram-graph-api)

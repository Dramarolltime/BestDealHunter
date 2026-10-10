# Instagram publishing via Buffer

Status: **dry run only.** `scripts/publish_buffer.py` plans posts for the existing Buffer-connected Instagram channel `best_dealhunter`. It has no code that contacts Buffer: `BufferClient` raises `NotImplementedError` until the steps below are approved. Automatic publishing stays **off**.

## What a run does
1. Loads `deals.json`, `cloudinary-images.json` and the post log `posts.json`.
2. Checks every deal against **all** of these gates and reports the first one that fails:
   - passes `validate_deals.py` today (fresh, ≥50% off, evidence, allowed store);
   - `publication_approved: true` on the deal, set by Alpha or the owner in a reviewed PR;
   - **not Amazon**, until approved Amazon data access exists (#1);
   - its `image` is a generated branded card from `deal_graphics.py` (the site shows `deal-<id>-square.jpg`), so no baked-price composites or illustrations;
   - the matching 4:5 `deal-<id>-feed.jpg` is hosted on Cloudinary as a JPEG (that's the one posted);
   - it's **active**: archived/expired deals (Past Deals) are never posted, because strict validation rejects them;
   - it isn't already in `posts.json` at the same URL and price (**duplicate prevention**).
3. Builds the caption from the same numbers as the card: % off, price, reference label and value, savings, verification date, "prices may change", `#ad`, the commission disclosure and "Link in bio".
4. Plans **at most 1 post per run** and writes the plan to the job summary.

The `Buffer publisher dry run` workflow runs it manually or on PRs that change it. It has read-only permissions and **no Buffer secret**.

## Kill switch and live gates (for later)
A live run would need **all** of the following:
- repository variable `INSTAGRAM_PUBLISH_ENABLED` = `true` (missing or anything else means off);
- the explicit `--live` flag;
- secret `BUFFER_API_KEY`;
- an implemented `BufferClient`;
- per-deal `publication_approved: true`.

A post is recorded as published **only when Buffer reports it `sent`**. Creating it in the queue isn't enough.

## Needed from Alpha/owner before implementing the client
1. **Confirm Buffer's current API** from Buffer's docs in the account. Public sources say it's a GraphQL API at `https://api.buffer.com` that authenticates with a personal API key (Bearer) and creates posts with a `createPost` mutation (`channelId`, `mode: addToQueue`). I couldn't reach Buffer's developer site from the build environment, so the exact schema, image-attachment field and status values must be checked first.
2. **Confirm the API key is available on the current plan**, with no upgrade. If it needs a paid plan, we stop here, per the no-new-charges rule.
3. The owner creates the key in Buffer and stores it **only** as the GitHub secret `BUFFER_API_KEY`. It never goes in code, issues or chat.
4. Look up the channel id for `best_dealhunter`.

After that, the client is a small change: create a queued post, record `queued` in `posts.json`, and in a later run read the status and record `sent` or `error`. It goes behind a protected GitHub environment `instagram` with required reviewers, until hands-off posting is approved.

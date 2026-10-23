# BestDealHunter: guide map to 24/7 autonomous publishing

**Goal:** the website and Instagram publish verified ≥ 50%-off deals on their own, around the clock, with no person in the loop. Every price comes from a source we're allowed to use, nothing is posted twice, no stale price is advertised, and no deal history is lost.

**Lead:** Claude. **Owner:** approves spending, merges, and anything only a person can do (account sign-ups, card payments).

## Where we are (2026-10-14)
| Piece | Status |
|---|---|
| Website (GitHub Pages) | Live; 5 deals listed, all labelled "Price not re-verified" |
| Branded cards (our own design, no retailer images) | Built and tested (PR #27) |
| Cloudinary hosting | Working (free tier) |
| Instagram via Buffer | Proven (the logo post); live posting built with a durable duplicate lock, `shareNow`, and a 3-hour freshness limit (PR #27) |
| Scheduler | `auto-publish.yml`, every 6 hours on GitHub Actions (free) |
| **Deal data** | **The blocker.** Retailers block or forbid page reading. Impact and Rakuten declined us. Amazon's terms forbid our method. Best Buy and eBay want a business email. |

The pipeline is finished; only the data supply is missing. **We are not starting over.**

## How it works once running
```
every 6 h (GitHub Actions, free)
  ├─ eBay Browse API ──────┐  official, free: "was" price + % off, new, trusted sellers
  ├─ Best Buy Products API ┤  official, free: regular vs sale price, online stock
  └─ (later) more official sources
          ↓ verified rows (≥ 50% off, credible reference, in stock, < 80% off)
  auto_publish.py → deals.json (append-only) → branded cards → Cloudinary
          ↓ git push → website updates (GitHub Pages)
  publish_buffer.py → Instagram (shareNow, lock tag per product+price, price check < 3 h)
          ↓
  maintenance monitor (hourly) → issue #1 alert if anything breaks

any time (a person saw a 50%+ deal on Target, Walmart, Home Depot, Dick's, Macy's, ...)
  "Submit a deal" issue form (owner/collaborators only, screenshot required)
          ↓ submit_deal.py: store's own link, ≥ 50% (< 80%), in stock, sold by the store, not listed yet
  same chain: deals.json → card → Cloudinary → website → Instagram; reply on the issue and close it
```

Cards and captions follow Alpha's Creative & Growth Playbook v1.0 (issue #1): Inter fonts, navy/lime/cyan
palette, "Price as of <time> PT", category hashtag sets. Amazon stays off (Associates price rules).

## Phases

### Phase 1: unlock official data (owner, about 45 min, about $10–12 a year)
1. **Buy a domain**, e.g. `bestdealhunter.com` or a variant, at a registrar that sells at cost (Cloudflare Registrar, Porkbun): about $10–12 a year. *Needs your approval to spend.*
2. **Business email on that domain, free:**
   - **Option 1, Cloudflare Email Routing:** `deals@yourdomain` forwards to your Gmail.
   - **Option 2, Zoho Mail's free plan:** a real mailbox.
3. **eBay Developers Program**, free, about 1 business day to approve. Then create a **Production keyset** and add two repository secrets: `EBAY_CLIENT_ID` and `EBAY_CLIENT_SECRET`.
4. **Best Buy developer key** (developer.bestbuy.com), using the business email. Add it as the secret `BEST_BUY_API_KEY`.
5. *(Optional, free)* point the website at the new domain (GitHub Pages custom domain). A real domain looks credible to visitors and to affiliate networks if you re-apply later.

### Phase 2: activate (Claude prepares, owner merges)
1. Merge **PR #28**: the Amazon disclosure line, and the fix for a test that is failing on `main`.
2. Re-run PR #27's **Auto-publish** check. It lists the real eBay / Best Buy deals it *would* publish (test mode). Review 5 of them.
3. Merge **PR #27**, then set the repository variable **`INSTAGRAM_PUBLISH_ENABLED` = `true`**.
4. Close the PRs #27 replaces: #4, #5, #6, #13, #14, #16, #17, #22, #24, #25. Close #26 (Impact, declined) or leave it inactive.

### Phase 3: run 24/7 (automatic)
- **Every 6 hours:** up to 5 new deals on the site, and 1 Instagram post per run (about 4 a day, raised once quality is confirmed).
- **Hourly:** the monitor checks the site, the hosting, the feed, and the last runs. Problems go to issue #1, with a daily digest.
- *(Optional)* a daily **Claude routine** (scheduled Claude session): reads the run logs, fixes a broken source or test, and reports. Needs your OK; it uses your Claude plan, not cash.

### Phase 4: grow (after 2–4 weeks of steady posting)
- Re-apply to affiliate networks (Awin, CJ, Impact) with the real domain and traffic numbers. This is optional, because links stay direct unless an affiliate link is approved.
- Add sources as they become available: Woot API (key by request), Walmart and Target only through approved feeds.
- Tune the posting rate and categories using Instagram insights.

## Guard rails (never relaxed)
- Only official APIs, or retailers explicitly cleared for page reading (`deal_rules.json` → `page_check_allowed_hosts`, currently empty). Never bypass blocks; never open Amazon pages.
- ≥ 50% off computed by us in cents; 80%+ held for a person; the reference price must come from the retailer's own data.
- Direct links; "#ad" only on real affiliate links.
- Append-only history; old prices on images are blurred and labelled.
- No spending, merging, or new accounts without the owner's OK.

## Open decisions for the owner
| Decision | Recommendation |
|---|---|
| Domain + email (~$10–12/yr) | Yes: it unlocks eBay and Best Buy, and helps future affiliate approvals |
| Merge #28 now | Yes: `main`'s scheduled test is failing without it |
| Daily Claude maintenance routine | Yes, after activation |
| Existing Amazon deals on the site (hand-captured prices) | Remove the prices or archive them; Amazon's terms don't allow showing them |

# Retailer verification: from candidates to verified deals

## Why the site has zero active deals (2026-10-12)
Discovery and research fill `pending_deals.json` and `deal-review-queue.json`, but **no part of the pipeline can read a retailer's current price**:
- The Claude Code cloud sandbox's network policy denies `bestbuy.com`, `macys.com`, `api.bestbuy.com`, eBay, Impact and Buffer. Web search snapshots are days or months old and are not verification.
- GitHub Actions runners *can* reach retailer APIs. But until now no workflow called one, and no retailer API key existed.
- The repository forbids scraping retailer pages (README), and Best Buy's and Macy's terms restrict it too. So the fix is official data, not page scraping.

## Legitimate sources

| Retailer | Official source | Gives us | Access | Status |
|---|---|---|---|---|
| **Best Buy** | [Best Buy Products API](https://developer.bestbuy.com/documentation/products-api) | `salePrice`, `regularPrice`, `onlineAvailability`, `orderable`, `priceUpdateDate` | Free key at developer.bestbuy.com. The [terms](https://developer.bestbuy.com/legal) forbid sharing the key and limit caching, and some summaries say commercial use needs Best Buy's approval, so confirm when signing up | **Adapter built** (`scripts/verify_deals.py`). Needs secret `BEST_BUY_API_KEY` |
| Best Buy (alt) | Impact catalog (Best Buy's affiliate program runs on Impact) | Catalog price, original price, stock | Apply to the Best Buy campaign in Impact. Our Impact auth already works, but 0 catalogs are shared (PR #11) | Apply |
| **Macy's** | Rakuten Advertising (Macy's affiliate program) product feed | Price and sale price feed | Apply to Macy's on Rakuten. No public Macy's product API exists | Not started. **Manual verification only** until then |
| Walmart, Target, Home Depot, Lowe's | Impact brand catalogs (confirm each program in the Impact marketplace) | Catalog items | Apply per brand | Not started |
| eBay | Browse API (PR #10) | Price plus a **seller-supplied** list price | Production approval | Dormant; never counts as 50%-off proof |
| Amazon | Associates product API | Price/offers | Requires API eligibility through Associates | Not eligible yet; no Amazon screenshots |

Best Buy's "comparable value" is **not** treated as a reference price. The verifier uses Best Buy's own `regularPrice`, so some Techtober "50% vs comparable value" listings will correctly fail.

## What `verify_deals.py` does (runs every 3 hours in Actions)
1. **Collects** every published deal (for re-verification) and every queue candidate that hasn't been rejected or published.
2. **Looks up** each one with its retailer's adapter, batched up to 100 per call. Best Buy items need a SKU (taken from the URL, or `sku`) or a `model_number`.
3. **Decides** for each item:
   - A candidate that is available online, ≥50% below the retailer's regular price and passes strict `validate()` is **published** (at most 10 new per run).
   - A published deal that still qualifies is **re-verified**: the price is updated and `verified_date` = today, because the retailer just confirmed it.
   - A published deal that lost its discount or stock is **archived** with the reason. Its posted price and date are kept, and it is never deleted.
   - A deal a person archived by hand is never reactivated.
   - A source outage changes nothing.
   - A store with no adapter (Macy's today) is reported as **manual**.
4. **Writes** `deals.json` and the queues only when the repo variable `AUTO_PUBLISH_VERIFIED_DEALS` is `true`. Otherwise it's a dry run with a report in the job summary.

The API key never appears in logs or reports (tested). Verification dates move **only** from a retailer response fetched in that run.

## Target workflow (fully automated, with approval points)

| Step | How | Where | Status |
|---|---|---|---|
| Discover | Retailer APIs and affiliate catalogs list on-sale items. Research sessions add candidates | `collect_candidates.py`, PR #10, a later Best Buy discovery query | Partly built |
| Verify price + stock + ≥50% | `verify_deals.py` against the official source | `verify-retailers.yml`, every 3h | **This PR** |
| Publish to site | Same run, gated by `AUTO_PUBLISH_VERIFIED_DEALS` | `deals.json` → GitHub Pages | **This PR** |
| Branded image | Deterministic card from deal data | PR #5 (`generate_deal_images.py`) | Open PR |
| Host image | Content-hash Cloudinary upload, verified URL | PR #4 | Open PR |
| Archive expired | Retailer re-check archives, and the 2-day freshness rule archives anything not re-checked | `verify_deals.py` + `validate_feed` | **This PR** + merged |
| Instagram draft for approval | `--draft-check` / gated publisher, dedupe ledger, kill switch off | PRs #6 → #16 | Open PRs |
| Monitor | Incidents for no active deals, stuck Buffer rows, failing workflows | `monitor.py` | This PR |

## Restoring deals now: what each path needs
1. **Fastest (minutes, free): Best Buy API key.**
   1. The owner creates a key at developer.bestbuy.com and saves it as the secret `BEST_BUY_API_KEY`.
   2. Merge this PR, then run *Verify deals with retailer data* manually. It's a dry run, so check the report.
   3. If the report looks right, set the variable `AUTO_PUBLISH_VERIFIED_DEALS=true`.

   This covers the 5 Best Buy candidates. 3 can be looked up today: It Takes Two (SKU 6444455), Chromebook (SKU 6673811) and Hisense (model 65S7SG). The Insignia TV and Energizer charger need their SKU added.
2. **Macy's (8 candidates):** apply to Macy's on Rakuten for a feed. Until then, a person checks the page and records the evidence in a reviewed PR. A Claude Code session on the owner's own computer with Claude in Chrome can read the page with the owner present.
3. **More retailers:** apply to brand campaigns in Impact. Catalogs then appear for the existing credentials, and an Impact adapter plugs into `verify_deals.py` the same way.

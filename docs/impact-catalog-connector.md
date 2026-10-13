# Impact catalog connector (Target, Walmart): inactive

Target's product pages don't expose price data to automated checks (probe run 38381517779:
4 product pages, 0 with price data). So retailer data has to come from an authorized feed.
Target (Target Partners) and Walmart both run their affiliate programs on Impact.

## Status
- `scripts/impact_catalog_candidates.py` and `.github/workflows/impact-catalog-candidates.yml` are built and tested with a fake Impact API. They have never been run against Impact.
- The Impact account currently has **0 shared catalogs**. The connector stays off until the repository variable `IMPACT_CATALOGS_ENABLED` is `true`. While it's off, the workflow job is skipped and the script reads nothing.
- It costs $0. It uses the existing `IMPACT_ACCOUNT_SID` / `IMPACT_AUTH_TOKEN` secrets and the free Impact API.

## Owner steps (needs your approval; no cost)
1. In the Impact dashboard, under **Brands → Find brands**, apply to **Target Partners** and **Walmart**.
2. Wait for approval, then check that a product catalog is shared with the account. Run the existing **Impact catalog connection check**: "Catalogs" should be 1 or more.
3. Set the repository variable `IMPACT_CATALOGS_ENABLED` = `true`, then run **Impact catalog candidates (Target, Walmart)** by hand.

## What it keeps
| Rule | Detail |
|---|---|
| Advertiser | The catalog's advertiser is Target or Walmart; every other catalog is ignored. |
| Price | Numeric `CurrentPrice` < `OriginalPrice`, **≥ 50% off**, rounded down. |
| Stock | `StockAvailability` is InStock or LimitedAvailability. |
| Link | https, on target.com / walmart.com (Impact tracking hosts such as goto.target.com qualify). |
| Seller | A row that names a marketplace seller other than the retailer is skipped. |

The output is a list of records in `scripts/add_verified_deals.py` format (`evidence.method: "affiliate_catalog"`, `evidence.source` = the run URL). It goes through the same gates as every other deal:

```
python scripts/add_verified_deals.py impact-candidates.json          # dry run
python scripts/add_verified_deals.py impact-candidates.json --apply  # stage in pending_deals.json
python scripts/approve_deal.py <id>                                  # a person lists it on the site
```

Instagram also needs `publication_approved: true` and `INSTAGRAM_PUBLISH_ENABLED=true`.

**Recommendation:** catalogs can lag the live site, so open each deal in a browser once before approving it for Instagram.

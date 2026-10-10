# BestDealHunter

A public GitHub Pages website for displaying verified discounts from authorized US retailer feeds.

## Current status
- Website frontend repaired and prepared to read `deals.json`.
- The feed currently contains `[]`: no verified live offers.
- Amazon tracking ID: `bestdealhun0b-20`.
- eBay campaign ID: `5339220016`.
- Tracking IDs do not grant product API access.
- API credentials must be stored as GitHub Actions secrets, never in public source code.

## Feed format
A JSON array of objects with `store`, `title`, `category`, `url`, `price`, `original_price`, `verified_date`, `source`, and `price_evidence`. A deal must be at least 50% off (affiliate-section deals: at least 20% off plus an `affiliate_disclosure`) and have a price comparison verified within the last 2 days to be *active*. Older or expired deals are kept and shown under **Past Deals** as "Expired · price not verified" (never deleted); `validate_feed` checks the published feed and `validate` checks anything new or outgoing.

Stores, categories, retailer domains and thresholds live in `scripts/deal_rules.json`. `scripts/validate_deals.py` is the single validator used by CI, `refresh_deals.py` and `approve_deal.py`; a test keeps the site's store and category filters in `index.html` in sync with it. Run `python -m unittest discover -s scripts -p 'test_*.py'` (requires Pillow).

## Next steps
Confirm retailer-approved product data access, add a server-side collection job, validate discounts and freshness, and publish only eligible affiliate links. Respect each retailer's product data and attribution policies. Do not fabricate discounts or scrape restricted sources.

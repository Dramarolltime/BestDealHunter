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
A JSON array of objects with `store`, `title`, `category`, `url`, `price`, `original_price`, `verified_date`, `source`, and `price_evidence`. A deal must be at least 50% off and have a recent verified price comparison.

## Next steps
Confirm retailer-approved product data access, add a server-side collection job, validate discounts and freshness, and publish only eligible affiliate links. Respect each retailer's product data and attribution policies. Do not fabricate discounts or scrape restricted sources.

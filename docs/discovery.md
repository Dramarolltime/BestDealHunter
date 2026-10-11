# Deal discovery

Discovery only fills the **review queue** (`pending_deals.json`). Nothing found here is published or posted until a person approves it and it passes strict validation (#1).

## eBay Browse API (first source; design + tests, dormant)
`scripts/ebay_browse.py` (adapter) and `scripts/collect_ebay.py` (collector) run as a step of the 6-hourly *Collect retailer deal candidates* workflow. Without credentials the step prints "not configured" and does nothing.

### What we verified about eBay (Alpha's ask in #1)
- **Approval:** eBay's Buy APIs, which include Browse, are "intended for certain approved eBay partners". Production access is granted after an application and an *Application Growth Check*. The Deal API is a Limited Release. The sandbox is open to any developer account. Sources: [eBay buy requirements](https://ebayerpro.codebase.ebay.com/api-docs/buy/buy-requirements.html), [eBay: get started on a buying application](https://developer.ebay.com/develop/get-started/get-started-on-a-buying-application).
- **Reference price semantics:** with Strike Through Pricing (`priceTreatment` `LIST_PRICE`), the crossed-out list price is defined as "the price the seller recently listed the item for sale or sold the item for" ([PriceTreatmentEnum](https://developers.ebay.com/api-docs/buy/browse/types/gct:PriceTreatmentEnum)). It's **seller-supplied**. For the related Trading API field, eBay states it "does not maintain or validate" it ([DiscountPriceInfo](https://developer.ebay.com/devzone/xml/docs/reference/ebay/types/DiscountPriceInfoType.html)).
- **Affiliate tracking:** applications that earn affiliate revenue must implement affiliate tracking. We send the EPN campaign in `X-EBAY-C-ENDUSERCTX` and use the returned affiliate URL.
- I couldn't open developer.ebay.com from the build sandbox, so the exact default call limits and caching terms still need checking in the eBay developer account.

### How the adapter treats eBay prices
- It requests only **new-condition, fixed-price, USD** listings.
- It **computes the discount itself** from `price` and `marketingPrice.originalPrice`, rounding down. It rejects the item if eBay's `discountPercentage` disagrees, or if there's no reference price. It never invents one.
- An item is a candidate only at **≥50% off**. Even then it gets `review_status: "needs_review"`, `publication_eligible: false`, and `reference_price_label: "Seller's list price"` (cards print that label), with evidence noting the reference isn't independently verified.
- Items already queued or published are skipped. At most 25 new candidates are added per run.
- `EBAY_ENV=sandbox` is **always a dry run**, because sandbox listings are test data.

### Owner setup (free; no purchases)
1. Using the eBay developer account tied to EPN, apply for production Buy API (Browse) access.
2. Create the production keyset, and set the marketplace account-deletion notification (subscribe or opt out), which eBay requires before production keys work.
3. Store `EBAY_CLIENT_ID` and `EBAY_CLIENT_SECRET` as GitHub **secrets**. Set **variables** `EBAY_CAMPAIGN_ID` (EPN campaign) and optionally `EBAY_ENV=sandbox` for a first dry test.
4. Search terms and categories are in `discovery/ebay_queries.json`.

## Other sources
- **Impact:** `IMPACT_ACCOUNT_SID` secret is empty, so the catalog check can't run. The owner adds it from the Impact dashboard.
- **`DEAL_FEED_URL`:** unset; `collect_candidates.py` imports nothing.
- **Best Buy Products API:** possible next adapter, once its key and terms are confirmed.

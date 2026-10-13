# Adding verified deals

A deal goes on BestDealHunter only after its price was checked against the retailer's live data
within the last 2 days. Old deals are never deleted (see `validate_deals.py`).

## 1. Verify, using one of these methods
| `evidence.method` | What counts | `evidence.source` |
|---|---|---|
| `retailer_page_check` | `scripts/verify_listings.py` verdict **`verified`** (retailer's own offer data: price to the cent, in stock). `blocked`, `unconfirmed`, `mismatch` never count. | the GitHub Actions run URL |
| `retailer_api` | An official retailer API response, e.g. Best Buy Products API `salePrice`/`regularPrice`/availability | the API request URL (no key) |
| `affiliate_catalog` | An approved affiliate product catalog (e.g. Impact) row with price and original price | the catalog/item URL |
| `human_browser_check` | A person opened the product page in a normal browser | the screenshot under `evidence/` |

**Human check:**
1. Open the exact product page.
2. Select the exact variant (size or colour).
3. Record:
   - the product name and variant;
   - the retailer, and the seller (who it is "sold and shipped by");
   - the current price;
   - the reference price **and its label** as the retailer prints it ("Reg.", "Comp. value", "List");
   - availability: in stock, with shipping or pickup;
   - the URL;
   - any end date shown;
   - the date and time.
4. Save a full-page screenshot to `evidence/`.

Don't use deal blogs or search snippets as evidence. Use them only to find candidates.

## 2. Record it
Add one object per deal to a JSON file (example in `scripts/add_verified_deals.py`), then:

```
python scripts/add_verified_deals.py verified-deals.json          # dry run: shows OK / REJECTED
python scripts/add_verified_deals.py verified-deals.json --apply  # append to deals.json
```

The script computes the discount itself, rounded down, and needs **≥ 50%**. It rejects:
- missing evidence, or evidence older than 2 days;
- a seller that isn't named;
- an item that can't be bought right now;
- a link that isn't on the store's own site;
- a duplicate.

One bad record means nothing is written. New deals start with `publication_approved: false`.

## 3. Images, hosting, Instagram drafts (open PR stack)
1. **Branded cards:** run `generate_deal_images.py` (#5) to render the square card for the site and the 4:5 card for Instagram from the deal data.
2. **Cloudinary:** run `cloudinary_upload.py` (#4) to upload both cards, check the public URLs return HTTP 200, and record them in `cloudinary-images.json`.
3. **Buffer:** run `publish_buffer.py` (#6/#14/#16) to plan the caption with disclosures. A draft-check creates a non-published Buffer draft, protected by a one-time lock.
4. **Approval and live posting:** a person sets `publication_approved: true` in a reviewed PR. Live posting also needs `INSTAGRAM_PUBLISH_ENABLED=true`. Nothing is posted automatically.

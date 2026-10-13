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

## 2. Stage it for approval
Add one object per deal to a JSON file (example in `scripts/add_verified_deals.py`; `image_source` may name
an authorized product photo committed under `images/source/`), then:

```
python scripts/add_verified_deals.py verified-deals.json          # dry run: shows OK / REJECTED
python scripts/add_verified_deals.py verified-deals.json --apply  # stage in pending_deals.json
```

The script computes the discount itself, rounded down, and needs **≥ 50%**. It rejects:
- missing evidence, or evidence older than 2 days;
- a seller that isn't named;
- an item that can't be bought right now;
- a link that isn't on the store's own site;
- a duplicate of a listed or already-staged deal.

One bad record means nothing is written. `--apply` only appends to `pending_deals.json` (with an `id`,
`review_status: "verified_awaiting_approval"`, `publication_approved: false`); the website does not change.

## 3. Approval gates (each one is a person)
| Gate | Command / setting | Re-checks |
|---|---|---|
| **Website listing** | `python scripts/approve_deal.py <id>`, committed in a reviewed PR | Refuses rows without verification evidence and any price check older than 2 days (strict `validate`). Append-only. |
| **Instagram post** | set `publication_approved: true` on the deal in a reviewed PR | Buffer publisher (#6/#14/#16) also requires a fresh price check, a branded card hosted on Cloudinary, no earlier post at this price, and not Amazon. |
| **Scheduled refresh** | `refresh-deals.yml` (every 6 h) merges `deals-source.json` | Applies only entries that carry `evidence_method` and pass strict `validate`; `deals-source.json` changes only through a reviewed commit. |
| **Live posting switch** | repository variable `INSTAGRAM_PUBLISH_ENABLED=true` | Without it the publisher only does a dry run. |

## 4. Images, hosting, Instagram drafts
| Step | On `main` today | With the open PR stack |
|---|---|---|
| Card | `generate-deal-images.yml` (on push to `main`) renders a 1080×1080 card for deals with an authorized `image_source` | `generate_deal_images.py` / `deal_graphics.py` (#5) render the square website card and the 4:5 Instagram card for every deal (typographic when there's no photo); every number comes from the deal record, and the label comes from `reference_price_label` |
| Cloudinary | `host-deal-image.yml` uploads one image you name and checks the public URL | `cloudinary_upload.py` (#4) uploads every card, checks each URL returns an image, and merges it into `cloudinary-images.json`, which the website reads |
| Buffer | — | `publish_buffer.py` (#6/#14/#16) builds the caption with `#ad` and disclosure; dry run by default; a draft-check makes one unpublished Buffer draft behind a one-time lock |

Nothing in this flow posts automatically.

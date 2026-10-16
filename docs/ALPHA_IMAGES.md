# Alpha's images → Cloudinary → Google Sheet (issue #59)

Alpha (ChatGPT) makes the deal image; Claude publishes. Nobody moves files by hand.

```
Alpha / owner                GitHub "Alpha images" (every 15 min)          Google Sheet (Apps Script, every 10 min)
─────────────                ────────────────────────────────────          ────────────────────────────────────────
saves image in Drive    ──►  downloads that Drive file, checks it,   ──►  copies the URL into the row's
folder, pastes its link      uploads to Cloudinary (repo secrets),        "AI Image Cloudinary URL" cell
in "AI Image Drive URL"      verifies the URL, records it in              (only if empty; nothing else changes)
                             alpha-images.json
                                                     │
                                                     ▼
                             hourly auto-publish: the deal (with all its price checks) uses that Cloudinary image
```

## Why this path

* ChatGPT cannot send image bytes to GitHub or Cloudinary. It can save files to Google Drive (through the
  owner's Drive connection), so Drive is the drop-off point. The owner can also drop an image into the folder
  from the Drive app on a phone.
* **No new credentials anywhere.** Cloudinary uploads use the three `CLOUDINARY_*` secrets already in GitHub.
  The Sheet script holds no keys: it reads the public `alpha-images.json` and writes one cell, with Google's
  narrowest permissions (this spreadsheet only, fetch a URL, run on a timer).
* **No public upload endpoint.** Only files linked from the Sheet (which only the owner and Alpha can edit) are
  ever uploaded, and only from Google Drive.

## Rules the code enforces

| Check | Where |
|---|---|
| Only Google Drive file links; a real JPEG or PNG (magic bytes + decoded); ≤ 10 MB; ≥ 600 px short side | `alpha_images.py` |
| Each Deal ID + Drive file is uploaded once; the same image can't be reused for another deal | `alpha_images.py` |
| Public ID `bestdealhunter/alpha/<deal-id>-<hash>`; `TEST-` deals go to `bestdealhunter/alpha-test/` | `alpha_images.py` |
| Upload is signed, `overwrite=false`; the URL must answer HTTP 200 with an image | `alpha_images.py` |
| The URL is written only into an **empty** `AI Image Cloudinary URL` cell of the row with the same Deal ID **and** Drive file | Apps Script |
| Publishing uses the image only from `alpha-images.json` (a URL typed into the Sheet is never trusted) and only when `Image Numbers Match` = Yes | `sheet_intake.py` |
| The AI image is **never** price evidence: every deal still needs `Screenshot Drive URL` (a real retailer page), `In Stock`, `Sold By Store`, a check under 3 hours old, 50–79 % off | `sheet_intake.py` |
| `TEST-` rows are never published to the website or Instagram | `sheet_intake.py` |

Screenshots stay in `Screenshot Drive URL` and are saved under `evidence/` in the repo; they are never uploaded
to the AI-image folder and never used as a deal picture.

## One-time setup (owner, about 5 minutes)

1. Open the BestDealHunter Deals Sheet → **Extensions → Apps Script**.
2. Delete what is in `Code.gs` and paste [`apps-script/AlphaImageSync.gs`](apps-script/AlphaImageSync.gs).
3. **Project Settings** (gear) → tick **Show "appsscript.json" manifest file in editor** → open `appsscript.json`
   and replace it with [`apps-script/appsscript.json`](apps-script/appsscript.json).
4. Back in the editor, choose **installTrigger** in the function menu → **Run** → approve the permissions
   ("See, edit… this spreadsheet only", "Connect to an external service", "Run when you're not present").
5. Done: every 10 minutes it fills `AI Image Cloudinary URL` for newly uploaded images. A new column with that
   name is added once at the end of the `Deal Queue` tab; no existing column moves.

## For Alpha

* Save the image in **BestDealHunter Deal Images** (Drive), copy the file's share link into the row's
  `AI Image Drive URL`. The row must have a `Deal ID`.
* Within ~15 minutes the Cloudinary URL appears in `AI Image Cloudinary URL`.
* If the image shows a price, it must exactly match the row's sale price, was-price and % (then set
  `Image Numbers Match` = Yes). Otherwise make a price-free image. Show the real product, nothing misleading.
* Setting an image never makes a deal publishable on its own: `Needs Claude review` still requires the retailer
  screenshot, seller and stock confirmation and a fresh time.
* To change an image after a row was processed, add a new row (reviewed rows are not edited).

Every result (uploaded / rejected with the reason) is in [`alpha-images.json`](../alpha-images.json).

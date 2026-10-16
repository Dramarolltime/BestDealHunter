/**
 * BestDealHunter: copy Alpha's verified Cloudinary image URLs into the Deal Queue tab (issue #59).
 *
 * Runs inside the owner's Google Sheet (Extensions > Apps Script). It holds NO passwords or API keys:
 * it only reads the public results file alpha-images.json that the GitHub "Alpha images" workflow writes
 * after uploading each image to Cloudinary, and writes a URL into the row's "AI Image Cloudinary URL"
 * cell when that cell is still empty, for the row whose Deal ID and AI Image Drive URL match.
 * It never changes any other cell, never edits a filled cell, and never sets Status.
 *
 * Setup: paste this file and appsscript.json, then run installTrigger() once and approve.
 */
const LEDGER_URL = 'https://raw.githubusercontent.com/Dramarolltime/BestDealHunter/main/alpha-images.json';
const TAB = 'Deal Queue';
const URL_COLUMN = 'AI Image Cloudinary URL';
const HOSTED = /^https:\/\/res\.cloudinary\.com\/[\w-]+\/image\/upload\/(v\d+\/)?bestdealhunter\/alpha(-test)?\/[\w.-]+\.(png|jpg)$/;

function syncAlphaImages() {
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(TAB)
      || SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];
  const resp = UrlFetchApp.fetch(LEDGER_URL + '?t=' + Date.now(), {muteHttpExceptions: true});
  if (resp.getResponseCode() !== 200) return 'results file not reachable (' + resp.getResponseCode() + ')';
  const ledger = JSON.parse(resp.getContentText());
  const values = sheet.getDataRange().getValues();
  const header = values[0].map(String);
  const idCol = header.indexOf('Deal ID');
  const driveCol = header.indexOf('AI Image Drive URL');
  if (idCol < 0 || driveCol < 0) return 'missing Deal ID or AI Image Drive URL column';
  let urlCol = header.indexOf(URL_COLUMN);
  if (urlCol < 0) {  // add the column once, at the end, so no existing column moves
    urlCol = header.length;
    sheet.getRange(1, urlCol + 1).setValue(URL_COLUMN);
  }
  let written = 0;
  ledger.filter(e => e.result === 'uploaded' && HOSTED.test(e.cloudinary_url || '')).forEach(e => {
    for (let r = 1; r < values.length; r++) {
      const sameDeal = String(values[r][idCol]).trim() === e.deal_id;
      const sameFile = String(values[r][driveCol]).indexOf(e.drive_file_id) >= 0;
      const empty = urlCol >= values[r].length || String(values[r][urlCol]).trim() === '';
      if (sameDeal && sameFile && empty) {
        sheet.getRange(r + 1, urlCol + 1).setValue(e.cloudinary_url);
        written++;
      }
    }
  });
  return written + ' URL(s) written';
}

function installTrigger() {
  ScriptApp.getProjectTriggers().forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('syncAlphaImages').timeBased().everyMinutes(10).create();
  Logger.log(syncAlphaImages());
}

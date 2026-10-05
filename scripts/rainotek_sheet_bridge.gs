// Run setupRainotek once, then deploy as a web app executing as your account.
// Only signed requests for this spreadsheet are accepted. Never log the secret.
function setupRainotek() {
  const p = PropertiesService.getScriptProperties();
  if (!p.getProperty('SECRET')) p.setProperty('SECRET', Utilities.getUuid() + Utilities.getUuid());
  p.setProperty('SHEET_ID', '1jDWTufbdaTqG8gAn8xHp096j91wl9_pDXeKbrZ8KLYg');
  return {ready: true};
}

function doGet() {
  return output_({ok: true, service: 'RAINOTEK', authenticatedWrites: true});
}

function output_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

function doPost(e) {
  const lock = LockService.getScriptLock();
  try {
    const req = JSON.parse(e.postData.contents);
    const p = PropertiesService.getScriptProperties();
    const secret = p.getProperty('SECRET');
    if (!secret || typeof req.payload !== 'string' || typeof req.signature !== 'string') throw Error('unauthorized');
    const signed = Utilities.computeHmacSha256Signature(req.payload, secret, Utilities.Charset.UTF_8);
    const expected = Utilities.base64EncodeWebSafe(signed);
    if (expected.length !== req.signature.length) throw Error('unauthorized');
    let diff = 0;
    for (let i = 0; i < expected.length; i++) diff |= expected.charCodeAt(i) ^ req.signature.charCodeAt(i);
    if (diff) throw Error('unauthorized');
    const body = JSON.parse(req.payload);
    if (!Number.isFinite(body.timestamp) || Math.abs(Date.now() - body.timestamp) > 120000) throw Error('expired');
    if (!lock.tryLock(20000)) throw Error('busy');
    if (typeof body.nonce !== 'string' || !/^[a-f0-9]{32}$/.test(body.nonce)) throw Error('invalid_request');
    const cache = CacheService.getScriptCache();
    if (cache.get(body.nonce)) throw Error('replay');
    cache.put(body.nonce, 'used', 240);
    const book = SpreadsheetApp.openById(p.getProperty('SHEET_ID'));
    const sheet = book.getSheets().find(s => s.getSheetId() === Number(body.gid || 0));
    if (!sheet) throw Error('worksheet_missing');
    if (body.action === 'read') return output_({ok: true, rows: sheet.getDataRange().getDisplayValues()});
    const edits = body.action === 'update' ? [body] : body.action === 'update_many' ? body.edits : null;
    if (!Array.isArray(edits) || !edits.length || edits.length > 500) throw Error('invalid_request');
    const width = sheet.getLastColumn();
    const requests = [];
    const cells = new Set();
    edits.forEach(edit => {
      if (!Number.isInteger(edit.row) || edit.row < 1 || edit.row > sheet.getLastRow()) throw Error('invalid_row');
      if (!Array.isArray(edit.expectedRow) || !Array.isArray(edit.changes)) throw Error('invalid_request');
      const current = sheet.getRange(edit.row, 1, 1, width).getDisplayValues()[0];
      if (JSON.stringify(current) !== JSON.stringify(edit.expectedRow)) throw Error('conflict');
      if (!edit.changes.length || edit.changes.length > 20) throw Error('invalid_changes');
      edit.changes.forEach(c => {
        if (!Number.isInteger(c.column) || c.column < 1 || c.column > width || typeof c.value !== 'string' || c.value.length > 1024) throw Error('invalid_cell');
        const key = edit.row + ':' + c.column;
        if (cells.has(key)) throw Error('invalid_changes');
        cells.add(key);
        requests.push({updateCells: {
          range: {sheetId: sheet.getSheetId(), startRowIndex: edit.row - 1, endRowIndex: edit.row, startColumnIndex: c.column - 1, endColumnIndex: c.column},
          rows: [{values: [{userEnteredValue: {stringValue: c.value}}]}], fields: 'userEnteredValue'
        }});
      });
    });
    // One atomic Google batch; only selected cells change, never unrelated formulas.
    // Human sheet edits are not locked by ScriptLock: keep the check/write window minimal.
    const response = UrlFetchApp.fetch('https://sheets.googleapis.com/v4/spreadsheets/' + p.getProperty('SHEET_ID') + ':batchUpdate', {
      method: 'post', contentType: 'application/json',
      headers: {Authorization: 'Bearer ' + ScriptApp.getOAuthToken()},
      payload: JSON.stringify({requests: requests}), muteHttpExceptions: true
    });
    if (response.getResponseCode() !== 200) throw Error('google_api_error');
    return output_({ok: true, rows: edits.map(edit => edit.row)});
  } catch (err) {
    const safe = ['unauthorized', 'expired', 'busy', 'replay', 'conflict', 'worksheet_missing', 'invalid_request', 'invalid_row', 'invalid_changes', 'invalid_cell'];
    return output_({ok: false, error: safe.includes(err.message) ? err.message : 'google_api_error'});
  } finally {
    if (lock.hasLock()) lock.releaseLock();
  }
}

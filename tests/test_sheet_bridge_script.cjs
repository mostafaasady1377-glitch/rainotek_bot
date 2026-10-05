const fs = require('fs');
const vm = require('vm');
const crypto = require('crypto');
const assert = require('assert/strict');
const source = fs.readFileSync('scripts/rainotek_sheet_bridge.gs', 'utf8');
const rows = [['model', 'price', '=SUM(A1:A2)'], ['Laptop', '50000', '=SUM(A1:A2)']];
const properties = {SECRET: 'test-only-secret', SHEET_ID: 'test-sheet'};
let held = false, calls = [], status = 200, cache = new Map();
const sheet = {getSheetId:()=>0, getLastColumn:()=>3, getLastRow:()=>2,
  getRange:row=>({getDisplayValues:()=>[rows[row-1].slice()]}), getDataRange:()=>({getDisplayValues:()=>rows})};
const context = {
  Utilities: {Charset:{UTF_8:'utf8'}, computeHmacSha256Signature:(p,s)=>crypto.createHmac('sha256',s).update(p).digest(),base64EncodeWebSafe:b=>Buffer.from(b).toString('base64url')+'=',getUuid:()=>crypto.randomUUID()},
  PropertiesService:{getScriptProperties:()=>({getProperty:k=>properties[k],setProperty:(k,v)=>properties[k]=v})},
  ContentService:{MimeType:{JSON:'json'},createTextOutput:text=>({setMimeType:()=>JSON.parse(text)})},
  LockService:{getScriptLock:()=>({tryLock:()=>held=true,hasLock:()=>held,releaseLock:()=>held=false})},
  CacheService:{getScriptCache:()=>({get:k=>cache.get(k),put:(k,v)=>cache.set(k,v)})},
  SpreadsheetApp:{openById:()=>({getSheets:()=>[sheet]})},
  ScriptApp:{getOAuthToken:()=> 'test-token'},
  UrlFetchApp:{fetch:(url,options)=>{calls.push(JSON.parse(options.payload));return {getResponseCode:()=>status};}},
};
vm.createContext(context); vm.runInContext(source,context);
function envelope(extra){const payload=JSON.stringify({timestamp:Date.now(),nonce:crypto.randomBytes(16).toString('hex'),gid:0,...extra});return {payload,signature:crypto.createHmac('sha256',properties.SECRET).update(payload).digest('base64url')+'='};}
function post(request){return context.doPost({postData:{contents:JSON.stringify(request)}});}
const edit={row:2,expectedRow:rows[1].slice(),changes:[{column:2,value:'55000'}]};
assert.equal(post(envelope({action:'update_many',edits:[edit]})).ok,true);
assert.equal(calls.length,1);assert.equal(calls[0].requests.length,1);
assert.equal(calls[0].requests[0].updateCells.range.startColumnIndex,1);
assert.equal(calls[0].requests[0].updateCells.fields,'userEnteredValue');
assert.equal(held,false);
assert.equal(post(envelope({action:'update',...edit,expectedRow:['stale']})).error,'conflict');
assert.equal(calls.length,1);
assert.equal(post(envelope({action:'update',...edit,changes:[{column:0,value:'bad'}]})).error,'invalid_cell');
const request=envelope({action:'read'});assert.equal(post(request).ok,true);assert.equal(post(request).error,'replay');
assert.equal(post({...envelope({action:'read'}),signature:'wrong'}).error,'unauthorized');
assert.equal(post(envelope({action:'read',timestamp:0})).error,'expired');
status=503;assert.equal(post(envelope({action:'update',...edit})).error,'google_api_error');assert.equal(held,false);
console.log('Apps Script mock tests: PASS (target cells, stale conflict, validation, replay, signature, expiry, API error, lock release)');

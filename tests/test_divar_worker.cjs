const fs=require('fs'),vm=require('vm'),assert=require('assert');
const values={'draft:1':{expires_at:Date.now()/1000+600},'draft:1:upload':false};let listener;
const chrome={runtime:{onMessage:{addListener:f=>listener=f}},tabs:{onRemoved:{addListener(){}}},storage:{session:{get:async keys=>Object.fromEntries((Array.isArray(keys)?keys:[keys]).map(k=>[k,values[k]])),set:async obj=>Object.assign(values,obj),remove:async keys=>(Array.isArray(keys)?keys:[keys]).forEach(k=>delete values[k])}}};
vm.runInNewContext(fs.readFileSync('browser_extensions/divar-handoff/worker.js','utf8'),{chrome,URL,Date});
const ask=type=>new Promise(resolve=>listener({type},{tab:{id:1},url:'https://divar.ir/new/PREMIUM_PANEL/category_StoreOne'},resolve));
(async()=>{
 assert.equal((await ask('upload_claim')).claimed,true);
 assert.ok((await ask('upload_claim')).error);
 assert.equal((await ask('get')).uploaded,true);
 await ask('clear');assert.equal(values['draft:1:upload'],undefined);
 console.log('4 worker checks passed: claim, duplicate prevention, reload persistence, cleanup');
})().catch(e=>{console.error(e);process.exit(1)});

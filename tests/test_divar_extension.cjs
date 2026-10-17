const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('browser_extensions/divar-handoff/content.js','utf8');
async function test(path,initial='',expected='Store title'){
 class Input {constructor(v=''){this._value=v;}get value(){return this._value;}set value(v){this._value=v;}dispatchEvent(){}}
 const title=new Input(initial),description=new Input(),price=new Input();
 const selectors={'[placeholder="عنوان آگهی خود را بنویسید"]':title,'[placeholder="توضیحات مربوط به آگهی را بنویسید"]':description,'input[name="price"]':price};
 const element=()=>({style:{},append(){},remove(){},isConnected:true,textContent:''});
 const document={createElement:element,body:{append(){}},documentElement:{},querySelector:s=>selectors[s]||null,querySelectorAll:s=>s.includes('عنوان')?[title,description,price]:[]};
 const ad={business_id:'StoreOne',expires_at:Date.now()/1000+600,title:'Store title',description:'Store description',price_toman:123456,photos:[]};
 const context={document,location:{pathname:path,hash:'',search:''},chrome:{runtime:{sendMessage:async()=>({ad})}},HTMLInputElement:Input,HTMLTextAreaElement:Input,Event:class{},MutationObserver:class{observe(){}},window:{addEventListener(){}},URLSearchParams,setTimeout,clearTimeout,Date,WeakSet,console};
 vm.runInNewContext(source,context);
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(title.value,expected);
 if(expected==='Store title')assert.equal(price.value,'123456');
}
(async()=>{
 await test('/new/PREMIUM_PANEL/category_StoreOne');
 await test('/new/PREMIUM_PANEL/category_OtherStore','','');
 await test('/new/PREMIUM_PANEL/category_StoreOne','Human draft','Human draft');
 console.log('3 extension autofill checks passed: own business, wrong business, preserve human draft');
})().catch(e=>{console.error(e);process.exit(1)});

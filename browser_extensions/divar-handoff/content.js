/* DOM-only form assistant. No private Divar API, cookies, submit, payment, or OTP access. */
(()=>{
  let ad=null, busy=false, uploaded=false;
  const ask=m=>chrome.runtime.sendMessage(m);
  const panel=document.createElement('section');
  panel.id='rainotek-handoff-panel';
  Object.assign(panel.style,{position:'fixed',bottom:'18px',right:'18px',zIndex:'2147483647',background:'#fff',color:'#222',padding:'16px',border:'1px solid #aaa',maxWidth:'350px',direction:'rtl',fontSize:'14px'});
  const status=document.createElement('p');
  const fill=document.createElement('button');fill.textContent='پرکردن فیلدهای این صفحه';
  const close=document.createElement('button');close.textContent='بستن بسته';
  panel.append(status,fill,close);
  const show=text=>{status.textContent=text;if(!panel.isConnected)document.body.append(panel);};
  close.onclick=async()=>{await ask({type:'clear'});ad=null;panel.remove();};
  const withinBusiness=()=>{
    const path=location.pathname;
    return path.startsWith('/new/PREMIUM_PANEL/')&&path.endsWith('_'+ad.business_id);
  };
  function setValue(element,value){
    if(!element)return false;
    if(element.value.trim()&&element.value!==String(value))throw Error('این فیلد متن یا مقدار دیگری دارد؛ ابتدا خودتان آن را بررسی کنید.');
    const type=element instanceof HTMLTextAreaElement?HTMLTextAreaElement:HTMLInputElement;
    Object.getOwnPropertyDescriptor(type.prototype,'value').set.call(element,String(value));
    element.dispatchEvent(new Event('input',{bubbles:true}));element.dispatchEvent(new Event('change',{bubbles:true}));
    if(element.value!==String(value))throw Error('پرکردن فیلد تأیید نشد.');
    return true;
  }
  async function uploadPhotos(){
    if(uploaded||!ad.photos.length)return false;
    const input=document.querySelector('input[type="file"][accept*="image"]');
    if(!input)return false;
    const old=[...document.querySelectorAll('button')].filter(b=>((b.getAttribute('aria-label')||'')+' '+b.textContent).includes('حذف عکس'));
    if(old.length)throw Error('گالری موجود است؛ برای جلوگیری از جایگزینی یا عکس تکراری، آن را خودتان بررسی کنید.');
    const transfer=new DataTransfer();
    for(const p of ad.photos){
      if(p.mime!=='image/jpeg'||typeof p.data!=='string'||p.data.length>5500000)throw Error('عکس نامعتبر است.');
      const bytes=Uint8Array.from(atob(p.data),c=>c.charCodeAt(0));
      const digest=[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(n=>n.toString(16).padStart(2,'0')).join('');
      if(digest!==p.sha256)throw Error('تمامیت عکس تأیید نشد.');
      transfer.items.add(new File([bytes],p.name,{type:p.mime}));
    }
    const claim=await ask({type:'upload_claim'});
    if(claim.error||!claim.claimed)throw Error(claim.error||'مجوز ارسال عکس تأیید نشد.');
    uploaded=true;
    input.files=transfer.files;input.dispatchEvent(new Event('change',{bubbles:true}));
    uploaded=true; // Never blindly retry a partially accepted gallery.
    return true;
  }
  fill.onclick=async()=>{
    if(busy||!ad)return;
    busy=true;fill.disabled=true;
    try{
      if(ad.expires_at*1000<=Date.now())throw Error('بسته منقضی شده؛ از بات لینک تازه بگیرید.');
      if(!withinBusiness())throw Error('از پنل همین فروشگاه، ثبت آگهی را باز کنید؛ فرم عمومی یا فروشگاه دیگر پر نمی‌شود.');
      const fields=[
        [document.querySelector('[placeholder="عنوان آگهی خود را بنویسید"]'),ad.title],
        [(document.querySelector('textarea[name="Description"]')||document.querySelector('[placeholder="توضیحات مربوط به آگهی را بنویسید"]')),ad.description],
        [(document.querySelector('input[name="price"]')||document.querySelector('input[name="Price"]')),ad.price_toman],
      ];
      for(const [element,value] of fields){
        if(element&&element.value.trim()&&element.value!==String(value))throw Error('فیلد دارای مقدار متفاوت است؛ قبل از ادامه خودتان بررسی کنید.');
      }
      let count=0;
      count+=setValue(document.querySelector('[placeholder="عنوان آگهی خود را بنویسید"]'),ad.title);
      count+=setValue((document.querySelector('textarea[name="Description"]')||document.querySelector('[placeholder="توضیحات مربوط به آگهی را بنویسید"]')),ad.description);
      count+=setValue((document.querySelector('input[name="price"]')||document.querySelector('input[name="Price"]')),ad.price_toman);
      const photos=await uploadPhotos();
      show(`${count} فیلد پر شد. ${photos?'عکس‌ها برای آپلود ارسال شدند؛ تا پایان واقعی آپلود صبر کنید.':''} وضعیت دستگاه، مشخصات تکمیلی و نقشه را بررسی کنید. ثبت و پرداخت با شماست.`);
    }catch(e){show(e.message);}finally{busy=false;fill.disabled=false;}
  };
  async function init(){
    const fragment=new URLSearchParams(location.hash.slice(1));
    const capsule=fragment.get('rainotek');
    let result;
    if(capsule){
      // Remove bearer material from the address bar before any later navigation.
      history.replaceState(null,'',location.pathname+location.search);
      try{
        if(capsule.length>2000)throw Error('invalid_link');
        const descriptor=JSON.parse(atob(capsule.replace(/-/g,'+').replace(/_/g,'/')));
        result=await ask({type:'redeem',...descriptor});
      }catch(e){show('لینک انتقال نامعتبر است؛ از بات لینک تازه بگیرید.');return;}
    }else result=await ask({type:'get'});
    if(result.error){show(result.error);return;}
    if(result.ad){ad=result.ad;uploaded=!!result.uploaded;show('بستهٔ آگهی دریافت شد. از پنل همین فروشگاه ثبت آگهی را باز کنید؛ فیلدهای خالی و عکس‌ها خودکار آماده می‌شوند. ثبت و پرداخت با شماست.');autoFill();}
  }
  const attempted=new WeakSet();
  function autoFill(){
    if(!ad||busy||!withinBusiness())return;
    const inputs=[...document.querySelectorAll('[placeholder="عنوان آگهی خود را بنویسید"],[placeholder="توضیحات مربوط به آگهی را بنویسید"],textarea[name="Description"],input[name="price"],input[name="Price"],input[type="file"][accept*="image"]')];
    const fresh=inputs.filter(e=>!attempted.has(e));
    if(!fresh.length)return;
    fresh.forEach(e=>attempted.add(e));
    fill.onclick();
  }
  let timer;
  const observer=new MutationObserver(()=>{clearTimeout(timer);timer=setTimeout(autoFill,500);});
  observer.observe(document.documentElement,{childList:true,subtree:true});
  window.addEventListener('popstate',autoFill);
  window.addEventListener('hashchange',()=>{if(new URLSearchParams(location.hash.slice(1)).has('rainotek'))init().catch(e=>show('اتصال افزونه برقرار نشد؛ صفحه را بازبینی کنید.'));});
  init().catch(e=>show('اتصال افزونه برقرار نشد؛ سرویس محلی و تنظیمات افزونه را بررسی کنید.'));
})();

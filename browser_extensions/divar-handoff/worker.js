chrome.runtime.onMessage.addListener((message,sender,reply)=>{
  (async()=>{
    if(!sender.tab||new URL(sender.url).origin!=='https://divar.ir') throw Error('invalid_sender');
    const key='draft:'+sender.tab.id;
    if(message.type==='redeem'){
      const configured=(await chrome.storage.local.get('handoffOrigin')).handoffOrigin;
      if(!configured||message.origin!==configured) throw Error('ابتدا آدرس رابط راینوتک را از تنظیمات افزونه ثبت کنید.');
      if(typeof message.ticket!=='string'||!/^[A-Za-z0-9_-]{32,100}$/.test(message.ticket)) throw Error('invalid_ticket');
      const response=await fetch(configured+'/v1/redeem',{
        method:'POST',credentials:'omit',cache:'no-store',redirect:'error',
        headers:{Authorization:'Bearer '+message.ticket},signal:AbortSignal.timeout(30000)
      });
      if(!response.ok) throw Error('لینک منقضی شده یا اطلاعات تغییر کرده؛ از بات لینک تازه بگیرید.');
      const ad=await response.json();
      if(ad.format!=='rainotek-ad-v1'||!Number.isFinite(ad.expires_at)||ad.expires_at*1000<=Date.now()||
         !/^[A-Za-z0-9_-]{3,80}$/.test(ad.business_id)||typeof ad.title!=='string'||typeof ad.description!=='string'||
         ad.title.length>250||ad.description.length>10000||!Number.isSafeInteger(ad.price_toman)||ad.price_toman<=0||
         !Array.isArray(ad.photos)||ad.photos.length!==15||
         ad.photos.some((p,i)=>p.kind!==(i<2?'shop':'single_laptop'))) throw Error('invalid_package');
      // The ticket is consumed only for the destination shown in this tab.
      if(!new URL(sender.url).pathname.includes('/business/'+ad.business_id)) throw Error('فروشگاه مقصد با بسته یکسان نیست.');
      await chrome.storage.session.set({[key]:ad,[key+':upload']:false});
      return {ad,uploaded:false};
    }
    if(message.type==='upload_claim'){
      const stored=await chrome.storage.session.get([key,key+':upload']);
      if(!stored[key]||stored[key].expires_at*1000<=Date.now())throw Error('بسته منقضی شده است.');
      if(stored[key+':upload'])throw Error('عکس‌های این بسته قبلاً برای آپلود ارسال شده‌اند؛ گالری را بررسی کنید.');
      await chrome.storage.session.set({[key+':upload']:true});
      return {claimed:true};
    }
    if(message.type==='get'){
      const ad=(await chrome.storage.session.get(key))[key];
      if(ad&&ad.expires_at*1000>Date.now()) return {ad,uploaded:!!(await chrome.storage.session.get(key+':upload'))[key+':upload']};
      await chrome.storage.session.remove([key,key+':upload']);
      return {};
    }
    if(message.type==='clear'){await chrome.storage.session.remove([key,key+':upload']);return {};}
    throw Error('invalid_message');
  })().then(reply).catch(e=>reply({error:e.message}));
  return true;
});
chrome.tabs.onRemoved.addListener(id=>chrome.storage.session.remove(['draft:'+id,'draft:'+id+':upload']));

chrome.storage.local.get('handoffOrigin').then(v => { document.querySelector('#origin').value=v.handoffOrigin||''; });
document.querySelector('#save').addEventListener('click',async()=>{
  const status=document.querySelector('#status');
  try {
    const u=new URL(document.querySelector('#origin').value.trim());
    const local=u.protocol==='http:'&&u.hostname==='127.0.0.1'&&u.port==='8769';
    if((u.protocol!=='https:'&&!local)||u.username||u.password||u.pathname!=='/'||u.search||u.hash) throw Error('آدرس HTTPS معتبر وارد کنید.');
    if(!await chrome.permissions.request({origins:[u.origin+'/*']})) throw Error('اجازهٔ اتصال داده نشد.');
    await chrome.storage.local.set({handoffOrigin:u.origin});
    status.textContent='ثبت شد. اکنون لینک انتقال را از بات باز کنید.';
  } catch(e){status.textContent=e.message;}
});

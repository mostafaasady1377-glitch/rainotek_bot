"""Build a review pack, never submit listings or impersonate product photography."""
import hashlib
import html
import json
import re
import shutil
import zipfile
from pathlib import Path

from divar_campaign.engine import ROOT, RUNTIME, draft, inventory, utcnow
from bot.services.model_photos import resolve_model_photo
from bot.services.laptop_assets import get_laptop_image_source


def normalized(value):
    return re.sub(r'[^a-z0-9]', '', str(value or '').lower())


def build():
    out = RUNTIME / 'pack-100'
    out.mkdir(parents=True, exist_ok=True)
    products = inventory(ROOT.parent / 'rainotek.db')
    candidates = sorted((p for p in products if p['available'] > 0 and (p['price'] or 0)>0
                         and p['status']=='active' and not any(s in p['brand'].lower()
                         for s in ('case','monitor','iphone'))), key=lambda p:p['price'])
    seen, ads = set(), []
    for p in candidates:
        key = tuple(normalized(p.get(f)) for f in ('brand','model','cpu','ram','storage','gpu'))
        if key in seen:
            continue
        seen.add(key)
        branch = p['branches'][0]
        d = draft(p, branch)
        number = len(ads)+1
        folder = out / f'{number:03d}-product-{p["id"]}'
        folder.mkdir(exist_ok=True)
        photo = resolve_model_photo(p['brand'],p['model'],cpu=p['cpu'],screen=p['screen_size'])
        kind = 'catalog_model_match_not_actual_unit'
        if not photo:
            _, _, photo = get_laptop_image_source(p['brand'],p['model'])
            kind = 'illustrative_not_exact_model'
        if not photo:
            raise ValueError(f'No local photo for product {p["id"]}')
        destination = folder / 'photo-reference.jpg'
        shutil.copyfile(photo, destination)
        d['photos'] = [{'file':destination.name,'kind':kind,
                        'sha256':hashlib.sha256(destination.read_bytes()).hexdigest(),
                        'rights_verified':False,'actual_unit_verified':False}]
        d['description'] += '\n\nبرای بررسی دستگاه و هماهنگی خرید حضوری از طریق چت همین آگهی پیام دهید.\nتصویر همراه، مرجع کاتالوگی است و عکس دستگاه عرضه‌شده تأیید نشده است.'
        d['blockers'] += ['عکس واقعی دستگاه و مجوز استفاده از تصویر تأیید شود',
                          'تازگی قیمت و موجودی شیت تأیید شود',
                          'آگهی‌های فعال حساب برای تشخیص تکراری بررسی شوند',
                          'سهمیه، تعرفه و schema جاری دیوار بررسی شوند']
        if any(str(p.get(f) or '').strip() in ('-','؟','?') for f in ('cpu','ram','storage','gpu')):
            d['blockers'].append('فیلد فنی دارای مقدار نامعتبر است')
        d.update(number=number, duplicate_key='|'.join(key), state='review_required',
                 publication_ready=False, folder=folder.name)
        (folder/'ad.json').write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8')
        (folder/'ad.txt').write_text(d['title']+'\n\n'+d['description']+'\n\nنیازمند بررسی:\n'+'\n'.join(d['blockers']),encoding='utf-8')
        ads.append(d)
        if len(ads)==100:
            break
    if len(ads)!=100:
        raise ValueError(f'Only {len(ads)} distinct stocked products; never duplicate to reach target')
    profile={'name':'راینوتک','latin_name':'RAINOTEK','category':'فروشگاه لپ‌تاپ',
             'description':'فروشگاه تخصصی لپ‌تاپ راینوتک (RAINOTEK)؛ معرفی مدل‌های موجود با مشخصات و قیمت روشن و هماهنگی بررسی و خرید حضوری.',
             'city':'تهران','primary_branch':None,
             'state':'local_draft_not_created_on_divar',
             'required':'تعیین شعبه اصلی، نشانی و تلفن عمومی فروشگاه، مجوز حساب و شرایط نمایش‌داده‌شده دیوار'}
    (out/'business-profile.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'manifest.json').write_text(json.dumps({'created_at':utcnow().isoformat(),'count':100,'ads':ads},ensure_ascii=False,indent=2),encoding='utf-8')
    e=lambda s:html.escape(str(s))
    cards=''.join(f'<article><img loading="lazy" src="{e(a["folder"])}/photo-reference.jpg"><div><h2>{a["number"]}. {e(a["title"])}</h2><b>{a["price_toman"]:,} تومان</b><p>عکس مرجع؛ نیازمند تأیید عکس واقعی و حقوق استفاده</p><details><summary>متن و کنترل‌های انتشار</summary><pre>{e(a["description"])}</pre><ul>'+''.join('<li>'+e(b)+'</li>' for b in a['blockers'])+f'</ul></details><a href="{e(a["folder"])}/ad.txt">متن آگهی</a></div></article>' for a in ads)
    page='''<!doctype html><html lang="fa" dir="rtl"><meta charset="utf-8"><title>۱۰۰ آگهی راینوتک</title><style>body{font:15px Tahoma;background:#f6f8fa;color:#17232b;max-width:1150px;margin:auto;padding:24px}h1{font-size:24px}h2{font-size:18px}article{display:flex;gap:24px;border-bottom:1px solid #ccc;padding:22px 0}img{width:220px;height:160px;object-fit:contain;background:white}pre{white-space:pre-wrap;font:14px Tahoma;line-height:1.9}p,li{line-height:1.9}a{color:#075b80}</style><h1>۱۰۰ آگهی لپ‌تاپ راینوتک | بسته بررسی</h1><p>۱۰۰ کالای متفاوت با کانفیگ و قیمت موجودی محلی انتخاب شده‌اند. این بسته آماده بررسی و تکمیل است؛ انتشار عمومی انجام نشده است. عکس‌ها مرجع هستند و عکس واقعی دستگاه و حق استفاده باید تأیید شود. تغییر عنوان یا عکس برای دور زدن تشخیص تکراری انجام نمی‌شود.</p>'''+cards+'</html>'
    (out/'index.html').write_text(page,encoding='utf-8')
    with zipfile.ZipFile(RUNTIME/'rainotek-100-ads-review.zip','w',zipfile.ZIP_DEFLATED) as z:
        for file in out.rglob('*'):
            if file.is_file():
                z.write(file,file.relative_to(out))
    return {'count':len(ads),'unique_keys':len({a['duplicate_key'] for a in ads}),
            'photos':sum((out/a['folder']/'photo-reference.jpg').is_file() for a in ads),
            'actual_unit_photos_verified':0,'ready_to_publish':0,'directory':str(out)}


if __name__=='__main__':
    print(json.dumps(build(),ensure_ascii=True,indent=2))

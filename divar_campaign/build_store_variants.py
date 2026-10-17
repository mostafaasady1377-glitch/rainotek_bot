"""Build local editorial alternatives. Does not submit or approve ads."""
import hashlib
import html
import json
import shutil
import zipfile
from pathlib import Path

from divar_campaign.engine import ROOT, RUNTIME, inventory, utcnow

TOPICS = [
 ('کار و درس', 'برای کلاس، تحقیق و کارهای روزمره، مشخصات را متناسب با نیازتان انتخاب کنید.'),
 ('مهندسی', 'نام نرم‌افزار مهندسی و نوع پروژه را بفرستید تا کانفیگ‌های موجود بررسی شوند.'),
 ('برنامه‌نویسی', 'از پروژه‌های دانشجویی تا توسعه نرم‌افزار؛ ابزارها و بودجه‌تان را بگویید.'),
 ('حسابداری', 'برای نرم‌افزار حسابداری و کار اداری، گزینه‌ها را بر اساس نیاز واقعی مقایسه کنید.'),
 ('دانشجویی', 'برای خرید دانشجویی، بودجه و رشته‌تان را بفرستید و انتخاب‌ها را مقایسه کنید.'),
 ('طراحی و گرافیک', 'نام نرم‌افزار طراحی و حجم پروژه، معیار بررسی دستگاه مناسب شماست.'),
 ('تدوین و محتوا', 'رزولوشن ویدیو و نرم‌افزار تدوین را بگویید تا انتخاب دقیق‌تری داشته باشید.'),
 ('بازی', 'نام بازی و انتظار عملکرد را مشخص کنید؛ موجودی مناسب بررسی می‌شود.'),
 ('کار اداری', 'برای دفتر و کار روزانه، مشخصات موردنیازتان را با گزینه‌های موجود بسنجید.'),
 ('دورکاری', 'برای جلسات و کار از خانه، اولویت‌های نمایشگر، وزن و باتری را بگویید.'),
 ('انتخاب اقتصادی', 'بودجه مشخص دارید؟ قیمت و مشخصات مدل‌های موجود را کنار هم بررسی کنید.'),
 ('خرید آگاهانه', 'قبل از انتخاب، پردازنده، رم، حافظه و وضعیت هر دستگاه را مقایسه کنید.'),
 ('پروژه دانشگاهی', 'کاربرد و نرم‌افزار پروژه‌تان را بفرستید؛ انتخاب را از نیازتان شروع کنید.'),
 ('کار چندوظیفه‌ای', 'تعداد برنامه‌های همزمان و حجم کارتان را بگویید تا مشخصات بررسی شوند.'),
 ('انتخاب بر اساس بودجه', 'بودجه و اولویت‌ها را مشخص کنید؛ قیمت هر کانفیگ جداگانه اعلام می‌شود.'),
 ('کارهای روزمره', 'برای وب، اسناد و استفاده معمول، انتخاب را متناسب با نیازتان انجام دهید.'),
 ('ارتقای ابزار کار', 'سیستم فعلی و نیاز جدیدتان را بگویید و گزینه‌های موجود را مقایسه کنید.'),
 ('مقایسه برندها', 'به جای انتخاب فقط بر اساس برند، مشخصات و وضعیت هر دستگاه را بررسی کنید.'),
 ('خرید حضوری', 'پیش از مراجعه، مدل و موجودی شعبه را در چت هماهنگ کنید.'),
 ('مشاوره خرید', 'کاربرد، بودجه و نرم‌افزارها را بفرستید تا گزینه‌های موجود معرفی شوند.'),
]

def build():
    products = inventory(ROOT.parent / 'rainotek.db')
    stocked = [p for p in products if p['status']=='active' and p['available']>0
               and not any(x in p['brand'].lower() for x in ('case','monitor','iphone'))]
    branches = {b['branch_id']: b for p in stocked for b in p['branches']}
    out = RUNTIME / 'store-100-review'
    photos_dir = out / 'reference-photos'
    photos_dir.mkdir(parents=True, exist_ok=True)
    sources = []
    first = RUNTIME / 'reference-photos'
    m = json.loads((first/'manifest.json').read_text(encoding='utf8'))
    sources += [(first/a['name'],a['url']) for a in m['assets']]
    second = RUNTIME/'web-reference-photos'
    m = json.loads((second/'manifest.json').read_text(encoding='utf8'))
    sources += [(second/(a['source']+'.jpg'),a['url']) for a in m if a['status']=='downloaded']
    photos, seen = [], set()
    for path,url in sources:
        if not path.exists(): continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in seen: continue
        seen.add(digest)
        shutil.copy2(path, photos_dir/path.name)
        photos.append({'path':'reference-photos/'+path.name,'source':url,
                       'status':'reference_only_not_cleared_for_publication'})
    assert len(photos)>=10, 'Need at least ten reference images'
    cover = next(p for p in photos if p['path'].endswith('businessinsider.jpg'))
    rest = [p for p in photos if p!=cover]
    ads=[]
    for branch_id,branch in sorted(branches.items()):
        available = [p for p in stocked if any(b['branch_id']==branch_id for b in p['branches'])]
        brands = sorted(set(p['brand'] for p in available))
        for topic,hook in TOPICS:
            number=len(ads)+1
            gallery=[cover]+[rest[(number+i)%len(rest)] for i in range(9)]
            title=f'راینوتک | لپ‌تاپ برای {topic}'
            text=(f'راینوتک (RAINOTEK) | {topic}\n{hook}\n\n'
                  f'برندهای دارای موجودی در این شعبه: {"، ".join(brands)}\n'
                  'قیمت، مشخصات و وضعیت هر کانفیگ را پیش از خرید در چت دریافت کنید.\n'
                  f'شعبه: {branch["name"]}\nنشانی: {branch["address"]}\n'
                  'بودجه و کاربردتان را پیام دهید؛ موجودی پیش از مراجعه دوباره بررسی می‌شود.')
            ad={'number':number,'title':title,'description':text,'branch':branch,
                'business_id':'mWYasTCg','topic':topic,'photos':gallery,
                'state':'editorial_alternative_requires_review','published':False,
                'blockers':['عکس‌ها مرجع هستند؛ اجازه استفاده و انطباق با موجودی تایید نشده است.',
                            'انتشار همزمان آگهی‌های کلی مشابه تایید نشده است.',
                            'محله ثبت باید با نشانی واقعی شعبه مطابقت داده شود.']}
            folder=out/f'{number:03d}';folder.mkdir(exist_ok=True)
            (folder/'ad.json').write_text(json.dumps(ad,ensure_ascii=False,indent=2),encoding='utf8')
            (folder/'ad.txt').write_text(title+'\n\n'+text,encoding='utf8')
            ads.append(ad)
    assert len(ads)==100, f'Expected five real branches; found {len(branches)}'
    assert len({(a['branch']['branch_id'],a['topic']) for a in ads})==100
    assert all(len(a['photos'])==10 and len({p['path'] for p in a['photos']})==10 for a in ads)
    (out/'manifest.json').write_text(json.dumps({'created':utcnow().isoformat(),
        'mode':'editorial_alternatives_not_mass_publication','ads':ads},ensure_ascii=False,indent=2),encoding='utf8')
    cards=[]
    for a in ads:
        cards.append(f'<article data-branch="{html.escape(a["branch"]["name"])}"><h2>{a["number"]}. {html.escape(a["title"])}</h2>'
          f'<p>{html.escape(a["branch"]["name"])}</p><pre>{html.escape(a["description"])}</pre>'
          +''.join(f'<img loading="lazy" src="{p["path"]}" alt="عکس مرجع؛ تاییدنشده">' for p in a['photos'])
          +f'<p><a href="{a["number"]:03d}/ad.txt">متن آماده</a> · عکس‌ها مرجع و نیازمند تایید هستند.</p></article>')
    (out/'index.html').write_text('<!doctype html><meta charset="utf-8"><title>۱۰۰ پیش‌نویس راینوتک</title>'
      '<style>body{direction:rtl;font:16px Tahoma;background:#eef2f6;margin:30px}article{background:white;padding:20px;margin:20px 0}pre{white-space:pre-wrap;font:inherit}img{width:150px;height:110px;object-fit:cover;margin:4px}</style>'
      '<h1>۱۰۰ پیش‌نویس فروشگاهی راینوتک</h1><p>۲۰ موضوع × ۵ شعبه واقعی؛ گزینه‌های انتخابی برای بررسی، نه ۱۰۰ مجوز انتشار. تصاویر خارجی مرجع‌اند و تصویر تاییدشده فروشگاه راینوتک نیستند.</p>'
      +''.join(cards),encoding='utf8')
    archive=RUNTIME/'rainotek-store-100-review.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for path in out.rglob('*'):
            if path.is_file():z.write(path,path.relative_to(out))
    print(json.dumps({'ads':len(ads),'real_branches':len(branches),'reference_files':len(photos),'photos_per_ad':10,'archive':str(archive)},ensure_ascii=False))

if __name__=='__main__':build()

"""Ordered, verified local shop and individual laptop galleries."""
import hashlib
import json
from pathlib import Path

GALLERY = Path(__file__).resolve().parents[1] / 'assets' / 'divar-gallery'


def gallery_files(seed, root=GALLERY):
    root=Path(root).resolve()
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('layout')!='2_shop_13_single' or not manifest.get('sets'):
        raise ValueError('گالری فروشگاهی معتبر نیست.')
    selected=manifest['sets'][seed % len(manifest['sets'])]
    if len(selected)!=15 or len({p['sha256'] for p in selected})!=15:
        raise ValueError('گالری باید ۱۵ عکس متفاوت داشته باشد.')
    result=[]
    for index,item in enumerate(selected):
        file=(root/item['file']).resolve()
        if not file.is_relative_to(root) or not file.is_file():
            raise ValueError('عکس گالری در دسترس نیست.')
        data=file.read_bytes()
        if len(data)>4_000_000 or hashlib.sha256(data).hexdigest()!=item['sha256']:
            raise ValueError('تمامیت یا حجم عکس گالری تأیید نشد.')
        result.append({'path':file,'sha256':item['sha256'],'kind':'shop' if index<2 else 'single_laptop'})
    return result

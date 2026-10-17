"""Local research and approval queue. Never writes to the RAINOTEK database."""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT.parent / 'scratch' / 'divar-campaign'
FIELDS = ('cpu', 'ram', 'storage', 'gpu', 'screen_size', 'condition', 'price')


def utcnow():
    return datetime.now(timezone.utc)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def observations():
    """Asking prices only. Model hints are not verified exact configurations."""
    hints = {
        'gaNXySMG': 'Lenovo ThinkPad E570', 'gaQndaeS': 'Lenovo LOQ 15IRX9',
        'gaQjdTkz': 'Lenovo IdeaPad 110', 'gaQbdsFt': 'MacBook Pro M3 Pro 18GB',
        'gaQPNVwB': 'MacBook Pro 2014', 'ga3i4r9g': 'ASUS VivoBook X1502ZA',
        'gaQLd9tU': 'Dell N5110', 'gaM3sPY9': 'ASUS ZenBook UX434F',
        'gaED6ep5': 'Dell 7710', 'gaQfZe3w': 'HP ZBook Studio G7',
        'gaOPhACv': 'Dell 3540',
    }
    run = json.loads((ROOT / 'data/run.json').read_text(encoding='utf-8'))
    with (ROOT / 'data/observed.tsv').open(encoding='utf-8', newline='') as f:
        rows = list(csv.DictReader(f, delimiter='\t'))
    for r in rows:
        r['price_toman'] = int(r['price_toman'])
        r['url'] = 'https://divar.ir/v/' + r['id']
        r['observed_date'] = run['observed_date']
        r['city'] = 'nearby' if 'شهرهای اطراف' in r['placement'] else 'tehran'
        r['model_hint'] = hints.get(r['id'])
        r['detail_verified'] = r['id'] in run['details']
        r['benchmark_eligible'] = r['detail_verified'] and r['condition'] != 'نیازمند تعمیر'
        r['benchmark_reason'] = ('کانفیگ از صفحه جزئیات خوانده شده؛ ادعای فروشنده' if r['benchmark_eligible']
                                 else 'مدل/کانفیگ کامل تأیید نشده یا خارج از محدوده')
    return rows


def inventory(db_path):
    """Read only product and branch data; never CRM, credentials or customer records."""
    con = sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    try:
        con.execute('BEGIN')
        rows = [dict(r) for r in con.execute('''SELECT l.id, b.name AS brand, l.model,
            l.cpu,l.ram,l.storage,l.gpu,l.screen_size,l.condition,l.price,l.status,l.image_url
            FROM laptops l JOIN laptop_brands b ON b.id=l.brand_id ORDER BY l.id''')]
        overrides = {r['laptop_id']: dict(r) for r in con.execute('SELECT * FROM laptop_staff_overrides')}
        stock = {}
        for r in con.execute('''SELECT i.laptop_id,b.id,b.name,b.address,b.address_full,
                i.quantity,i.reserved_count FROM branch_inventory i
                JOIN branches b ON b.id=i.branch_id WHERE b.is_active=1'''):
            available = max(0, r['quantity'] - r['reserved_count'])
            if available:
                stock.setdefault(r['laptop_id'], []).append({
                    'branch_id': r['id'], 'name': r['name'],
                    'address': r['address_full'] or r['address'], 'available': available})
        for row in rows:
            override = overrides.get(row['id'], {})
            for field in FIELDS:
                if override.get(field) is not None:
                    row[field] = override[field]
            row['branches'] = stock.get(row['id'], [])
            row['available'] = sum(b['available'] for b in row['branches'])
            # image_url may be a generic catalog image; ownership/exactness is not inferable.
            row['photo_verification'] = 'human_confirmation_required'
        return rows
    finally:
        con.close()


def draft(product, branch):
    blockers = []
    if product['status'] != 'active' or product['available'] < 1:
        blockers.append('کالا فعال و موجود نیست')
    if not product.get('price') or product['price'] <= 0:
        blockers.append('قیمت معتبر ندارد')
    for field in ('model', 'cpu', 'ram', 'storage', 'gpu', 'condition'):
        if not product.get(field) or str(product[field]).strip() in ('ثبت نشده', 'نامشخص', 'unknown'):
            blockers.append('مشخصات ناقص: ' + field)
    if branch['available'] < 1 or not branch.get('address'):
        blockers.append('موجودی یا نشانی شعبه ناقص است')
    title = f"{product['brand']} {product['model']} | RAM {product.get('ram') or '?'}"
    description = '\n'.join([
        'فروشگاه راینوتک (RAINOTEK)',
        f"مدل: {product['brand']} {product['model']}",
        f"پردازنده: {product.get('cpu') or 'نیازمند تکمیل'}",
        f"رم: {product.get('ram') or 'نیازمند تکمیل'}",
        f"حافظه: {product.get('storage') or 'نیازمند تکمیل'}",
        f"گرافیک: {product.get('gpu') or 'نیازمند تکمیل'}",
        f"وضعیت ثبت‌شده: {product.get('condition') or 'نیازمند تکمیل'}",
        f"قیمت نقدی: {product.get('price') or 0:,} تومان",
        f"شعبه: {branch['name']}",
        'نشانی: ' + (branch.get('address') or 'نیازمند تکمیل'),
    ])
    return {
        'product_id': product['id'], 'branch_id': branch['branch_id'],
        'source': 'rainotek_local_database_snapshot',
        'sheet_freshness': 'not_independently_verified',
        'inventory_fingerprint': fingerprint(product),
        'title': title, 'description': description, 'price_toman': product.get('price'),
        'city': 'tehran', 'category': 'laptop-notebook-macbook',
        'photos': [], 'photo_note': 'عکس واقعی و مجاز دستگاه باید انتخاب و تأیید شود',
        'blockers': blockers, 'market_price_suggestion': None,
        'market_note': 'برای پیشنهاد قیمت، دست‌کم ۳ آگهی تازه با مدل، CPU، RAM، GPU، حافظه و وضعیت یکسان لازم است.',
    }


class Queue:
    """Approvals bind exact payload + inventory. No network publisher is enabled."""
    def __init__(self, path):
        self.path = path
        with self.connect() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS drafts(
              id INTEGER PRIMARY KEY, payload TEXT NOT NULL, digest TEXT NOT NULL,
              state TEXT NOT NULL DEFAULT 'draft', approved_by TEXT, approved_until TEXT,
              remote_id TEXT);
              CREATE TABLE IF NOT EXISTS events(
              id INTEGER PRIMARY KEY, draft_id INTEGER, at TEXT, action TEXT, actor TEXT);''')

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def save(self, payload):
        digest = fingerprint(payload)
        with self.connect() as c:
            old = c.execute('SELECT id FROM drafts WHERE digest=?', (digest,)).fetchone()
            if old:
                return old['id']
            return c.execute('INSERT INTO drafts(payload,digest) VALUES(?,?)',
                             (json.dumps(payload, ensure_ascii=False), digest)).lastrowid

    def approve(self, draft_id, expected_digest, actor, product, *, photos_verified=False, sheet_verified=False):
        if not actor or not photos_verified or not sheet_verified:
            raise ValueError('تأیید عکس واقعی، تازگی شیت و هویت تأییدکننده لازم است')
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT * FROM drafts WHERE id=?', (draft_id,)).fetchone()
            if not row or row['state'] != 'draft' or row['digest'] != expected_digest:
                raise ValueError('نسخه پیش‌نویس یا وضعیت تغییر کرده است')
            payload = json.loads(row['payload'])
            if payload['blockers'] or not payload['photos']:
                raise ValueError('پیش‌نویس ناقص است')
            if fingerprint(product) != payload['inventory_fingerprint']:
                raise ValueError('موجودی یا قیمت تغییر کرده است؛ پیش‌نویس جدید بسازید')
            until = (utcnow() + timedelta(minutes=30)).isoformat()
            c.execute("UPDATE drafts SET state='approved',approved_by=?,approved_until=? WHERE id=?",
                      (actor, until, draft_id))
            c.execute('INSERT INTO events(draft_id,at,action,actor) VALUES(?,?,?,?)',
                      (draft_id, utcnow().isoformat(), 'approved_exact_payload', actor))

    def publication_payload(self, draft_id, product):
        """Validate before a future official connector. Does not submit anything."""
        with self.connect() as c:
            row = c.execute('SELECT * FROM drafts WHERE id=?', (draft_id,)).fetchone()
        if not row or row['state'] != 'approved':
            raise ValueError('تأیید انسانی برای این نسخه ثبت نشده است')
        if datetime.fromisoformat(row['approved_until']) < utcnow():
            raise ValueError('تأیید منقضی شده است')
        p = json.loads(row['payload'])
        if row['digest'] != fingerprint(p) or p['inventory_fingerprint'] != fingerprint(product):
            raise ValueError('اطلاعات تغییر کرده است؛ تأیید تازه لازم است')
        return p

    def publish(self, draft_id, product):
        self.publication_payload(draft_id, product)
        raise RuntimeError('publisher_not_configured: اتصال رسمی کنار دیوار و مجوز کسب‌وکار هنوز تنظیم نشده')


def build_report(db_path, out=RUNTIME):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    rows = observations()
    products = inventory(db_path)
    candidates = [p for p in products if p['status'] == 'active' and p['available'] > 0 and (p['price'] or 0) > 0]
    drafts = [draft(p, p['branches'][0]) for p in sorted(candidates, key=lambda p: p['price'])]
    queue = Queue(out / 'approval_queue.sqlite3')
    for d in drafts:
        queue_id = queue.save(d)
        d['review_digest'] = fingerprint(d)
        d['queue_id'] = queue_id
    (out / 'market.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'drafts.json').write_text(json.dumps(drafts, ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'inventory_snapshot.json').write_text(json.dumps(products, ensure_ascii=False, indent=2), encoding='utf-8')
    with (out / 'market.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    count_tehran = sum(r['city'] == 'tehran' for r in rows)
    e = lambda v: html.escape(str(v))
    market = ''.join(f"<tr><td><a target='_blank' rel='noopener' href='{e(r['url'])}'>{e(r['title'])}</a></td>"
                     f"<td>{r['price_toman']:,}</td><td>{e(r['condition'])}</td><td>{e(r['location'])}</td>"
                     f"<td>{e(r['model_hint'] or 'مدل دقیق نامشخص')}</td><td>{e(r['placement'])}</td></tr>" for r in rows)
    cards = ''.join(f"<details><summary>#{d['queue_id']} — {e(d['title'])} — {d['price_toman']:,} تومان</summary>"
                    f"<pre>{e(d['description'])}</pre><p>وضعیت: پیش‌نویس؛ عکس واقعی و تازگی شیت باید تأیید شود.</p>"
                    f"<p>{e('؛ '.join(d['blockers']))}</p></details>" for d in drafts)
    page = '''<!doctype html><html lang="fa" dir="rtl"><meta charset="utf-8"><title>تحلیل بازار و صف آگهی راینوتک</title>
    <style>body{font:15px Tahoma,sans-serif;max-width:1400px;margin:32px auto;padding:24px;background:#f7f8fa;color:#18222c}
    h1{font-size:24px}h2{margin-top:32px;font-size:20px}p{line-height:1.9}a{color:#075b80}table{width:100%;border-collapse:collapse;background:white}
    td,th{padding:12px;border-bottom:1px solid #ddd;text-align:right}th{background:#e9eef1}input{padding:12px;width:60%;margin:12px 0}
    details{padding:16px 0;border-bottom:1px solid #ccd3d8}summary{cursor:pointer}pre{white-space:pre-wrap;font:14px Tahoma;line-height:1.9}
    .notice{border-right:4px solid #075b80;padding:16px;background:white}.stats{font-size:18px}</style>
    <h1>راینوتک | تحلیل بازار و آماده‌سازی آگهی</h1><p>مشاهده بازار: ۱۵ اکتبر ۲۰۲۶؛ نمونه محدود از مرورگر. قیمت‌ها پیشنهادی فروشندگان هستند؛ قیمت معامله نیستند.</p>
    '''
    page += f"<p class='stats'>{len(rows)} آگهی — {count_tehran} تهران — {len(rows)-count_tehran} پیشنهاد اطراف — {len(drafts)} پیش‌نویس از موجودی محلی راینوتک</p>"
    page += '''<p class="notice">انتشار خاموش است. خواندن دیتابیس فقط خواندنی است. اتصال تازه به شیت در این ابزار تأیید نشده است.
    پیش‌نویس‌های زیر قیمت موجودی راینوتک را دارند؛ هیچ قیمت رقیبی جایگزین نشده است. هیچ عکس رقیبی برداشته نشده است.</p>
    <h2>معماری اجرا</h2><p>مشاهده بازار ← استخراج و کنترل کیفیت ← تطبیق کانفیگ دقیق ← خواندن موجودی راینوتک ← پیش‌نویس ← بررسی انسانی متن/قیمت/عکس/شعبه ← تأیید نسخه ثابت ← کنترل دوباره موجودی ← رابط رسمی دیوار ← دریافت شناسه و پیگیری وضعیت</p>
    <h2>آگهی نمونه شما</h2><p><a href="https://divar.ir/v/ga05juvO" target="_blank">ga05juvO</a> — ۱۸٬۵۰۰٬۰۰۰ تومان؛ ۲۰ عکس؛ مدل دقیق نامشخص.
    نقطه قوت: اشاره به ضمانت، تحویل و اقساط. پیشنهاد: به‌جای ادعای کلی، یک مدل مشخص با کانفیگ، عکس واقعی و قیمت همان دستگاه معرفی شود.
    ادعاهای سابقه، پایین‌ترین قیمت و شرایط اقساط فروشنده تأیید نشده‌اند و در متن راینوتک کپی نمی‌شوند.</p>
    <h2>آگهی‌های مشاهده‌شده</h2><p>این نمونه برای برآورد قیمت همه مدل‌ها کافی نیست. فقط LOQ 15IRX9 از نظر کانفیگ در صفحه جزئیات خوانده شد؛ یک نمونه برای تعیین قیمت بازار کافی نیست.</p>
    <input id="filter" placeholder="جست‌وجوی مدل، محله، وضعیت یا برند" oninput="filterRows(this.value)">
    <table><thead><tr><th>آگهی و منبع</th><th>قیمت (تومان)</th><th>وضعیت</th><th>محله</th><th>مدل از عنوان</th><th>زمینه نمایش</th></tr></thead><tbody>'''
    page += market + '</tbody></table><h2>پیش‌نویس‌های موجودی راینوتک</h2>' + cards
    page += '''<script>function filterRows(q){document.querySelectorAll('tbody tr').forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(q.toLowerCase()))}</script></html>'''
    (out / 'report.html').write_text(page, encoding='utf-8')
    summary = {'observed': len(rows), 'tehran': count_tehran, 'nearby': len(rows)-count_tehran,
               'inventory_products': len(products), 'in_stock_priced_products': len(candidates),
               'drafts': len(drafts), 'coverage': 'incomplete_sample', 'publication': 'disabled',
               'generated_at': utcnow().isoformat(), 'report': str(out / 'report.html')}
    (out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Prepare local Divar research and draft queue; never publishes')
    parser.add_argument('--inventory-db', type=Path, default=ROOT.parent / 'rainotek.db')
    parser.add_argument('--out', type=Path, default=RUNTIME)
    args = parser.parse_args()
    print(json.dumps(build_report(args.inventory_db, args.out), ensure_ascii=True, indent=2))

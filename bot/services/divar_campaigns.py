"""Persistent, version-bound editorial approvals. No external posting side effects."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from divar_campaign.engine import inventory, fingerprint, draft

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / 'scratch/divar-campaign/pack-100'
STORE = ROOT / 'scratch/divar-campaign/seller-review.sqlite3'


def version(ad):
    return fingerprint(ad)


class CampaignStore:
    def __init__(self, path=STORE, pack=PACK, inventory_db=None):
        self.path, self.pack = Path(path), Path(pack)
        self.inventory_db = Path(inventory_db or ROOT / 'rainotek.db')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS ads(
              number INTEGER PRIMARY KEY, digest TEXT NOT NULL, payload TEXT NOT NULL,
              state TEXT NOT NULL DEFAULT 'draft', approved_by INTEGER, approved_at TEXT);
              CREATE TABLE IF NOT EXISTS review_events(
              id INTEGER PRIMARY KEY, number INTEGER, digest TEXT, actor INTEGER,
              action TEXT, created_at TEXT);
              CREATE TABLE IF NOT EXISTS shop_queue(
              number INTEGER PRIMARY KEY, digest TEXT NOT NULL, state TEXT NOT NULL,
              remote_id TEXT);''')

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def sync(self):
        ads = json.loads((self.pack / 'manifest.json').read_text(encoding='utf-8'))['ads']
        if len(ads) != 100 or len({a['number'] for a in ads}) != 100:
            raise ValueError('بسته باید دقیقاً ۱۰۰ آگهی یکتا داشته باشد')
        products={p['id']:p for p in inventory(self.inventory_db)}
        fresh_ads=[]
        for saved in ads:
            ad=dict(saved)
            ad['source_pack_digest']=version(saved)
            product=products.get(ad['product_id'])
            branch=next((b for b in (product or {}).get('branches',[]) if b['branch_id']==ad['branch_id']),None)
            if product and branch:
                ad.update(draft(product,branch))
                from bot.services.divar_store_copy import apply_store_copy
                apply_store_copy(ad,product,seed=ad["number"]-1)
                ad['photos']=saved['photos']

                ad['blockers']+=['عکس واقعی و حق استفاده تأیید شود','تازگی شیت و عدم تکرار در دیوار بررسی شود']
            else:
                ad['inventory_fingerprint']='unavailable'
                ad['blockers']=list(ad['blockers'])+['کالا یا موجودی شعبه در دسترس نیست']
            fresh_ads.append(ad)
        ads=fresh_ads
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            for ad in ads:
                digest = version(ad)
                old = c.execute('SELECT digest FROM ads WHERE number=?', (ad['number'],)).fetchone()
                if not old:
                    c.execute('INSERT INTO ads(number,digest,payload) VALUES(?,?,?)',
                              (ad['number'], digest, json.dumps(ad, ensure_ascii=False)))
                elif old['digest'] != digest:
                    c.execute("UPDATE ads SET digest=?,payload=?,state='draft',approved_by=NULL,approved_at=NULL WHERE number=?",
                              (digest, json.dumps(ad, ensure_ascii=False), ad['number']))
                    c.execute('DELETE FROM shop_queue WHERE number=?', (ad['number'],))
            numbers = {a['number'] for a in ads}
            for row in c.execute('SELECT number FROM ads').fetchall():
                if row['number'] not in numbers:
                    c.execute('DELETE FROM shop_queue WHERE number=?', (row['number'],))
                    c.execute('DELETE FROM ads WHERE number=?', (row['number'],))

    def list(self, branch_id=None, shop=False):
        with self.connect() as c:
            rows = c.execute('SELECT a.* FROM ads a '+
                             ('JOIN shop_queue q ON q.number=a.number AND q.digest=a.digest ' if shop else '')+
                             'ORDER BY a.number').fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item['ad'] = json.loads(item.pop('payload'))
            if branch_id is None or item['ad']['branch_id'] == branch_id:
                result.append(item)
        return result

    def get(self, number):
        return next((r for r in self.list() if r['number'] == number), None)

    def photo(self, ad):
        path = (self.pack / ad['folder'] / ad['photos'][0]['file']).resolve()
        if not path.is_relative_to(self.pack.resolve()) or not path.is_file():
            raise ValueError('فایل عکس معتبر نیست')
        if hashlib.sha256(path.read_bytes()).hexdigest() != ad['photos'][0]['sha256']:
            raise ValueError('عکس تغییر کرده؛ بسته دوباره ساخته شود')
        return path

    def approve(self, number, digest_prefix, actor):
        """Approve copy for local shop preparation only, never approve public publication."""
        current = {p['id']: p for p in inventory(self.inventory_db)}
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT * FROM ads WHERE number=?', (number,)).fetchone()
            if not row or len(digest_prefix) != 16 or row['digest'][:16] != digest_prefix:
                raise ValueError('نسخه آگهی تغییر کرده؛ پیش‌نمایش تازه را باز کنید')
            ad = json.loads(row['payload'])
            self.photo(ad)
            product = current.get(ad['product_id'])
            if not product or fingerprint(product) != ad['inventory_fingerprint']:
                raise ValueError('قیمت، مشخصات یا موجودی تغییر کرده؛ بسته باید تازه شود')
            if row['state'] == 'editorial_approved':
                return False
            now = datetime.now(timezone.utc).isoformat()
            c.execute("UPDATE ads SET state='editorial_approved',approved_by=?,approved_at=? WHERE number=?",
                      (actor, now, number))
            c.execute("INSERT OR REPLACE INTO shop_queue(number,digest,state) VALUES(?,?,'blocked_pending_checks')",
                      (number, row['digest']))
            c.execute('INSERT INTO review_events(number,digest,actor,action,created_at) VALUES(?,?,?,?,?)',
                      (number, row['digest'], actor, 'editorial_approved_for_local_shop', now))
        return True

    def revoke(self, number, actor):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute("UPDATE ads SET state='draft',approved_by=NULL,approved_at=NULL WHERE number=?", (number,))
            c.execute('DELETE FROM shop_queue WHERE number=?', (number,))
            c.execute('INSERT INTO review_events(number,actor,action,created_at) VALUES(?,?,?,?)',
                      (number, actor, 'revoked', datetime.now(timezone.utc).isoformat()))

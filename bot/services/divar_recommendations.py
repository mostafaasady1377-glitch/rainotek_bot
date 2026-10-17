"""Restart-safe personal suggestions. Never submits posts or shares browser sessions."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import re
from urllib.parse import urlsplit

from bot.services.divar_campaigns import CampaignStore, STORE
from divar_campaign.engine import inventory, draft, fingerprint

PERIOD = 2 * 60 * 60
DAY = 86400
TEHRAN_OFFSET = 12600
DAILY_LIMIT = 10
BATCH_COUNT = 10
DAILY_SLOTS = DAILY_LIMIT * BATCH_COUNT
from bot.services.divar_store_copy import STORE_TITLES,apply_store_copy


def day_start(now):
    return ((int(now)+TEHRAN_OFFSET)//DAY)*DAY-TEHRAN_OFFSET


def build_ad(p, branch):
    ad = draft(p,branch)
    apply_store_copy(ad,p)
    ad['blockers'] += ['تازگی موجودی و محدودیت آگهی تکراری دیوار باید بررسی شود']
    return ad


class RecommendationStore(CampaignStore):
    def __init__(self, path=STORE, inventory_db=None):
        super().__init__(path=path, inventory_db=inventory_db)
        with self.connect() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS personal_ads(
                    id INTEGER PRIMARY KEY, slot INTEGER NOT NULL, owner INTEGER NOT NULL,
                    product_id INTEGER NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'suggested', created_at INTEGER NOT NULL,
                    UNIQUE(slot,owner), UNIQUE(slot,product_id));
                CREATE INDEX IF NOT EXISTS personal_ads_history ON personal_ads(owner,product_id,slot);
                CREATE TABLE IF NOT EXISTS personal_ad_events(
                    id INTEGER PRIMARY KEY, assignment_id INTEGER NOT NULL,
                    actor INTEGER NOT NULL, action TEXT NOT NULL, at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS seller_divar_links(
                    owner INTEGER PRIMARY KEY, business_id TEXT NOT NULL,
                    url TEXT NOT NULL, updated_at INTEGER NOT NULL,
                    verification TEXT NOT NULL DEFAULT 'link_only');
            ''')

    def register_link(self, owner, url):
        url = url.strip()
        try:
            parsed = urlsplit(url)
            if parsed.scheme!='https' or parsed.netloc!='divar.ir' or parsed.query or parsed.fragment:
                raise ValueError()
            match = re.fullmatch(r'/(?:my-divar/my-business/|my-divar/my-posts/business/|pro/)([A-Za-z0-9_-]{3,80})(?:/panel/posts)?/?', parsed.path)
            if not match:
                raise ValueError()
        except ValueError:
            raise ValueError('لینک کامل و مستقیم فروشگاه در https://divar.ir را بفرستید؛ لینک آگهی یا سایت دیگر پذیرفته نمی‌شود.') from None
        if not any(u['telegram_id']==owner for u in self.staff()):
            raise ValueError('این حساب در فهرست مدیران یا کارشناسان فعال نیست؛ مدیر باید دسترسی را بررسی کند.')
        business_id = match[1]
        canonical = f'https://divar.ir/my-divar/my-posts/business/{business_id}'
        with self.connect() as c:
            c.execute('''INSERT INTO seller_divar_links(owner,business_id,url,updated_at)
                VALUES(?,?,?,?) ON CONFLICT(owner) DO UPDATE SET business_id=excluded.business_id,
                url=excluded.url,updated_at=excluded.updated_at,verification='link_only' ''',
                (owner,business_id,canonical,int(time.time())))
        return canonical

    def account_link(self, owner):
        with self.connect() as c:
            row = c.execute('SELECT * FROM seller_divar_links WHERE owner=?',(owner,)).fetchone()
        return dict(row) if row else None

    def staff(self):
        c = sqlite3.connect(self.inventory_db.resolve().as_uri() + '?mode=ro', uri=True)
        c.row_factory = sqlite3.Row
        try:
            from bot.config import get_settings
            admin_ids=set(get_settings().ADMIN_TELEGRAM_IDS)
            result=[]
            for record in c.execute("SELECT telegram_id,managed_branch_id,role FROM users WHERE is_active=1 AND telegram_id>0 ORDER BY telegram_id"):
                user=dict(record)
                user['is_admin']=user['telegram_id'] in admin_ids
                if user['is_admin'] or user['role'] in ('seller','branch_manager'):
                    result.append(user)
            return result
        finally:
            c.close()

    def allocate(self, *, now=None, staff=None, products=None, slot_override=None):
        now = int(time.time() if now is None else now)
        slot = now // PERIOD if slot_override is None else slot_override
        staff = self.staff() if staff is None else staff
        products = inventory(self.inventory_db) if products is None else products
        candidates = [p for p in products if p['status']=='active' and p['available']>0 and (p.get('price') or 0)>0]
        # A stable rotating order avoids always giving scarce stock to the lowest staff ID.
        ordered = sorted(staff, key=lambda u: fingerprint([slot,u['telegram_id']]))
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            used = {r[0] for r in c.execute('SELECT product_id FROM personal_ads WHERE slot=?',(slot,))}
            for user in ordered:
                owner = user['telegram_id']
                existing = c.execute('SELECT * FROM personal_ads WHERE slot=? AND owner=?',(slot,owner)).fetchone()
                if existing:
                    p = next((p for p in candidates if p['id']==existing['product_id']),None)
                    branch = next((b for b in (p or {}).get('branches',[]) if (user.get('is_admin',False) or b['branch_id']==user['managed_branch_id']) and b['available']>0),None)
                    if not branch:
                        c.execute("UPDATE personal_ads SET state='unavailable' WHERE id=?",(existing['id'],))
                    else:
                        ad = build_ad(p,branch)
                        previous=json.loads(existing['payload'])
                        if previous.get('custom_text') and previous.get('inventory_fingerprint')==ad['inventory_fingerprint']:
                            ad.update(title=previous['title'],description=previous['description'],custom_text=True)
                        if fingerprint(ad)!=existing['digest'] or existing['state']=='unavailable':
                            c.execute("UPDATE personal_ads SET payload=?,digest=?,state='suggested' WHERE id=?",
                                      (json.dumps(ad,ensure_ascii=False),fingerprint(ad),existing['id']))
                    continue
                eligible = []
                for p in candidates:
                    branch = next((b for b in p['branches'] if (user.get('is_admin',False) or b['branch_id']==user['managed_branch_id']) and b['available']>0),None)
                    if p['id'] not in used and branch:
                        last = c.execute('SELECT MAX(slot) FROM personal_ads WHERE owner=? AND product_id=?',(owner,p['id'])).fetchone()[0]
                        eligible.append((last if last is not None else -1, fingerprint([slot,owner,p['id']]),p,branch))
                if not eligible:
                    continue  # Never manufacture stock or duplicate another seller's suggestion.
                _,_,p,branch = min(eligible,key=lambda item:item[:2])
                ad = build_ad(p,branch)
                c.execute('INSERT INTO personal_ads(slot,owner,product_id,payload,digest,created_at) VALUES(?,?,?,?,?,?)',
                          (slot,owner,p['id'],json.dumps(ad,ensure_ascii=False),fingerprint(ad),now))
                used.add(p['id'])

    def current(self, owner, *, now=None):
        slot = int(time.time() if now is None else now)//PERIOD
        with self.connect() as c:
            row = c.execute('SELECT * FROM personal_ads WHERE slot=? AND owner=?',(slot,owner)).fetchone()
        return self.decode(row)

    def allocate_daily(self, *, now=None, staff=None, products=None, batch=0):
        now=int(time.time() if now is None else now)
        staff=self.staff() if staff is None else staff
        products=inventory(self.inventory_db) if products is None else products
        # Negative slots isolate daily packs from legacy two-hour assignments.
        base=-((day_start(now)+TEHRAN_OFFSET)//DAY+1)*DAILY_SLOTS
        batch=max(0,min(int(batch),BATCH_COUNT-1))
        for index in range(DAILY_LIMIT):
            prior=self.daily_rows(None,now=now,batch=None)
            used={r['product_id'] for r in prior if r['slot']!=base+batch*DAILY_LIMIT+index}
            self.allocate(now=now,staff=staff,products=[p for p in products if p['id'] not in used],slot_override=base+batch*DAILY_LIMIT+index)

    def daily_rows(self, owner, *, now=None,batch=0):
        now=int(time.time() if now is None else now)
        base=-((day_start(now)+TEHRAN_OFFSET)//DAY+1)*DAILY_SLOTS
        lower=base if batch is None else base+max(0,min(int(batch),BATCH_COUNT-1))*DAILY_LIMIT
        upper=base+DAILY_SLOTS if batch is None else lower+DAILY_LIMIT
        with self.connect() as c:
            rows=c.execute('SELECT * FROM personal_ads WHERE slot>=? AND slot<?'+(' AND owner=?' if owner is not None else '')+' ORDER BY slot',
                           (lower,upper,owner) if owner is not None else (lower,upper)).fetchall()
        return [self.decode(r) for r in rows]

    @staticmethod
    def decode(row):
        return dict(row, ad=json.loads(row['payload'])) if row else None

    def review_transfer(self, assignment_id, owner, digest, *, now=None):
        """Record an exact human request; disconnected accounts cannot become queued/successful."""
        now = int(time.time() if now is None else now)
        staff = self.staff()
        user = next((s for s in staff if s['telegram_id']==owner),None)
        products = inventory(self.inventory_db)
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT * FROM personal_ads WHERE id=? AND owner=?',(assignment_id,owner)).fetchone()
            daily_base=-((day_start(now)+TEHRAN_OFFSET)//DAY+1)*DAILY_SLOTS
            valid_slot=bool(row and (row['slot']==now//PERIOD or daily_base<=row['slot']<daily_base+DAILY_SLOTS))
            if not row or not user or not valid_slot:
                raise ValueError('پیشنهاد منقضی شده یا متعلق به حساب شما نیست؛ پیشنهاد تازه را باز کنید.')
            ad = json.loads(row['payload'])
            if row['digest'][:16]!=digest or row['digest']!=fingerprint(ad):
                raise ValueError('نسخهٔ آگهی تغییر کرده؛ دوباره بازبینی کنید.')
            p = next((p for p in products if p['id']==row['product_id']),None)
            if not p or fingerprint(p)!=ad['inventory_fingerprint'] or (not user.get('is_admin',False) and user['managed_branch_id']!=ad['branch_id']):
                raise ValueError('قیمت، مشخصات، موجودی یا شعبه تغییر کرده؛ پیشنهاد نیازمند بازسازی است.')
            if row['state']=='suggested':
                c.execute("UPDATE personal_ads SET state='blocked_account_connection' WHERE id=?",(assignment_id,))
                c.execute('INSERT INTO personal_ad_events(assignment_id,actor,action,at) VALUES(?,?,?,?)',
                          (assignment_id,owner,'transfer_requested_connection_required',now))
        return 'blocked_account_connection'


    def edit_text(self, assignment_id, owner, digest, title, description):
        title=title.strip(); description=description.strip()
        if not 3<=len(title)<=70 or not 10<=len(description)<=2800:
            raise ValueError('عنوان باید ۳ تا ۷۰ حرف و توضیحات ۱۰ تا ۲۸۰۰ حرف باشد.')
        self.review_transfer(assignment_id,owner,digest)
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT * FROM personal_ads WHERE id=? AND owner=?',(assignment_id,owner)).fetchone()
            if not row or row['digest'][:16]!=digest:
                raise ValueError('نسخه آگهی تغییر کرده؛ دوباره باز کنید.')
            ad=json.loads(row['payload'])
            ad.update(title=title,description=description,custom_text=True)
            c.execute("UPDATE personal_ads SET payload=?,digest=?,state='suggested' WHERE id=?",
                      (json.dumps(ad,ensure_ascii=False),fingerprint(ad),assignment_id))
            c.execute('INSERT INTO personal_ad_events(assignment_id,actor,action,at) VALUES(?,?,?,?)',
                      (assignment_id,owner,'text_edited',int(time.time())))


async def recommendation_loop():
    store = RecommendationStore()
    while True:
        try:
            await asyncio.to_thread(store.allocate_daily)
        except Exception:
            from loguru import logger
            logger.exception('Personal Divar suggestion refresh failed')
        await asyncio.sleep(60)

"""Opaque one-use handoff tickets. No Divar cookies, credentials, or posting calls."""
import base64
import hashlib
import json
import os
import secrets
import time
from urllib.parse import urlsplit

from bot.services.divar_recommendations import RecommendationStore, PERIOD, DAY, day_start
from bot.services.divar_campaigns import ROOT


class HandoffStore(RecommendationStore):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        with self.connect() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS divar_handoff_tickets(
                ticket_hash TEXT PRIMARY KEY, owner INTEGER NOT NULL, assignment_id INTEGER NOT NULL,
                digest TEXT NOT NULL, business_id TEXT NOT NULL, expires_at INTEGER NOT NULL,
                used_at INTEGER)''')

    def issue(self, assignment_id, owner, digest, *, base_url=None, now=None):
        now=int(time.time() if now is None else now)
        from bot.config import get_settings
        base=(base_url or get_settings().DIVAR_HANDOFF_BASE_URL).rstrip('/')
        parts=urlsplit(base)
        local=(parts.scheme=='http' and parts.hostname=='127.0.0.1' and parts.port==8769)
        if (parts.scheme!='https' and not local) or not parts.hostname or parts.username or parts.password or parts.path or parts.query or parts.fragment:
            raise ValueError('آدرس HTTPS رابط انتقال هنوز تنظیم نشده است؛ لینک فروشگاه به‌تنهایی آگهی را منتقل نمی‌کند.')
        account=self.account_link(owner)
        if not account:
            raise ValueError('ابتدا لینک فروشگاه خودتان را ثبت کنید.')
        self.review_transfer(assignment_id,owner,digest,now=now)
        ticket=secrets.token_urlsafe(32)
        with self.connect() as c:
            c.execute('DELETE FROM divar_handoff_tickets WHERE expires_at<?',(now-86400,))
            c.execute('INSERT INTO divar_handoff_tickets VALUES(?,?,?,?,?,?,NULL)',
                (hashlib.sha256(ticket.encode()).hexdigest(),owner,assignment_id,digest,account['business_id'],min(now+900,day_start(now)+DAY)))
        fragment=base64.urlsafe_b64encode(json.dumps({'origin':base,'ticket':ticket},separators=(',',':')).encode()).decode().rstrip('=')
        return account['url']+'#rainotek='+fragment

    def redeem(self, ticket, *, now=None):
        now=int(time.time() if now is None else now)
        if not isinstance(ticket,str) or not 32<=len(ticket)<=100:
            raise ValueError('درخواست نامعتبر است.')
        key=hashlib.sha256(ticket.encode()).hexdigest()
        with self.connect() as c:
            row=c.execute('SELECT * FROM divar_handoff_tickets WHERE ticket_hash=?',(key,)).fetchone()
        if not row or row['used_at'] is not None or row['expires_at']<=now:
            raise ValueError('لینک مصرف شده یا منقضی است؛ از بات لینک تازه بگیرید.')
        account=self.account_link(row['owner'])
        if not account or account['business_id']!=row['business_id']:
            raise ValueError('فروشگاه تغییر کرده است؛ لینک تازه بگیرید.')
        self.review_transfer(row['assignment_id'],row['owner'],row['digest'],now=now)
        with self.connect() as c:
            adrow=c.execute('SELECT * FROM personal_ads WHERE id=?',(row['assignment_id'],)).fetchone()
        ad=json.loads(adrow['payload'])
        if adrow['digest'][:16]!=row['digest']:
            raise ValueError('اطلاعات تازه شده؛ لینک تازه بگیرید.')
        from bot.services.divar_gallery import gallery_files
        photos=[]
        for index,photo in enumerate(gallery_files(ad['product_id'])):
            data=photo['path'].read_bytes()
            photos.append({'name':f'gallery-{index+1:02}.jpg','mime':'image/jpeg',
                           'kind':photo['kind'],'sha256':photo['sha256'],
                           'data':base64.b64encode(data).decode()})
        result={'format':'rainotek-ad-v1','assignment_id':row['assignment_id'],
                'business_id':row['business_id'],'expires_at':row['expires_at'],
                'title':ad['title'],'description':ad['description'],'price_toman':ad['price_toman'],
                'photos':photos,'blockers':ad['blockers'],'submitted':False,'paid':False}
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            latest=c.execute('SELECT digest FROM personal_ads WHERE id=?',(row['assignment_id'],)).fetchone()
            account_now=c.execute('SELECT business_id FROM seller_divar_links WHERE owner=?',(row['owner'],)).fetchone()
            if not latest or latest['digest']!=adrow['digest'] or not account_now or account_now['business_id']!=row['business_id']:
                raise ValueError('اطلاعات یا فروشگاه تغییر کرده؛ لینک تازه بگیرید.')
            if c.execute('UPDATE divar_handoff_tickets SET used_at=? WHERE ticket_hash=? AND used_at IS NULL AND expires_at>?',(now,key,now)).rowcount!=1:
                raise ValueError('لینک قبلاً استفاده شده است.')
            c.execute("UPDATE personal_ads SET state='browser_package_delivered' WHERE id=?",(row['assignment_id'],))
        return result

"""Private customer messages and explicit administrator replies, stored locally."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import time
from aiogram import BaseMiddleware
from sqlalchemy import select
from database.models import User
from database.session import AsyncSessionLocal

STORE=Path(__file__).resolve().parents[2]/'scratch/customer-inbox.sqlite3'


class InboxStore:
    def __init__(self,path=STORE):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS customer_inbox(
                id INTEGER PRIMARY KEY,chat_id INTEGER NOT NULL,message_id INTEGER NOT NULL,
                name TEXT NOT NULL,username TEXT,body TEXT NOT NULL,kind TEXT NOT NULL,
                created_at INTEGER NOT NULL,UNIQUE(chat_id,message_id));
                CREATE TABLE IF NOT EXISTS inbox_replies(
                admin_id INTEGER NOT NULL,message_id INTEGER NOT NULL,target INTEGER NOT NULL,
                state TEXT NOT NULL,remote_id INTEGER,created_at INTEGER NOT NULL,
                PRIMARY KEY(admin_id,message_id));''')
            if "body" not in {row[1] for row in c.execute("PRAGMA table_info(inbox_replies)")}:
                c.execute("ALTER TABLE inbox_replies ADD COLUMN body TEXT")

    @contextmanager
    def connect(self):
        c=sqlite3.connect(self.path,timeout=15)
        c.row_factory=sqlite3.Row
        try:
            with c:yield c
        finally:c.close()

    def record(self,message):
        sender=message.from_user
        body=message.text or message.caption or ('شماره تماس: '+message.contact.phone_number if message.contact else '['+str(message.content_type)+']')
        with self.connect() as c:
            c.execute('''INSERT INTO customer_inbox(chat_id,message_id,name,username,body,kind,created_at)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(chat_id,message_id) DO UPDATE SET
                body=excluded.body,kind=excluded.kind,name=excluded.name,username=excluded.username''',
                (message.chat.id,message.message_id,sender.full_name,sender.username,body[:12000],str(message.content_type),int(message.date.timestamp())))

    def threads(self,page=0,targets=None):
        if targets is not None and not targets:
            return [],0
        clause=' WHERE chat_id IN ('+','.join('?' for _ in targets)+')' if targets is not None else ''
        params=tuple(targets or [])
        with self.connect() as c:
            count=c.execute('SELECT count(DISTINCT chat_id) FROM customer_inbox'+clause,params).fetchone()[0]
            rows=c.execute('''SELECT * FROM customer_inbox WHERE id IN
                (SELECT max(id) FROM customer_inbox'''+clause+''' GROUP BY chat_id)
                ORDER BY id DESC LIMIT 8 OFFSET ?''',params+(max(0,page)*8,)).fetchall()
        return [dict(r) for r in rows],count

    def messages(self,chat_id,page=0):
        history="""WITH history AS (
            SELECT *, 'incoming' AS direction FROM customer_inbox WHERE chat_id=?
            UNION ALL
            SELECT -r.message_id,r.target,r.remote_id,
                (SELECT name FROM customer_inbox WHERE chat_id=r.target ORDER BY id DESC LIMIT 1),
                (SELECT username FROM customer_inbox WHERE chat_id=r.target ORDER BY id DESC LIMIT 1),
                r.body,'outgoing',r.created_at,'outgoing'
            FROM inbox_replies r WHERE r.target=? AND r.state='sent' AND r.body IS NOT NULL
        ) """
        with self.connect() as c:
            rows=c.execute(history+'SELECT * FROM history ORDER BY created_at DESC,id DESC LIMIT 5 OFFSET ?',
                           (chat_id,chat_id,max(0,page)*5)).fetchall()
            count=c.execute(history+'SELECT count(*) FROM history',(chat_id,chat_id)).fetchone()[0]
        return [dict(r) for r in rows],count

    def claim_reply(self,admin_id,message_id,target):
        with self.connect() as c:
            if not c.execute('SELECT 1 FROM customer_inbox WHERE chat_id=?',(target,)).fetchone():
                raise ValueError('گفت‌وگوی مشتری یافت نشد.')
            return c.execute('INSERT OR IGNORE INTO inbox_replies(admin_id,message_id,target,state,remote_id,created_at) VALUES(?,?,?,?,NULL,?)',
                (admin_id,message_id,target,'sending',int(time.time()))).rowcount==1

    def reply_result(self,admin_id,message_id,state,remote_id=None,body=None):
        with self.connect() as c:
            c.execute('UPDATE inbox_replies SET state=?,remote_id=?,body=? WHERE admin_id=? AND message_id=?',
                      (state,remote_id,body,admin_id,message_id))


class InboxCaptureMiddleware(BaseMiddleware):
    async def __call__(self,handler,event,data):
        from bot.services.customer_chat_menu import panel_anchor
        if panel_anchor.get() is None and event.chat.type=='private' and event.from_user and not event.from_user.is_bot:
            from bot.config import get_settings
            if event.from_user.id not in get_settings().ADMIN_TELEGRAM_IDS:
                import asyncio
                await asyncio.to_thread(InboxStore().record,event)
        return await handler(event,data)


async def inbox_targets(actor):
    """None means verified admin; staff can access only canonical first-touch referrals."""
    from bot.handlers.role_panels import _is_admin,_is_staff
    if _is_admin(actor):
        return None
    if not _is_staff(actor):
        return []
    async with AsyncSessionLocal() as session:
        # Recheck staff status from the database, not just an old FSM or callback.
        active=await session.scalar(select(User).where(User.telegram_id==actor.telegram_id,User.is_active.is_(True),User.role.in_(('seller','branch_manager'))))
        if not active:
            return []
        return list((await session.scalars(select(User.telegram_id).where(User.role=='customer',User.referrer_telegram_id==actor.telegram_id))).all())

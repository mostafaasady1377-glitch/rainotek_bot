import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from bot.services.divar_campaigns import CampaignStore, PACK
from divar_campaign.engine import inventory
from bot.handlers import divar_campaigns as handlers


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=CampaignStore(Path(self.tmp.name)/'review.db')
        self.store.sync()
        self.row=self.store.list()[0]
        self.p=next(p for p in inventory('rainotek.db') if p['id']==self.row['ad']['product_id'])
        self.mock=patch('bot.services.divar_campaigns.inventory',return_value=[self.p])
        self.mock.start()
        self.addCleanup(self.mock.stop)
        self.addCleanup(self.tmp.cleanup)

    def approve(self):
        return self.store.approve(self.row['number'],self.row['digest'][:16],7495565146)

    def test_exact_approval_moves_once_to_local_shop_and_survives_restart(self):
        self.assertTrue(self.approve())
        self.assertFalse(self.approve())
        again=CampaignStore(self.store.path)
        self.assertEqual(len(again.list(shop=True)),1)
        with again.connect() as c:
            q=c.execute('SELECT * FROM shop_queue').fetchone()
            self.assertEqual(q['state'],'blocked_pending_checks')
            self.assertIsNone(q['remote_id'])
            self.assertEqual(c.execute('SELECT count(*) FROM review_events').fetchone()[0],1)

    def test_old_version_or_changed_stock_cannot_approve(self):
        with self.assertRaises(ValueError):
            self.store.approve(self.row['number'],'0'*16,1)
        with patch('bot.services.divar_campaigns.inventory',return_value=[self.p|{'available':0}]):
            with self.assertRaises(ValueError):
                self.approve()
        self.assertEqual(self.store.list(shop=True),[])

    def test_revocation_removes_shop_entry(self):
        self.approve()
        self.store.revoke(self.row['number'],7495565146)
        self.assertEqual(self.store.list(shop=True),[])

    def test_changed_pack_invalidates_previous_approval(self):
        self.approve()
        manifest=json.loads((PACK/'manifest.json').read_text(encoding='utf8'))
        manifest['ads'][0]['title']='Changed title'
        local=Path(self.tmp.name)/'pack'
        local.mkdir()
        (local/'manifest.json').write_text(json.dumps(manifest),encoding='utf8')
        self.store.pack=local
        self.store.sync()
        self.assertEqual(self.store.list(shop=True),[])
        self.assertEqual(self.store.get(self.row['number'])['state'],'draft')


class HandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.patch=patch('bot.handlers.role_panels.get_settings',return_value=SimpleNamespace(ADMIN_TELEGRAM_IDS=[7495565146]))
        self.patch.start()
        self.addCleanup(self.patch.stop)

    async def test_customer_forged_callback_denied(self):
        cb=SimpleNamespace(data='dc:approve:1:'+'a'*16,answer=AsyncMock(),message=SimpleNamespace(answer=AsyncMock()))
        with patch.object(handlers,'load_store',AsyncMock()) as load:
            await handlers.action(cb,SimpleNamespace(role='customer',telegram_id=7,is_active=True))
            load.assert_not_awaited()
        self.assertTrue(cb.answer.await_args.kwargs['show_alert'])

    def test_scope_is_branch_limited_and_disabled_staff_denied(self):
        user=SimpleNamespace(role='seller',telegram_id=7,is_active=True,managed_branch_id=2)
        self.assertTrue(handlers.can_view(user,{'branch_id':2}))
        self.assertFalse(handlers.can_view(user,{'branch_id':3}))
        user.is_active=False
        self.assertFalse(handlers.can_view(user))

    async def test_seller_cannot_approve_even_own_branch(self):
        store=SimpleNamespace(get=lambda n:{'ad':{'branch_id':2}})
        cb=SimpleNamespace(data='dc:approve:1:'+'a'*16,answer=AsyncMock(),message=SimpleNamespace(answer=AsyncMock()))
        user=SimpleNamespace(role='seller',telegram_id=7,is_active=True,managed_branch_id=2)
        with patch.object(handlers,'load_store',AsyncMock(return_value=store)):
            await handlers.action(cb,user)
        self.assertIn('مالک/ادمین',cb.message.answer.await_args.args[0])

    async def test_entry_clears_product_edit_state_and_lists(self):
        from aiogram import Bot, Dispatcher
        from aiogram.fsm.storage.memory import MemoryStorage
        from aiogram.types import Update
        from aiogram.dispatcher.middlewares.base import BaseMiddleware
        class Inject(BaseMiddleware):
            async def __call__(self,handler,event,data):
                data['current_user']=SimpleNamespace(role='admin',telegram_id=7495565146,is_active=True)
                return await handler(event,data)
        # Use a fresh router so the real application router remains attachable.
        from aiogram import Router,F
        r=Router()
        r.message.register(handlers.entry,F.text==handlers.ENTRY)
        dp=Dispatcher(storage=MemoryStorage())
        dp.message.middleware(Inject())
        dp.include_router(r)
        bot=Bot('123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi')
        state=dp.fsm.get_context(bot=bot,chat_id=7495565146,user_id=7495565146)
        await state.set_state('PanelState:product_value')
        upd=Update.model_validate({'update_id':1,'message':{'message_id':1,'date':1,
             'chat':{'id':7495565146,'type':'private'},'from':{'id':7495565146,'is_bot':False,'first_name':'Owner'},'text':handlers.ENTRY}})
        with patch.object(handlers,'show_personal',AsyncMock()) as show:
            await dp.feed_update(bot,upd)
            show.assert_awaited_once()
        self.assertIsNone(await state.get_state())
        await bot.session.close()

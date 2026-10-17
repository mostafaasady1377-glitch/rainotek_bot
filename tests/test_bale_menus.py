"""Exercise every visible role-menu entry through actual routers and Bale transport.

Database and HTTP are isolated; this checks dispatch/rendering, not native client UI.
"""
import json
import sys
import unittest
from contextlib import ExitStack
from datetime import datetime
from unittest.mock import patch

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.types import Update
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from bot.config import Settings
from bot.services.bale_transport import PlatformSession, normalize_bale_payload
from bot.services.platform_identity import bale_identity
from database.models import Base, User, Branch, LaptopBrand, Laptop, BranchInventory


class BaleRoleMenuTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_visible_role_entries_dispatch_and_render(self):
        import main as application
        from bot.handlers.role_panels import customer_menu, _staff_menu, _manager_menu, _admin_menu
        engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        transport = PlatformSession('bale', 'unused', 'unused')
        bot = Bot('123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi', session=transport, default=DefaultBotProperties(parse_mode='HTML'))
        dp = Dispatcher()
        from bot.middlewares.user_context import UserContextMiddleware
        dp.message.middleware(UserContextMiddleware())
        dp.callback_query.middleware(UserContextMiddleware())
        for name, router in vars(application).items():
            if name == 'router' or name.endswith('_router'):
                dp.include_router(router)
        cfg = Settings(ADMIN_TELEGRAM_IDS=[bale_identity(10)], AI_ADMIN_BOT_TOKEN='', AI_OWNER_TELEGRAM_ID=0, GOOGLE_SHEET_BRIDGE_URL='')
        sent = []
        sequence = 1

        async def http_request(session, current_bot, method, timeout=None):
            nonlocal sequence
            sent.append(method)
            kind = method.__api_method__
            if kind == 'getMe':
                result = {'id':123456,'is_bot':True,'first_name':'Bot','username':'Rinotek_bot'}
            elif kind in {'answerCallbackQuery','deleteMessage'}:
                result = True
            else:
                sequence += 1
                values = method.model_dump(warnings=False)
                result = {'message_id':sequence,'date':1,'chat':{'id':values.get('chat_id',10),'type':'private'},'text':values.get('text','')}
            return session.check_response(current_bot, method, 200, json.dumps({'ok':True,'result':result}, ensure_ascii=False)).result

        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            actors = []
            async with factory() as session:
                branch = Branch(name='میرداماد',code='BR-MIRDAMAD',is_active=True)
                brand = LaptopBrand(name='Dell')
                session.add_all([branch,brand]); await session.flush()
                laptop = Laptop(brand_id=brand.id,model='Test',price=30_000_000,status='active')
                session.add(laptop); await session.flush()
                session.add(BranchInventory(laptop_id=laptop.id,branch_id=branch.id,quantity=2,reserved_count=0))
                for raw_id, role in ((7,'customer'),(8,'seller'),(9,'branch_manager'),(10,'admin')):
                    user = User(telegram_id=bale_identity(raw_id),username=f'user{raw_id}test',full_name='Test',role=role,view_panel=role,is_active=True,phone_number=f'0912000000{raw_id % 10}',managed_branch_id=branch.id if role!='customer' else None,product_edit_allowed=True)
                    session.add(user); actors.append((raw_id,user))
                await session.commit()
            with ExitStack() as stack:
                for name,module in list(sys.modules.items()):
                    if not module or not name.startswith(('bot.','database.')):
                        continue
                    if hasattr(module,'AsyncSessionLocal'):
                        stack.enter_context(patch.object(module,'AsyncSessionLocal',factory))
                    if hasattr(module,'get_settings'):
                        stack.enter_context(patch.object(module,'get_settings',return_value=cfg))
                stack.enter_context(patch.object(AiohttpSession,'make_request',http_request))
                for raw_id,user in actors:
                    menu = {'customer':customer_menu,'seller':_staff_menu,'branch_manager':_manager_menu,'admin':lambda _: _admin_menu()}[user.role](user)
                    for row in menu.keyboard:
                        for button in row:
                            with self.subTest(role=user.role,button=button.text):
                                state = dp.fsm.get_context(bot=bot,chat_id=user.telegram_id,user_id=user.telegram_id)
                                await state.clear()
                                before = len(sent)
                                update = Update.model_validate(normalize_bale_payload({'update_id':sequence,'message':{'message_id':sequence,'date':1,'chat':{'id':raw_id,'type':'private'},'from':{'id':raw_id,'is_bot':False,'first_name':'Test','username':user.username},'text':button.text}}),context={'bot':bot})
                                result = await dp.feed_update(bot,update)
                                self.assertIsNot(result,UNHANDLED)
                                self.assertGreater(len(sent),before,'Visible menu button produced no response')
        finally:
            await dp.storage.close()
            await bot.session.close()
            await engine.dispose()

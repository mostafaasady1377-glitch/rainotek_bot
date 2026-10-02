import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from database.models import Base, SupportRequest
from bot.keyboards.reply_menus import main_menu_kb
from bot.services.support_service import open_support_request, staff_support_queue


class SupportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_repeat_click_reuses_request_and_menu_placement(self):
        async with self.factory() as session:
            first = await open_support_request(session, 123)
            second = await open_support_request(session, 123)
            self.assertEqual(first.id, second.id)
            self.assertEqual(await session.scalar(select(func.count(SupportRequest.id))), 1)
        rows = main_menu_kb().keyboard
        self.assertEqual([b.text for b in rows[-2]], ['💬 کارشناس و پشتیبانی آنلاین'])
        from bot.keyboards.reply_menus import AI_MENU_LABEL
        self.assertEqual([b.text for b in rows[-1]], [AI_MENU_LABEL])

    async def test_staff_scope_and_customer_denial(self):
        async with self.factory() as session:
            session.add_all([
                SupportRequest(customer_telegram_id=1, branch_id=10),
                SupportRequest(customer_telegram_id=2, branch_id=20),
                SupportRequest(customer_telegram_id=3, assigned_user_id=7),
            ])
            await session.commit()
            manager = SimpleNamespace(is_active=True, role='branch_manager', managed_branch_id=10)
            self.assertEqual(len(await staff_support_queue(session, manager)), 2)
            agent = SimpleNamespace(is_active=True, role='seller', id=7)
            self.assertEqual(len(await staff_support_queue(session, agent)), 1)
            with self.assertRaises(PermissionError):
                await staff_support_queue(session, SimpleNamespace(is_active=True, role='customer'))
            manager.is_active = False
            with self.assertRaises(PermissionError):
                await staff_support_queue(session, manager)

    async def test_button_clears_search_and_returns_menu(self):
        from bot.handlers.online_support import open_online_support
        message = SimpleNamespace(from_user=SimpleNamespace(id=456), answer=AsyncMock())
        state = SimpleNamespace(clear=AsyncMock())
        with patch('bot.handlers.online_support.AsyncSessionLocal', self.factory):
            await open_online_support(message, state)
        state.clear.assert_awaited_once()
        self.assertIn('فعال‌سازی پنل', message.answer.call_args.args[0])
        self.assertIsNotNone(message.answer.call_args.kwargs['reply_markup'])

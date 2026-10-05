import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from database.models import Base, User, Branch, Laptop, LaptopBrand, BranchInventory, BotInteraction
from bot.handlers.dashboard_cards import crm_data, branch_data, dashboard_callback


class DashboardCardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from bot.config import Settings
        admin_patch = patch('bot.handlers.role_panels.get_settings', return_value=Settings(ADMIN_TELEGRAM_IDS=[9]))
        admin_patch.start()
        self.addCleanup(admin_patch.stop)
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.factory() as session:
            session.add_all([User(telegram_id=1, role='customer', full_name='کاربر قدیمی', first_seen_at=datetime.utcnow()-timedelta(days=90)), User(telegram_id=2, role='customer', full_name='کاربر جدید', first_seen_at=datetime.utcnow()), Branch(id=1, name='شعبه آزمایشی', code='T'), LaptopBrand(id=1, name='Test')])
            await session.flush()
            session.add(Laptop(id=1, brand_id=1, model='test', status='active'))
            session.add_all([BotInteraction(telegram_id=1, action='product'), BotInteraction(telegram_id=2, action='branch')])
            await session.flush()
            session.add(BranchInventory(branch_id=1, laptop_id=1, quantity=5, reserved_count=2))
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_crm_includes_old_and_new_visitors(self):
        async with self.factory() as session:
            for period in ('daily', 'weekly', 'monthly'):
                text, keyboard = await crm_data(session, User(telegram_id=9, role='admin'), period, 0)
                self.assertIn('کاربران جدید: 1', text)
                self.assertIn('تعداد کاربران: 2', text)
                labels = [b.text for row in keyboard.inline_keyboard for b in row]
                self.assertIn('👤 کاربر قدیمی', labels)
                self.assertIn('👤 کاربر جدید', labels)
                actions = {b.text: b.callback_data for row in keyboard.inline_keyboard for b in row}
                self.assertEqual(actions['👤 کاربران جدید'], 'nav:report:new:daily:0')
                self.assertEqual(actions['📈 گزارش ورودی‌ها'], 'nav:report:visits:daily:0')
                self.assertTrue(all(b.callback_data.startswith('dash:crm:') for b in keyboard.inline_keyboard[0]))

    async def test_branch_stock_reservations_and_no_fake_sales(self):
        async with self.factory() as session:
            text, keyboard = await branch_data(session, 1, 'daily')
        self.assertIn('موجودی فعلی: 5', text)
        self.assertIn('رزرو فعلی: 2', text)
        self.assertIn('قابل فروش: 3', text)
        self.assertIn('درخواست خرید معادل فروش قطعی نیست', text)

    async def test_missing_branch_rejected(self):
        async with self.factory() as session:
            with self.assertRaises(ValueError):
                await branch_data(session, 99, 'daily')

    async def test_non_admin_cannot_use_forged_dashboard(self):
        for role in ('customer', 'seller', 'branch_manager'):
            callback = SimpleNamespace(data='dash:branch:1:daily', answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()))
            await dashboard_callback(callback, User(telegram_id=1, role=role))
            callback.message.answer.assert_not_awaited()

    async def test_invalid_callback_rejected(self):
        callback = SimpleNamespace(data='dash:crm:-1:unknown', answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()))
        await dashboard_callback(callback, User(telegram_id=9, role='admin'))
        callback.message.answer.assert_not_awaited()

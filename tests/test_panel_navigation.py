import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from database.models import Base, User, BotInteraction, LaptopBrand, Laptop, Branch, BranchInventory
from bot.handlers import panel_navigation as nav
from bot.handlers.role_panels import edit_brand_products
from bot.services.sales_contact import telegram_chat_url, sales_contact, sales_expert, expert_chat_keyboard, contact_text


class PanelNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from bot.config import Settings
        admin_patch = patch('bot.handlers.role_panels.get_settings', return_value=Settings(ADMIN_TELEGRAM_IDS=[90]))
        admin_patch.start()
        self.addCleanup(admin_patch.stop)
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.factory() as session:
            session.add_all([User(id=1, telegram_id=10, full_name='مشتری من', role='customer', referrer_telegram_id=20, first_seen_at=datetime.utcnow()), User(id=2, telegram_id=11, full_name='مشتری دیگر', role='customer', referrer_telegram_id=30, first_seen_at=datetime.utcnow()), User(id=3, telegram_id=20, full_name='کارشناس من', role='seller', username='expert20', phone_number='09120000020')])
            session.add_all([BotInteraction(telegram_id=10, action='product'), BotInteraction(telegram_id=11, action='branch')])
            session.add(LaptopBrand(id=1, name='Dell'))
            session.add(Branch(id=1, code='test', name='test'))
            await session.flush()
            session.add_all([Laptop(id=1, brand_id=1, model='Latitude', cpu='i5', ram='8GB', storage='256', status='active'), Laptop(id=2, brand_id=1, model='Latitude', cpu='i7', ram='16GB', storage='512', status='active')])
            await session.flush()
            session.add_all([BranchInventory(laptop_id=1, branch_id=1, quantity=1, reserved_count=0), BranchInventory(laptop_id=2, branch_id=1, quantity=1, reserved_count=1)])
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_reports_only_show_own_customers(self):
        async with self.factory() as session:
            for period in ('daily', 'weekly', 'monthly'):
                for kind in ('new', 'visits'):
                    text, keyboard = await nav.report_data(session, User(telegram_id=20, role='seller'), kind, period, 0)
                    labels = [b.text for row in keyboard.inline_keyboard for b in row]
                    self.assertIn('👤 مشتری من', labels)
                    self.assertNotIn('👤 مشتری دیگر', labels)
                    self.assertIn('تعامل‌های ثبت‌شده در بازه: 1', text)
            _, keyboard = await nav.report_data(session, User(telegram_id=90, role='admin'), 'new', 'daily', 0)
            self.assertIn('👤 مشتری دیگر', [b.text for row in keyboard.inline_keyboard for b in row])

    async def test_forged_customer_detail_is_blocked(self):
        callback = SimpleNamespace(data='nav:customer:2:new:daily:0', answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()))
        with patch.object(nav, 'AsyncSessionLocal', self.factory):
            await nav.customer_detail(callback, User(telegram_id=20, role='seller'))
        callback.message.answer.assert_not_awaited()
        self.assertTrue(callback.answer.await_args.kwargs['show_alert'])

    async def test_customer_detail_has_chat_and_local_dates(self):
        callback = SimpleNamespace(data='nav:customer:1:new:daily:0', answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()))
        with patch.object(nav, 'AsyncSessionLocal', self.factory):
            await nav.customer_detail(callback, User(telegram_id=20, role='seller'))
        text = callback.message.answer.await_args.args[0]
        self.assertIn('مشتری من', text)
        self.assertIn('مدل و مشخصات: 1', text)
        self.assertEqual(callback.message.answer.await_args.kwargs['reply_markup'].inline_keyboard[0][0].url, 'tg://user?id=10')

    async def test_bad_report_callback_is_rejected(self):
        callback = SimpleNamespace(data='nav:report:new:unknown:-1', answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()))
        await nav.report_page(callback, SimpleNamespace(), User(telegram_id=20, role='seller'))
        callback.message.answer.assert_not_awaited()

    async def test_customer_cannot_open_reports_or_editor(self):
        callback = SimpleNamespace(data='nav:report:new:daily:0', answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()))
        await nav.report_page(callback, SimpleNamespace(), User(telegram_id=10, role='customer'))
        callback.message.answer.assert_not_awaited()

    async def test_configuration_stock_filter_excludes_reserved(self):
        callback = SimpleNamespace(data='panel:editmodel:1:0:stock', answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()))
        with patch.object(nav, 'AsyncSessionLocal', self.factory):
            await nav.edit_configurations(callback, SimpleNamespace(clear=AsyncMock()), User(telegram_id=20, role='seller'))
        keyboard = callback.message.answer.await_args.kwargs['reply_markup']
        payloads = [b.callback_data for row in keyboard.inline_keyboard for b in row]
        self.assertIn('panel:edit:1', payloads)
        self.assertNotIn('panel:edit:2', payloads)

    async def test_brand_groups_configurations_under_one_model(self):
        callback = SimpleNamespace(data='panel:editbrand:1:0', answer=AsyncMock(), message=SimpleNamespace(edit_text=AsyncMock()))
        with patch('bot.handlers.role_panels.AsyncSessionLocal', self.factory):
            await edit_brand_products(callback, User(telegram_id=20, role='seller'))
        keyboard = callback.message.edit_text.await_args.kwargs['reply_markup']
        model_buttons = [b for row in keyboard.inline_keyboard for b in row if (b.callback_data or '').startswith('panel:editmodel:')]
        self.assertEqual(len(model_buttons), 1)

    def test_chat_url_username_and_numeric_fallback(self):
        self.assertEqual(telegram_chat_url(User(telegram_id=20, username='expert20')), 'https://t.me/expert20')
        self.assertEqual(telegram_chat_url(User(telegram_id=20, username='bad/url')), 'tg://user?id=20')

    def test_customer_chat_keyboard_uses_only_assigned_expert(self):
        token = sales_contact.set('09120000020')
        expert_token = sales_expert.set(User(telegram_id=20, role='seller', username='expert20', full_name='کارشناس من'))
        try:
            self.assertEqual(expert_chat_keyboard().inline_keyboard[0][0].url, 'https://t.me/expert20')
            self.assertIn('https://t.me/expert20', contact_text())
            self.assertNotIn('expert30', contact_text())
        finally:
            sales_expert.reset(expert_token)
            sales_contact.reset(token)

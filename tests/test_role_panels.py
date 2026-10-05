import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.handlers.role_panels import (
    _is_admin, _is_staff, admin_entry, assign_staff_role,
    _referrer_id_from_payload, entry_menu_kb, receive_customer_contact, staff_entry,
    staff_referral_link, _short_referrer_user_id,
    start_role_selection,
    _admin_menu, admin_role_selection, selected_customer_panel,
)
from database.models import Base, User


class RolePanelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from bot.config import Settings
        self.settings_patch = patch('bot.handlers.role_panels.get_settings', return_value=Settings(ADMIN_TELEGRAM_IDS=[4, 9]))
        self.settings_patch.start()
        self.addCleanup(self.settings_patch.stop)
    def test_admin_layout_moves_reports_inside_crm(self):
        rows = [[b.text for b in row] for row in _admin_menu().keyboard]
        buttons = [text for row in rows for text in row]
        self.assertNotIn('👤 کاربران جدید', buttons)
        self.assertNotIn('📈 گزارش ورودی‌ها', buttons)
        self.assertNotIn('🔐 دسترسی ویرایش پرسنل', buttons)
        self.assertEqual(rows[2], ['✏️ ویرایش محصول', '💻 کاتالوگ و مشخصات'])
        self.assertEqual(rows[-1], ['🔙 انتخاب پنل'])

    async def test_staff_access_contains_edit_permission_submenu(self):
        from bot.handlers.role_panels import staff_access_start
        message = SimpleNamespace(answer=AsyncMock())
        state = SimpleNamespace(set_state=AsyncMock())
        with patch('bot.handlers.management_dashboard._send_staff', AsyncMock()):
            await staff_access_start(message, state, User(role='admin', telegram_id=4))
        keyboard = message.answer.await_args.kwargs['reply_markup']
        self.assertIn('panel:permissions', [b.callback_data for row in keyboard.inline_keyboard for b in row])

    async def test_admin_panel_selector_and_customer_panel(self):
        admin = User(telegram_id=4, role='admin', is_active=True, phone_number='09120000000', view_panel='admin')
        message = SimpleNamespace(answer=AsyncMock())
        state = SimpleNamespace(clear=AsyncMock())
        await admin_role_selection(message, state, admin)
        self.assertEqual(len(message.answer.await_args.kwargs['reply_markup'].keyboard), 4)
        await selected_customer_panel(message, state, admin)
        self.assertIn('منوی مشتری', message.answer.await_args.args[0])
        self.assertNotIn('دسترسی پرسنل', str(message.answer.await_args.kwargs['reply_markup']))
        self.assertEqual(admin.role, 'admin')

    def test_entry_menu_has_three_distinct_choices(self):
        self.assertEqual([row[0].text for row in entry_menu_kb(User(telegram_id=4, role='admin')).keyboard], [
            'ورود مشتریان', 'پنل مدیر شعبه', 'پنل کارشناسان فروش', 'پنل مدیریت',
        ])

    def test_staff_permission_does_not_include_customer(self):
        self.assertFalse(_is_staff(User(telegram_id=1, role='customer')))
        self.assertTrue(_is_staff(User(telegram_id=2, role='seller')))
        self.assertTrue(_is_staff(User(telegram_id=3, role='branch_manager')))
        self.assertTrue(_is_admin(User(telegram_id=4, role='admin')))

    def test_short_rino_referral_payload(self):
        self.assertEqual(_referrer_id_from_payload('r123'), 123)
        self.assertEqual(_referrer_id_from_payload('rino_123'), 123)
        self.assertEqual(_referrer_id_from_payload('ref_123'), 123)
        self.assertEqual(_referrer_id_from_payload('rino_bad'), 0)
        for value in ('r-1', 'r0', 'r+12', 'r۱۲', 'r123x', 'r' + '9' * 20):
            self.assertEqual(_referrer_id_from_payload(value), 0)

    async def test_staff_gets_short_branded_referral_link(self):
        message = SimpleNamespace(
            bot=SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(username='rainotek_bot'))),
            answer=AsyncMock(),
        )
        with patch('bot.handlers.role_panels.get_referral_code', AsyncMock(return_value='R1')):
            await staff_referral_link(message, User(id=7, telegram_id=123, role='seller'))
        text = message.answer.await_args.args[0]
        self.assertEqual(text, '🔗 لینک راینو:\nhttps://t.me/rainotek_bot?start=R1')

    def test_short_code_is_separate_from_legacy_telegram_id(self):
        self.assertEqual(_short_referrer_user_id('r_7'), 7)
        for invalid in ('r7417661889', 'r_0', 'r_-1', 'r_۷', 'r_abc'):
            self.assertEqual(_short_referrer_user_id(invalid), 0)

    async def test_rino_link_shows_only_assigned_expert_phone(self):
        engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            customer = User(telegram_id=10, role='customer')
            session.add_all([
                customer,
                User(telegram_id=20, role='seller', phone_number='09120000020'),
                User(telegram_id=30, role='seller', phone_number='09120000030'),
            ])
            await session.commit()
        message = SimpleNamespace(text='/start rino_20', answer=AsyncMock())
        state = SimpleNamespace(clear=AsyncMock(), set_state=AsyncMock())
        try:
            with patch('bot.handlers.role_panels.AsyncSessionLocal', factory):
                await start_role_selection(message, state, customer)
            rendered = message.answer.await_args_list[-2].args[0]
            self.assertIn('09120000020', rendered)
            self.assertNotIn('09120000030', rendered)
            async with factory() as session:
                saved = await session.scalar(select(User).where(User.telegram_id == 10))
                self.assertEqual(saved.referrer_telegram_id, 20)
            message.text = '/start r30'
            with patch('bot.handlers.role_panels.AsyncSessionLocal', factory):
                await start_role_selection(message, state, customer)
            self.assertIn('09120000020', message.answer.await_args_list[-2].args[0])
            self.assertNotIn('09120000030', message.answer.await_args_list[-2].args[0])
            async with factory() as session:
                new_customer = User(telegram_id=11, role='customer')
                session.add(new_customer)
                expert = await session.scalar(select(User).where(User.telegram_id == 20))
                await session.commit()
            message.text = f'/start r_{expert.id}'
            with patch('bot.handlers.role_panels.AsyncSessionLocal', factory):
                await start_role_selection(message, state, new_customer)
            async with factory() as session:
                saved = await session.get(User, new_customer.id)
                self.assertEqual(saved.referrer_telegram_id, 20)
        finally:
            await engine.dispose()

    async def test_unauthorized_panel_entry_and_role_change(self):
        user = User(telegram_id=1, role='customer')
        message = SimpleNamespace(answer=AsyncMock())
        state = SimpleNamespace(clear=AsyncMock())
        await staff_entry(message, state, user)
        await admin_entry(message, state, user)
        self.assertEqual(message.answer.await_count, 2)
        callback = SimpleNamespace(data='panel:role:1:admin', answer=AsyncMock())
        await assign_staff_role(callback, user)
        callback.answer.assert_awaited_once()

    async def test_forwarded_contact_is_rejected(self):
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            contact=SimpleNamespace(user_id=20, phone_number='09120000000'),
            answer=AsyncMock(),
        )
        await receive_customer_contact(message, SimpleNamespace(), User(id=1, telegram_id=10))
        message.answer.assert_awaited_once()

    async def test_start_resets_an_incomplete_form(self):
        message = SimpleNamespace(answer=AsyncMock())
        state = SimpleNamespace(clear=AsyncMock())
        await start_role_selection(message, state)
        state.clear.assert_awaited_once()
        self.assertEqual(len(message.answer.await_args.kwargs['reply_markup'].keyboard), 3)

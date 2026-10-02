import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.handlers.role_panels import (
    _is_admin, _is_staff, admin_entry, assign_staff_role,
    entry_menu_kb, receive_customer_contact, staff_entry,
    start_role_selection,
)
from database.models import User


class RolePanelTests(unittest.IsolatedAsyncioTestCase):
    def test_entry_menu_has_three_distinct_choices(self):
        self.assertEqual([row[0].text for row in entry_menu_kb().keyboard], [
            'ورود مشتریان', 'ورود کارشناسان و مدیران فروش', 'ورود ادمین',
        ])

    def test_staff_permission_does_not_include_customer(self):
        self.assertFalse(_is_staff(User(telegram_id=1, role='customer')))
        self.assertTrue(_is_staff(User(telegram_id=2, role='seller')))
        self.assertTrue(_is_staff(User(telegram_id=3, role='branch_manager')))
        self.assertTrue(_is_admin(User(telegram_id=4, role='admin')))

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

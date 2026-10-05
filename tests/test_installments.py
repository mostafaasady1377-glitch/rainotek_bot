import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.handlers.online_support import installment_terms, installment_section, INSTALLMENT_SECTIONS
from bot.keyboards.reply_menus import main_menu_kb
from bot.services.sales_contact import sales_contact


class InstallmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_main_menu_and_submenu(self):
        self.assertIn('💳 شرایط خرید اقساطی', [b.text for row in main_menu_kb('customer').keyboard for b in row])
        message = SimpleNamespace(answer=AsyncMock())
        state = SimpleNamespace(clear=AsyncMock())
        await installment_terms(message, state)
        state.clear.assert_awaited_once()
        self.assertEqual(len(message.answer.await_args.kwargs['reply_markup'].inline_keyboard), 3)

    async def test_sections_keep_assigned_expert(self):
        token = sales_contact.set('09120000020')
        try:
            for section in INSTALLMENT_SECTIONS:
                callback = SimpleNamespace(data='installment:' + section, answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()))
                await installment_section(callback)
                self.assertIn('09120000020', callback.message.answer.await_args.args[0])
        finally:
            sales_contact.reset(token)

    async def test_unknown_section_is_rejected(self):
        callback = SimpleNamespace(data='installment:bad', answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()))
        await installment_section(callback)
        callback.message.answer.assert_not_awaited()

import unittest
from types import SimpleNamespace as N
from unittest.mock import AsyncMock, patch
from bot.services.edit_safety import parse_price, new_confirmation, valid_confirmation
from bot.services.edit_permissions import can_manage_editor
from bot.handlers.role_panels import _can_edit, _is_staff, confirm_sheet_edit, save_product_edit, _staff_menu
from database.models import User


class EditSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_permission_saved_and_defaults_to_allowed(self):
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from database.models import Base
        from bot.services.edit_permissions import set_edit_permission
        engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                admin = User(telegram_id=91, role='admin', is_active=True)
                seller = User(telegram_id=92, role='seller', is_active=True)
                session.add_all([admin, seller])
                await session.commit()
                self.assertTrue(seller.product_edit_allowed)
                await set_edit_permission(session, admin, seller, False)
                await session.refresh(seller)
                self.assertFalse(seller.product_edit_allowed)
                await set_edit_permission(session, admin, seller, True)
                await session.refresh(seller)
                self.assertTrue(seller.product_edit_allowed)
        finally:
            await engine.dispose()

    def test_manager_menu_has_permission_control(self):
        manager = User(role='branch_manager')
        self.assertIn('🔐 دسترسی ویرایش پرسنل', [b.text for row in _staff_menu(manager).keyboard for b in row])

    async def test_photo_preview_has_unique_confirm_button(self):
        state = N(get_data=AsyncMock(return_value={'edit_field':'photo', 'laptop_id':1}), update_data=AsyncMock(), set_state=AsyncMock())
        message = N(photo=[N(file_id='photo-id')], answer_photo=AsyncMock(), answer=AsyncMock())
        await save_product_edit(message, state, User(role='seller', telegram_id=1))
        message.answer_photo.assert_awaited_once()
        args = message.answer_photo.await_args.kwargs
        self.assertEqual(args['photo'], 'photo-id')
        self.assertTrue(args['reply_markup'].inline_keyboard[0][0].callback_data.startswith('panel:sheet:confirm:'))

    async def test_image_document_rejected_before_save(self):
        state = N(get_data=AsyncMock(return_value={'edit_field':'photo', 'laptop_id':1}), update_data=AsyncMock())
        message = N(photo=[], document=N(file_id='doc-id'), answer=AsyncMock())
        await save_product_edit(message, state, User(role='seller', telegram_id=1))
        state.update_data.assert_not_awaited()

    async def test_revoked_editor_cannot_confirm(self):
        state = N(clear=AsyncMock())
        callback = N(answer=AsyncMock())
        await confirm_sheet_edit(callback, state, User(role='seller', telegram_id=1, product_edit_allowed=False))
        state.clear.assert_awaited_once()

    def test_price_accepts_exact_numbers(self):
        for value in ('50000000', '۵۰٬۰۰۰٬۰۰۰', '50,000,000'):
            self.assertEqual(parse_price(value), 50000000)

    def test_price_rejects_ambiguous_inputs(self):
        for value in ('-50000000', '50.5 میلیون', '50 تا 60', '0', '1,23', '+10', '9'*16):
            with self.assertRaises(ValueError):
                parse_price(value)

    def test_default_allowed_revoked_and_inactive(self):
        self.assertTrue(_can_edit(User(role='seller', telegram_id=1)))
        self.assertFalse(_can_edit(User(role='seller', telegram_id=1, product_edit_allowed=False)))
        self.assertTrue(_is_staff(User(role='seller', telegram_id=1, product_edit_allowed=False)))
        self.assertFalse(_can_edit(User(role='admin', telegram_id=1, is_active=False)))

    def test_manager_can_only_manage_own_branch_sellers(self):
        manager = User(role='branch_manager', managed_branch_id=1)
        self.assertTrue(can_manage_editor(manager, User(role='seller', managed_branch_id=1)))
        self.assertFalse(can_manage_editor(manager, User(role='seller', managed_branch_id=2)))
        self.assertFalse(can_manage_editor(manager, User(role='admin', managed_branch_id=1)))

    def test_confirmation_expiry_and_unique_token(self):
        first, second = new_confirmation(), new_confirmation()
        self.assertFalse(valid_confirmation(second, first['edit_token']))
        self.assertTrue(valid_confirmation(second, second['edit_token']))
        second['edit_expires'] = 0
        self.assertFalse(valid_confirmation(second, second['edit_token']))

    async def test_old_button_cannot_consume_new_edit(self):
        data = dict(new_confirmation(), sheet_edits=[{}])
        state = N(get_data=AsyncMock(return_value=data), clear=AsyncMock())
        callback = N(data='panel:sheet:confirm:old', answer=AsyncMock(), message=N(answer=AsyncMock()))
        with patch('bot.services.sheet_product_editor.apply_product_edit', AsyncMock()) as write:
            await confirm_sheet_edit(callback, state, User(role='seller', telegram_id=1))
        write.assert_not_awaited()
        state.clear.assert_not_awaited()

    async def test_duplicate_confirm_does_not_repeat_write(self):
        data = dict(new_confirmation(), sheet_edits=[{}])
        state = N(get_data=AsyncMock(side_effect=[data, {}]), clear=AsyncMock())
        callback = N(data='panel:sheet:confirm:' + data['edit_token'], answer=AsyncMock(), message=N(answer=AsyncMock()))
        with patch('bot.handlers.role_panels.AsyncSessionLocal') as sessions, patch('bot.services.sheet_product_editor.apply_product_edit', AsyncMock()) as write:
            sessions.return_value.__aenter__.return_value=N(add=lambda row: None, commit=AsyncMock())
            for _ in range(2):
                await confirm_sheet_edit(callback, state, User(role='seller', telegram_id=1))
            write.assert_awaited_once()
        state.clear.assert_awaited_once()

import unittest
from unittest.mock import patch
from bot.config import Settings
from bot.handlers.role_panels import _is_admin, entry_menu_kb, ADMIN
from database.models import User, Base
from database.users import ensure_user
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker


class ManagementAllowlistTests(unittest.IsolatedAsyncioTestCase):
    async def test_owner_inside_manager_panel_hides_management_choice(self):
        from bot.handlers.role_panels import admin_role_selection, _manager_menu
        from types import SimpleNamespace as N
        from unittest.mock import AsyncMock
        owner = User(telegram_id=1, role='admin', view_panel='branch_manager', managed_branch_id=1)
        with patch('bot.handlers.role_panels.get_settings', return_value=Settings(ADMIN_TELEGRAM_IDS=[1])):
            message = N(answer=AsyncMock())
            await admin_role_selection(message, N(clear=AsyncMock()), owner)
            labels = [b.text for row in message.answer.await_args.kwargs['reply_markup'].keyboard for b in row]
            self.assertNotIn(ADMIN, labels)
            self.assertIn('🔙 بازگشت به پنل مدیریت', labels)
            manager_labels = [b.text for row in _manager_menu(owner).keyboard for b in row]
            self.assertIn('📦 مدل‌ها و موجودی شعبه', manager_labels)
            self.assertNotIn('💻 مدل‌های شعبه', manager_labels)
            self.assertNotIn('📦 موجودی شعبه', manager_labels)

    def test_manager_has_no_back_to_management_and_owner_scope_is_bound(self):
        from bot.handlers.role_panels import _manager_menu
        from bot.handlers.branch_manager_panel import allowed_branch
        with patch('bot.handlers.role_panels.get_settings', return_value=Settings(ADMIN_TELEGRAM_IDS=[1])):
            manager = User(telegram_id=2, role='branch_manager', managed_branch_id=1)
            labels = [b.text for row in _manager_menu(manager).keyboard for b in row]
            self.assertNotIn('🔙 بازگشت به پنل مدیریت', labels)
            owner = User(telegram_id=1, role='admin', view_panel='branch_manager', managed_branch_id=1)
            self.assertTrue(allowed_branch(owner, 1))
            self.assertFalse(allowed_branch(owner, 2))

    def test_only_three_fixed_ids_get_management(self):
        settings = Settings(ADMIN_TELEGRAM_IDS=[7495565146, 6183342455, 5656664370])
        with patch('bot.handlers.role_panels.get_settings', return_value=settings):
            for identity in settings.ADMIN_TELEGRAM_IDS:
                user = User(telegram_id=identity, role='admin', is_active=True)
                self.assertTrue(_is_admin(user))
                self.assertEqual(len(entry_menu_kb(user).keyboard), 4)
            for role in ('admin', 'branch_manager', 'seller', 'customer'):
                user = User(telegram_id=99, role=role, is_active=True)
                self.assertFalse(_is_admin(user))
                self.assertEqual(len(entry_menu_kb(user).keyboard), 3)
                self.assertNotIn(ADMIN, [b.text for row in entry_menu_kb(user).keyboard for b in row])

    async def test_legacy_admin_is_removed_on_entry(self):
        engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                session.add(User(telegram_id=99, role='admin'))
                await session.commit()
                user = await ensure_user(session, 99, 'legacy', 'test', Settings(ADMIN_TELEGRAM_IDS=[1,2,3]))
                self.assertEqual(user.role, 'customer')
        finally:
            await engine.dispose()

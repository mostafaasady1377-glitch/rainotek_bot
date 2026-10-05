import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from database.models import Base, User
from database.users import ensure_user
from bot.config import Settings
from bot.handlers.role_panels import receive_customer_contact, start_role_selection


class VerifiedOnboardingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_username_cannot_grant_staff(self):
        async with self.factory() as session:
            user = await ensure_user(session, 10, 'seller', 'test', settings=Settings(ADMIN_TELEGRAM_IDS=[]))
            self.assertEqual(user.role, 'customer')

    async def test_verified_phone_promotes_only_allowlisted_owner(self):
        async with self.factory() as session:
            users = [User(telegram_id=i, role='customer', is_active=True) for i in (10, 20, 30)]
            session.add_all(users)
            await session.commit()
        state = SimpleNamespace(clear=AsyncMock(), get_data=AsyncMock(return_value={}))
        settings = Settings(ADMIN_PHONE_NUMBERS='09122988936', ADMIN_TELEGRAM_IDS=[10, 30])
        with patch('bot.handlers.role_panels.AsyncSessionLocal', self.factory), patch('bot.handlers.role_panels.get_settings', return_value=settings):
            for user, phone, expected in zip(users, ('+989122988936', '09120000000', '09122988936'), ('admin', 'customer', 'customer')):
                message = SimpleNamespace(from_user=SimpleNamespace(id=user.telegram_id, first_name='test'), contact=SimpleNamespace(user_id=user.telegram_id, phone_number=phone), answer=AsyncMock())
                await receive_customer_contact(message, state, user)
                async with self.factory() as session:
                    saved = await session.get(User, user.id)
                    self.assertEqual(saved.role, expected)
                if expected == 'admin':
                    self.assertIn('مدیریت کل', message.answer.await_args.args[0])
                elif phone == '09120000000':
                    keys = str(message.answer.await_args.kwargs['reply_markup'])
                    self.assertNotIn('دسترسی پرسنل', keys)
                else:
                    self.assertIn('قبلاً', message.answer.await_args.args[0])

    async def test_start_routes_each_existing_role(self):
        settings = Settings(ADMIN_PHONE_NUMBERS='', ADMIN_TELEGRAM_IDS=[4])
        with patch('bot.handlers.role_panels.get_settings', return_value=settings):
            for role, marker in [('customer', 'کاتالوگ'), ('seller', 'کارشناسان'), ('branch_manager', 'مدیر شعبه'), ('admin', 'مدیریت کل')]:
                user = User(telegram_id=4 if role == 'admin' else 1, role=role, phone_number='09120000000', is_active=True, managed_branch_id=1 if role == 'branch_manager' else None)
                message = SimpleNamespace(text='/start', answer=AsyncMock())
                state = SimpleNamespace(clear=AsyncMock())
                await start_role_selection(message, state, user)
                self.assertIn(marker, message.answer.await_args.args[0])

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from database.models import Base, User
from bot.services.sales_contact import sales_contact, resolve_sales_contact, contact_phone
from bot.services.branch_presentation import branch_details
from bot.handlers.role_panels import staff_referral_count


class SalesContactTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.factory() as session:
            session.add_all([
                User(telegram_id=20, role='seller', phone_number='09120000020'),
                User(telegram_id=30, role='seller', phone_number='09120000030'),
                User(telegram_id=1, role='customer', referrer_telegram_id=20),
                User(telegram_id=2, role='customer', referrer_telegram_id=20),
                User(telegram_id=3, role='customer', referrer_telegram_id=30),
            ])
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_referral_uses_own_phone_and_missing_expert_hides_branch(self):
        async with self.factory() as session:
            for expert_id, expected in [(20, '09120000020'), (99, '')]:
                user = SimpleNamespace(role='customer', referrer_telegram_id=expert_id)
                token = sales_contact.set(await resolve_sales_contact(session, user))
                try:
                    rendered = branch_details('شعبه', phone='02112345678')
                    self.assertNotIn('02112345678', rendered)
                    self.assertNotIn('09120000030', rendered)
                    if expected:
                        self.assertIn(expected, rendered)
                finally:
                    sales_contact.reset(token)
        self.assertEqual(contact_phone('02112345678'), '02112345678')

    async def test_parallel_customers_do_not_share_contact(self):
        async def render(phone):
            token = sales_contact.set(phone)
            try:
                await asyncio.sleep(0)
                return contact_phone('branch')
            finally:
                sales_contact.reset(token)
        self.assertEqual(await asyncio.gather(render('20'), render('30')), ['20', '30'])

    async def test_count_is_scoped_to_current_expert(self):
        message = SimpleNamespace(answer=AsyncMock())
        with patch('bot.handlers.role_panels.AsyncSessionLocal', self.factory):
            await staff_referral_count(message, User(telegram_id=20, role='seller'))
        self.assertIn('2 مشتری یکتا', message.answer.await_args.args[0])

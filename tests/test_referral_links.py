import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from database.models import Base, User
from bot.services import referral_links as links
from bot.handlers.role_panels import start_role_selection


class ReferralCodeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.factory() as session:
            self.expert = User(id=100, telegram_id=7417661889, role='seller', phone_number='09120000001')
            self.second = User(id=200, telegram_id=99, role='branch_manager', phone_number='09120000002')
            self.customer = User(id=300, telegram_id=10, role='customer')
            session.add_all([self.expert, self.second, self.customer])
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_codes_are_sequential_and_stable_not_database_ids(self):
        with patch.object(links, 'AsyncSessionLocal', self.factory):
            self.assertEqual(await links.get_referral_code(self.expert), 'R1')
            self.assertEqual(await links.get_referral_code(self.second), 'R2')
            self.assertEqual(await links.get_referral_code(self.expert), 'R1')

    async def test_concurrent_request_does_not_allocate_twice(self):
        with patch.object(links, 'AsyncSessionLocal', self.factory):
            codes = await asyncio.gather(links.get_referral_code(self.expert), links.get_referral_code(self.expert))
        self.assertEqual(codes, ['R1', 'R1'])

    async def test_short_code_tracks_expert_and_preserves_first_touch(self):
        with patch.object(links, 'AsyncSessionLocal', self.factory):
            await links.get_referral_code(self.expert)
            await links.get_referral_code(self.second)
        state = SimpleNamespace(clear=AsyncMock(), set_state=AsyncMock())
        message = SimpleNamespace(text='/start R1', answer=AsyncMock())
        with patch('bot.handlers.role_panels.AsyncSessionLocal', self.factory):
            await start_role_selection(message, state, self.customer)
            self.assertIn('09120000001', message.answer.await_args_list[-2].args[0])
            message.text = '/start R2'
            await start_role_selection(message, state, self.customer)
            self.assertNotIn('09120000002', message.answer.await_args_list[-2].args[0])
        async with self.factory() as session:
            saved = await session.get(User, self.customer.id)
            self.assertEqual(saved.referrer_telegram_id, 7417661889)

    async def test_customer_cannot_get_expert_code(self):
        with patch.object(links, 'AsyncSessionLocal', self.factory):
            with self.assertRaises(ValueError):
                await links.get_referral_code(self.customer)

    def test_code_parsing_does_not_conflict_with_legacy_links(self):
        self.assertEqual(links.referral_code_id('R1'), 1)
        for value in ('r7417661889', 'r_5', 'R0', 'R-1', 'R۱', 'Rabc'):
            self.assertEqual(links.referral_code_id(value), 0)

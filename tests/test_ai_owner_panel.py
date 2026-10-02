import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.handlers import ai_owner_panel, vpn_shop
from database.models import AiFeatureVisit, Base, User, VpnConfig, VpnOrder
from bot.services import ai_feature_visits


class AiOwnerPanelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.settings = SimpleNamespace(AI_OWNER_TELEGRAM_ID=900, AI_ADMIN_BOT_TOKEN='admin-test', ADMIN_TELEGRAM_IDS=[800])

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_owner_only_and_store_admin_not_authorized(self):
        message = SimpleNamespace(from_user=SimpleNamespace(id=800), answer=AsyncMock())
        with patch.object(ai_owner_panel, 'get_settings', return_value=self.settings):
            await ai_owner_panel.owner_start(message)
            self.assertIn('مجاز نیست', message.answer.await_args.args[0])
            message.from_user = SimpleNamespace(id=900)
            await ai_owner_panel.owner_start(message)
            self.assertIn('AI Developer', message.answer.await_args.args[0])
            keyboard = message.answer.await_args.kwargs['reply_markup']
            self.assertEqual(keyboard.inline_keyboard[0][0].callback_data, 'ai:project:rainotek')
        with patch.object(vpn_shop, 'get_settings', return_value=self.settings):
            self.assertEqual(vpn_shop._vpn_admin_ids(), [900])

    async def test_owner_review_delivers_with_customer_bot_not_admin_bot(self):
        async with self.factory() as session:
            session.add(VpnOrder(customer_telegram_id=123, duration_days=30, user_count=1,
                                 base_price_toman=379000, price_toman=435850, status='pending_review'))
            session.add(VpnConfig(duration_days=30, user_count=1, connection_url='https://example.test/i/unique', url_hash='unique'))
            await session.commit()
        admin_bot = SimpleNamespace(send_message=AsyncMock())
        customer_bot = SimpleNamespace(send_message=AsyncMock())
        callback = SimpleNamespace(from_user=SimpleNamespace(id=900), data='ai:review:1:approve',
                                   answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()), bot=admin_bot)
        with patch.object(ai_owner_panel, 'get_settings', return_value=self.settings), patch.object(vpn_shop, 'AsyncSessionLocal', self.factory):
            await ai_owner_panel.owner_review(callback, customer_bot)
        admin_bot.send_message.assert_not_awaited()
        customer_bot.send_message.assert_awaited_once()
        self.assertEqual(customer_bot.send_message.await_args.args[0], 123)
        async with self.factory() as session:
            self.assertEqual((await session.get(VpnOrder, 1)).status, 'delivered')

    async def test_one_link_can_only_be_registered_once(self):
        bot = SimpleNamespace(send_message=AsyncMock())
        with patch.object(vpn_shop, 'AsyncSessionLocal', self.factory):
            first = await vpn_shop._register_config(30, 1, 'https://example.test/i/unique', bot)
            second = await vpn_shop._register_config(30, 1, 'https://example.test/i/unique', bot)
        self.assertEqual(first, ('available', None))
        self.assertEqual(second, ('duplicate', None))

    async def test_crm_shows_shared_contact_ai_visits_and_subscription(self):
        async with self.factory() as session:
            session.add(User(telegram_id=123, first_name='Kian', full_name='Kian Test', username='kian',
                             phone_number='09123456789', role='customer', joined_at=datetime(2026, 10, 2, 10)))
            session.add(VpnOrder(customer_telegram_id=123, duration_days=30, user_count=1,
                                 base_price_toman=379000, price_toman=435850, status='delivered'))
            await session.commit()
        with patch.object(ai_feature_visits, 'AsyncSessionLocal', self.factory):
            await ai_feature_visits.record_ai_visit(123, 'ai_menu')
            await ai_feature_visits.record_ai_visit(123, 'ai_menu')
        async with self.factory() as session:
            self.assertEqual((await session.get(AiFeatureVisit, (123, 'ai_menu'))).interaction_count, 2)
        callback = SimpleNamespace(from_user=SimpleNamespace(id=900), data='ai:crm:ai:0',
                                   answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()))
        with patch.object(ai_owner_panel, 'get_settings', return_value=self.settings), patch.object(ai_owner_panel, 'AsyncSessionLocal', self.factory):
            await ai_owner_panel.owner_crm_list(callback)
            self.assertIn('09123456789', callback.message.answer.await_args.args[0])
            callback.data = 'ai:crm:buyers:0'
            await ai_owner_panel.owner_crm_list(callback)
            self.assertIn('خریداران اشتراک', callback.message.answer.await_args.args[0])
            await ai_owner_panel.owner_dashboard(callback)
            self.assertIn('مشتریان CRM: 1', callback.message.answer.await_args.args[0])
            callback.data = 'ai:crm:person:1'
            await ai_owner_panel.owner_crm_person(callback)
            details = callback.message.answer.await_args.args[0]
            self.assertIn('09123456789', details)
            self.assertIn('@kian', details)
            self.assertIn('ورود به هوش مصنوعی', details)
            self.assertIn('تحویل‌شده', details)
            callback.from_user = SimpleNamespace(id=800)
            callback.message.answer.reset_mock()
            await ai_owner_panel.owner_crm_person(callback)
            callback.message.answer.assert_not_awaited()

    async def test_held_account_requires_owner_and_matching_approved_order(self):
        async with self.factory() as session:
            session.add(VpnOrder(customer_telegram_id=123, duration_days=30, user_count=1,
                                 base_price_toman=379000, price_toman=435850, status='approved'))
            session.add(VpnConfig(duration_days=30, user_count=1,
                                  connection_url='https://example.test/i/held', url_hash='heldhash', status='held'))
            await session.commit()
        bot = SimpleNamespace(send_message=AsyncMock())
        callback = SimpleNamespace(from_user=SimpleNamespace(id=800), data='ai:held:assign:1:1',
                                   answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()))
        with patch.object(ai_owner_panel, 'get_settings', return_value=self.settings), patch.object(ai_owner_panel, 'AsyncSessionLocal', self.factory), patch.object(vpn_shop, 'AsyncSessionLocal', self.factory):
            await ai_owner_panel.owner_assign_held_config(callback, bot)
            bot.send_message.assert_not_awaited()
            callback.from_user = SimpleNamespace(id=900)
            await ai_owner_panel.owner_assign_held_config(callback, bot)
            bot.send_message.assert_awaited_once()
            self.assertEqual(bot.send_message.await_args.args[0], 123)
            async with self.factory() as session:
                self.assertEqual((await session.get(VpnOrder, 1)).status, 'delivered')
                self.assertEqual((await session.get(VpnConfig, 1)).order_id, 1)
            await ai_owner_panel.owner_assign_held_config(callback, bot)
            bot.send_message.assert_awaited_once()

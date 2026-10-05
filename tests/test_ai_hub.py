import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.handlers import ai_hub
from bot.services import ai_feature_visits
from database.models import AiApiInquiry, AiFeatureVisit, Base


class AiHubTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    def test_ai_menu_separates_assistant_and_api_catalog(self):
        callbacks = [b.callback_data for row in ai_hub.AI_MENU.inline_keyboard for b in row]
        self.assertEqual(callbacks, ['ai:assistant', 'vpn:home', 'ai:apis', 'ai:main_menu'])
        self.assertEqual(ai_hub.AI_MENU.inline_keyboard[0][0].text, ai_hub.AI_ASSISTANT_LABEL)
        self.assertEqual(ai_hub.AI_MENU.inline_keyboard[2][0].text, '✨ سرویس‌های هوش مصنوعی')
        self.assertIn('openai', ai_hub.API_PRODUCTS)
        self.assertIn('gemini', ai_hub.API_PRODUCTS)
        self.assertIn('claude', ai_hub.API_PRODUCTS)
        self.assertIn('veo', ai_hub.API_PRODUCTS)

    async def test_api_inquiry_deduplicates_and_never_creates_payment(self):
        callback = SimpleNamespace(
            data='ai:api_request:gemini', from_user=SimpleNamespace(id=123),
            answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()),
        )
        with patch.object(ai_hub, 'AsyncSessionLocal', self.factory):
            await ai_hub.api_request(callback)
            await ai_hub.api_request(callback)
            async with self.factory() as session:
                self.assertEqual(await session.scalar(select(func.count(AiApiInquiry.id))), 1)
                row = await session.scalar(select(AiApiInquiry))
                self.assertEqual((row.customer_telegram_id, row.provider_key, row.status), (123, 'gemini', 'awaiting_quote'))

    async def test_services_menu_opens_and_records_visit(self):
        callback = SimpleNamespace(from_user=SimpleNamespace(id=123), answer=AsyncMock(),
                                   message=SimpleNamespace(answer=AsyncMock()))
        with patch.object(ai_feature_visits, 'AsyncSessionLocal', self.factory):
            await ai_hub.api_categories(callback)
            async with self.factory() as session:
                visit = await session.get(AiFeatureVisit, (123, 'ai_api_catalog'))
                self.assertIsNotNone(visit)
        self.assertIn('سرویس‌های هوش مصنوعی', callback.message.answer.await_args.args[0])
        buttons = [button for row in callback.message.answer.await_args.kwargs['reply_markup'].inline_keyboard for button in row]
        self.assertEqual(buttons[0].callback_data, 'premium:home')
        self.assertEqual([button.callback_data for button in buttons[1:-1]],
                         [f'ai:subscription:{key}' for key in ai_hub.OFFICIAL_SUBSCRIPTIONS])
        self.assertTrue(all(button.text.startswith('✨ ') for button in buttons[1:-1]))
        self.assertEqual(buttons[-1].callback_data, 'ai:home')

    async def test_subscription_shows_official_us_price_without_checkout(self):
        callback = SimpleNamespace(data='ai:subscription:gemini_pro', answer=AsyncMock(),
                                   message=SimpleNamespace(answer=AsyncMock()))
        await ai_hub.official_subscription(callback)
        text = callback.message.answer.await_args.args[0]
        self.assertIn('$19.99 / month', text)
        self.assertIn('gemini.google/subscriptions/', text)
        buttons = [button.callback_data for row in callback.message.answer.await_args.kwargs['reply_markup'].inline_keyboard for button in row]
        self.assertEqual(buttons, ['premium:home', 'ai:apis'])
        self.assertIn('پرداخت ریالی', text)

    async def test_main_menu_return_restores_reply_keyboard(self):
        callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()))
        state = SimpleNamespace(clear=AsyncMock())
        user = SimpleNamespace(role='admin')
        await ai_hub.ai_main_menu(callback, state, user)
        state.clear.assert_awaited_once()
        self.assertIn('منوی اصلی راینوتک', callback.message.answer.await_args.args[0])
        labels = [button.text for row in callback.message.answer.await_args.kwargs['reply_markup'].keyboard for button in row]
        self.assertIn('🔎 جستجوی هوشمند', labels)
        self.assertNotIn('➕ ورود کالا', labels)

    async def test_guided_advice_uses_current_inventory(self):
        state = SimpleNamespace(get_data=AsyncMock(return_value={'purpose': 'gaming', 'detail': 'بازی سنگین'}), clear=AsyncMock())
        message = SimpleNamespace(text='تا ۵۰ میلیون', answer=AsyncMock())
        with patch.object(ai_hub, 'AsyncSessionLocal', self.factory), patch.object(ai_hub.AISearchService, 'smart_search', new_callable=AsyncMock, return_value=[] ) as search:
            await ai_hub.assistant_recommend(message, state)
        self.assertIn('گیمینگ', search.await_args.args[1])
        self.assertIn('تا ۵۰ میلیون', search.await_args.args[1])
        self.assertIn('مدل منطبقی', message.answer.await_args.args[0])

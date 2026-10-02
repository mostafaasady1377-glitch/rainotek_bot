import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.handlers import premium_shop
from bot.handlers import ai_owner_panel
from bot.services.premium_seed import SOURCE_SNAPSHOT
from database.models import Base, PremiumOrder, PremiumPlan


class PremiumShopTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    def test_exact_ten_percent_markup_and_stale_stock_guard(self):
        self.assertEqual(premium_shop.sale_price(379000), 416900)
        self.assertEqual(premium_shop.sale_price(101), 112)
        plan = PremiumPlan(source_sku='x', category='chat', title='Sample', source_price_toman=379000,
                           stock=1, checked_at=datetime.utcnow() - timedelta(hours=25))
        self.assertFalse(premium_shop.plan_available(plan))
        prices = {sku: premium_shop.sale_price(price) for sku, _, _, price, stock, _ in SOURCE_SNAPSHOT if price and stock}
        self.assertEqual(prices['hami-chatgpt-plus-ready-1m'], 3738900)
        self.assertEqual(prices['hami-gemini-pro-18m'], 878900)
        self.assertEqual(prices['hami-multi-api-20usd'], 1098900)
        self.assertEqual(prices['hami-multi-api-10usd'], 548900)

    async def test_order_reserves_once_and_snapshots_price(self):
        async with self.factory() as session:
            session.add(PremiumPlan(source_sku='gpt-plus', category='chat', title='ChatGPT Plus',
                                    source_price_toman=500000, stock=1, checked_at=datetime.utcnow()))
            await session.commit()
        callback = SimpleNamespace(data='premium:buy:1', from_user=SimpleNamespace(id=123),
                                   answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()))
        state = SimpleNamespace(set_state=AsyncMock(), update_data=AsyncMock())
        settings = SimpleNamespace(VPN_PAYMENT_CARD='1111222233334444', VPN_PAYMENT_HOLDER='Holder')
        with patch.object(premium_shop, 'AsyncSessionLocal', self.factory), patch.object(premium_shop, 'get_settings', return_value=settings):
            await premium_shop.premium_buy(callback, state)
            await premium_shop.premium_buy(callback, state)
            async with self.factory() as session:
                orders = (await session.scalars(select(PremiumOrder))).all()
                self.assertEqual(len(orders), 1)
                self.assertEqual(orders[0].price_toman, 550000)
                self.assertEqual((await session.get(PremiumPlan, 1)).stock, 0)
            await premium_shop.premium_card(SimpleNamespace(data='premium:card:1', from_user=SimpleNamespace(id=124),
                                            answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock())))
            self.assertEqual(callback.message.answer.await_count, 2)

    async def test_owner_only_review_and_rejection_restore_stock_once(self):
        async with self.factory() as session:
            session.add(PremiumPlan(source_sku='claude', category='chat', title='Claude',
                                    source_price_toman=600000, stock=0, checked_at=datetime.utcnow()))
            session.add(PremiumOrder(customer_telegram_id=123, plan_id=1, title_snapshot='Claude',
                                     source_price_toman=600000, price_toman=660000,
                                     status='pending_review', receipt_file_id='file', receipt_kind='photo'))
            await session.commit()
        callback = SimpleNamespace(data='ai:premium:review:1:reject', from_user=SimpleNamespace(id=999),
                                   answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()))
        customer_bot = SimpleNamespace(send_message=AsyncMock())
        with patch.object(ai_owner_panel, 'AsyncSessionLocal', self.factory), patch.object(ai_owner_panel, '_owner', side_effect=lambda user: user.id == 900):
            await ai_owner_panel.owner_premium_review(callback, customer_bot)
            customer_bot.send_message.assert_not_awaited()
            callback.from_user = SimpleNamespace(id=900)
            await ai_owner_panel.owner_premium_review(callback, customer_bot)
            await ai_owner_panel.owner_premium_review(callback, customer_bot)
            async with self.factory() as session:
                self.assertEqual((await session.get(PremiumOrder, 1)).status, 'rejected')
                self.assertEqual((await session.get(PremiumPlan, 1)).stock, 1)
            customer_bot.send_message.assert_awaited_once()

    async def test_customer_cancel_releases_reserved_stock_once(self):
        async with self.factory() as session:
            session.add(PremiumPlan(source_sku='gemini', category='chat', title='Gemini',
                                    source_price_toman=500000, stock=0, checked_at=datetime.utcnow()))
            session.add(PremiumOrder(customer_telegram_id=123, plan_id=1, title_snapshot='Gemini',
                                     source_price_toman=500000, price_toman=550000, status='awaiting_receipt'))
            await session.commit()
        callback = SimpleNamespace(data='premium:cancel:1', from_user=SimpleNamespace(id=123),
                                   answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()))
        state = SimpleNamespace(clear=AsyncMock())
        with patch.object(premium_shop, 'AsyncSessionLocal', self.factory):
            await premium_shop.premium_cancel(callback, state)
            await premium_shop.premium_cancel(callback, state)
            async with self.factory() as session:
                self.assertEqual((await session.get(PremiumOrder, 1)).status, 'cancelled')
                self.assertEqual((await session.get(PremiumPlan, 1)).stock, 1)

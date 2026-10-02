import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.handlers import vpn_shop
from bot.handlers.ai_hub import ai_home_message
from database.models import Base, VpnConfig, VpnOrder


class VpnShopTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.settings = SimpleNamespace(VPN_PAYMENT_CARD='1111222233334444', VPN_PAYMENT_HOLDER='Test Holder', ADMIN_TELEGRAM_IDS=[900], AI_OWNER_TELEGRAM_ID=900)

    async def asyncTearDown(self):
        await self.engine.dispose()

    def test_every_screenshot_price_has_exact_15_percent_markup(self):
        self.assertEqual(set(vpn_shop.SOURCE_PRICES), {30, 60, 90, 180})
        for duration, plans in vpn_shop.SOURCE_PRICES.items():
            self.assertEqual(set(plans), {1, 2, 3})
            for users, base_price in plans.items():
                self.assertEqual(vpn_shop.sale_price(duration, users) * 100, base_price * 115)
        self.assertEqual(vpn_shop.sale_price(30, 1), 435850)
        self.assertEqual(vpn_shop.sale_price(180, 3), 3563850)

    async def test_ai_menu_links_to_vpn_without_removing_search(self):
        message = SimpleNamespace(answer=AsyncMock(), from_user=SimpleNamespace(id=123))
        with patch('bot.handlers.ai_hub.record_ai_visit', new_callable=AsyncMock):
            await ai_home_message(message, SimpleNamespace(clear=AsyncMock()))
        keyboard = message.answer.await_args.kwargs['reply_markup']
        callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
        self.assertEqual(callbacks, ['ai:assistant', 'vpn:home', 'ai:apis', 'ai:main_menu'])
        home_callbacks = [button.callback_data for row in vpn_shop.HOME.inline_keyboard for button in row]
        self.assertTrue({'vpn:accounts', 'vpn:wallet', 'vpn:durations', 'vpn:trial', 'vpn:support', 'vpn:guide', 'vpn:troubleshoot', 'vpn:locations'} <= set(home_callbacks))

    async def test_payment_receipt_review_and_manual_delivery(self):
        bot = SimpleNamespace(send_photo=AsyncMock(), send_document=AsyncMock(), send_message=AsyncMock())
        answer = AsyncMock()
        customer = SimpleNamespace(id=123)
        callback = SimpleNamespace(data='vpn:create:30:1', from_user=customer, message=SimpleNamespace(answer=answer), answer=AsyncMock(), bot=bot)
        state = SimpleNamespace(set_state=AsyncMock(), update_data=AsyncMock(), get_data=AsyncMock(), clear=AsyncMock())
        with patch.object(vpn_shop, 'AsyncSessionLocal', self.factory), patch.object(vpn_shop, 'get_settings', return_value=self.settings):
            await vpn_shop.vpn_create_order(callback, state)
            await vpn_shop.vpn_create_order(callback, state)
            async with self.factory() as session:
                orders = (await session.scalars(select(VpnOrder))).all()
                self.assertEqual(len(orders), 1)
                order = orders[0]
                order_id = order.id
                self.assertEqual((order.status, order.price_toman), ('awaiting_receipt', 435850))
            callback.data = f'vpn:card:{order_id}'
            callback.from_user = SimpleNamespace(id=124)
            await vpn_shop.vpn_card(callback)
            self.assertEqual(answer.await_count, 2)
            callback.from_user = customer
            await vpn_shop.vpn_card(callback)
            self.assertEqual(answer.await_count, 3)
            card_message = answer.await_args
            self.assertTrue(card_message.kwargs['protect_content'])
            self.assertIn('صاحب کارت: <b>Test Holder</b>', card_message.args[0])
            self.assertIn('1111222233334444', card_message.args[0])
            self.assertEqual(card_message.kwargs['reply_markup'].inline_keyboard[0][0].callback_data, f'vpn:account:{order_id}')
            state.get_data.return_value = {'vpn_order_id': order_id}
            receipt = SimpleNamespace(photo=[SimpleNamespace(file_id='receipt-photo')], document=None, from_user=customer, bot=bot, answer=AsyncMock())
            await vpn_shop.vpn_receipt(receipt, state)
            bot.send_photo.assert_awaited_once()
            self.assertIn('tg://user?id=900', receipt.answer.await_args.args[0])
            self.assertIn('رسید شما در سیستم ثبت شد', receipt.answer.await_args.args[0])
            async with self.factory() as session:
                self.assertEqual((await session.get(VpnOrder, order_id)).status, 'pending_review')
            callback.data = f'vpn:review:{order_id}:approve'
            callback.from_user = customer
            await vpn_shop.vpn_review(callback)
            async with self.factory() as session:
                self.assertEqual((await session.get(VpnOrder, order_id)).status, 'pending_review')
            callback.from_user = SimpleNamespace(id=900)
            await vpn_shop.vpn_review(callback)
            async with self.factory() as session:
                self.assertEqual((await session.get(VpnOrder, order_id)).status, 'approved')
            deliver = SimpleNamespace(from_user=SimpleNamespace(id=900), bot=bot, answer=AsyncMock())
            command = SimpleNamespace(args=f'{order_id} vless://example')
            await vpn_shop.vpn_deliver(deliver, command)
            async with self.factory() as session:
                stored = await session.get(VpnOrder, order_id)
                self.assertEqual(stored.status, 'delivered')
                self.assertEqual(stored.delivery_text, 'vless://example')

    async def test_order_cannot_be_created_without_payment_details(self):
        callback = SimpleNamespace(data='vpn:create:60:2', from_user=SimpleNamespace(id=123), answer=AsyncMock())
        with patch.object(vpn_shop, 'get_settings', return_value=SimpleNamespace(VPN_PAYMENT_CARD='', VPN_PAYMENT_HOLDER='')):
            await vpn_shop.vpn_create_order(callback, SimpleNamespace())
        callback.answer.assert_awaited_once()
        async with self.factory() as session:
            self.assertIsNone(await session.scalar(select(VpnOrder)))

    async def test_final_payment_menu_and_wallet_do_not_create_order(self):
        callback = SimpleNamespace(data='vpn:quote:30:1', answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()))
        await vpn_shop.vpn_quote(callback)
        keyboard = callback.message.answer.await_args.kwargs['reply_markup']
        self.assertEqual([button.text for button in keyboard.inline_keyboard[0]], ['💳 کارت به کارت', '💰 کیف پول'])
        callback.data = 'vpn:wallet_pay:30:1'
        await vpn_shop.vpn_wallet_payment(callback)
        self.assertIn('هنوز فعال نشده', callback.message.answer.await_args.args[0])
        async with self.factory() as session:
            self.assertIsNone(await session.scalar(select(VpnOrder)))

    async def test_only_main_admin_can_open_vpn_order_queue(self):
        message = SimpleNamespace(from_user=SimpleNamespace(id=123), answer=AsyncMock())
        with patch.object(vpn_shop, 'AsyncSessionLocal', self.factory), patch.object(vpn_shop, 'get_settings', return_value=self.settings):
            await vpn_shop.vpn_admin_orders(message)
            self.assertIn('فقط برای ادمین', message.answer.await_args.args[0])
            message.from_user = SimpleNamespace(id=900)
            await vpn_shop.vpn_admin_orders(message)
            self.assertIn('وجود ندارد', message.answer.await_args.args[0])

    async def test_matching_config_delivered_once_after_receipt_approval(self):
        bot = SimpleNamespace(send_message=AsyncMock())
        async with self.factory() as session:
            session.add_all([
                VpnOrder(customer_telegram_id=123, duration_days=30, user_count=1, base_price_toman=379000, price_toman=435850, status='pending_review'),
                VpnOrder(customer_telegram_id=124, duration_days=30, user_count=1, base_price_toman=379000, price_toman=435850, status='pending_review'),
                VpnConfig(duration_days=30, user_count=1, connection_url='https://example.test/i/private', url_hash='testhash'),
            ])
            await session.commit()
        callback = SimpleNamespace(from_user=SimpleNamespace(id=900), data='vpn:review:1:approve',
                                   message=SimpleNamespace(answer=AsyncMock()), answer=AsyncMock(), bot=bot)
        with patch.object(vpn_shop, 'AsyncSessionLocal', self.factory), patch.object(vpn_shop, 'get_settings', return_value=self.settings):
            await vpn_shop.vpn_review(callback)
            self.assertIn('لینک اسمارت</a>', bot.send_message.await_args.args[1])
            async with self.factory() as session:
                self.assertEqual((await session.get(VpnOrder, 1)).status, 'delivered')
                self.assertEqual((await session.get(VpnConfig, 1)).order_id, 1)
            callback.data = 'vpn:review:2:approve'
            await vpn_shop.vpn_review(callback)
            async with self.factory() as session:
                self.assertEqual((await session.get(VpnOrder, 2)).status, 'approved')
                self.assertEqual((await session.get(VpnConfig, 1)).order_id, 1)

    async def test_smart_link_message_uses_only_its_own_plan_and_ssh_details(self):
        bot = SimpleNamespace(send_message=AsyncMock())
        async with self.factory() as session:
            session.add(VpnOrder(customer_telegram_id=123, duration_days=30, user_count=1,
                                 base_price_toman=379000, price_toman=435850, status='approved'))
            await session.commit()
        details = vpn_shop.parse_ssh_details(
            'Protocol: SSH-Direct\nName: RAINOTEK\nSSH Host: example.test\n'
            'SSH Port: 7777\nUdpgw Port: 7300\nUsername: unique1\nPassword: secret1'
        )
        with patch.object(vpn_shop, 'AsyncSessionLocal', self.factory):
            result = await vpn_shop._register_config(30, 1, 'https://example.test/i/unique1', bot, ssh_details=details)
        self.assertEqual(result, ('delivered', 1))
        sent = bot.send_message.await_args.args[1]
        self.assertEqual(bot.send_message.await_args.args[0], 123)
        self.assertIn('30 روز نامحدود (1 کاربر)', sent)
        self.assertIn('https://rcktnl.site/download', sent)
        self.assertIn('https://example.test/i/unique1', sent)
        self.assertIn('unique1 · لینک اسمارت', sent)
        self.assertIn('SSH Host: <code>example.test</code>', sent)
        self.assertIn('Password: <code>secret1</code>', sent)

    async def test_failed_send_keeps_config_reserved_for_retry(self):
        bot = SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError('offline')))
        async with self.factory() as session:
            session.add(VpnOrder(customer_telegram_id=123, duration_days=60, user_count=2, base_price_toman=899000, price_toman=1033850, status='approved'))
            session.add(VpnConfig(duration_days=60, user_count=2, connection_url='https://example.test/i/private', url_hash='retryhash'))
            await session.commit()
        with patch.object(vpn_shop, 'AsyncSessionLocal', self.factory):
            self.assertEqual(await vpn_shop._deliver_available_config(1, bot), 'send_failed')
            async with self.factory() as session:
                self.assertEqual((await session.get(VpnOrder, 1)).status, 'approved')
                self.assertEqual((await session.get(VpnConfig, 1)).status, 'reserved')
            bot.send_message.side_effect = None
            self.assertEqual(await vpn_shop._deliver_available_config(1, bot), 'delivered')
            self.assertEqual(await vpn_shop._deliver_available_config(1, bot), 'unavailable')

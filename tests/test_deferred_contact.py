import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace as N
from aiogram.types import Message, Chat, User as TelegramUser
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select
from database.models import Base, User
from bot.middlewares.user_context import UserContextMiddleware


class DeferredContactTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_dispatcher_contact_after_lost_state_reopens_menu(self):
        from aiogram import Bot, Dispatcher, Router, F
        from aiogram.filters import Command
        from aiogram.types import Update, Contact
        from aiogram.fsm.storage.memory import MemoryStorage
        from aiogram.fsm.storage.base import StorageKey
        from bot.handlers.role_panels import start_role_selection, receive_customer_contact
        engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        bot = Bot(token='123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi')
        dp = Dispatcher(storage=MemoryStorage())
        routes = Router()
        routes.message.register(start_role_selection, Command('start'))
        routes.message.register(receive_customer_contact, F.contact)
        chosen = AsyncMock()
        async def catalog(message):
            await chosen()
        routes.message.register(catalog, F.text == 'catalog')
        dp.message.middleware(UserContextMiddleware())
        dp.include_router(routes)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            actor = TelegramUser(id=777, is_bot=False, first_name='مشتری')
            with patch('bot.middlewares.user_context.AsyncSessionLocal', factory), patch('bot.handlers.role_panels.AsyncSessionLocal', factory), patch.object(Message, 'answer', AsyncMock()) as answer:
                async def send(text=None, contact=None):
                    await dp.feed_update(bot, Update(update_id=answer.await_count + 1, message=Message(message_id=1, date=datetime.now(), chat=Chat(id=777, type='private'), from_user=actor, text=text, contact=contact)))
                await send('/start')
                self.assertTrue(answer.call_args.kwargs['reply_markup'].inline_keyboard)
                await send('catalog')
                chosen.assert_not_awaited()
                # Restart/expired FSM must not discard the official contact message.
                await dp.storage.set_state(StorageKey(bot_id=bot.id, chat_id=777, user_id=777), None)
                await send(contact=Contact(phone_number='+989120000000', first_name='مشتری', user_id=777))
                self.assertIn('راینوتک', answer.call_args.args[0])
                self.assertTrue(answer.call_args.kwargs['reply_markup'].inline_keyboard)
                async with factory() as session:
                    user = await session.scalar(select(User).where(User.telegram_id == 777))
                    self.assertEqual(user.phone_number, '09120000000')
                await send('catalog')
                chosen.assert_awaited_once()
        finally:
            await dp.storage.close()
            await bot.session.close()
            await engine.dispose()

    async def test_landing_then_action_then_registered_phone(self):
        engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            actor = TelegramUser(id=777, is_bot=False, first_name='Customer')
            state = N(get_state=AsyncMock(return_value=None), clear=AsyncMock(), set_state=AsyncMock(), update_data=AsyncMock())
            handler = AsyncMock()
            middleware = UserContextMiddleware()
            with patch('bot.middlewares.user_context.AsyncSessionLocal', factory), patch.object(Message, 'answer', AsyncMock()) as answer:
                def event(text):
                    return Message(message_id=1, date=datetime.now(), chat=Chat(id=777, type='private'), from_user=actor, text=text)
                await middleware(handler, event('/start'), {'event_from_user':actor, 'state':state})
                handler.assert_awaited_once()
                await middleware(handler, event('💻 کاتالوگ و مشخصات'), {'event_from_user':actor, 'state':state})
                self.assertEqual(handler.await_count, 1)
                state.set_state.assert_awaited_once()
                answer.assert_awaited_once()
                self.assertIn('ارتباط فروشگاه با شما', answer.call_args.args[0])
                self.assertIn('دکمهٔ رسمی تلگرام', answer.call_args.args[0])
                self.assertTrue(answer.call_args.kwargs['reply_markup'].keyboard[0][0].request_contact)
                async with factory() as session:
                    user = await session.scalar(select(User).where(User.telegram_id == 777))
                    user.phone_number = '09120000000'
                    await session.commit()
                await middleware(handler, event('💻 کاتالوگ و مشخصات'), {'event_from_user':actor, 'state':state})
                self.assertEqual(handler.await_count, 2)
                self.assertEqual(answer.await_count, 1)
        finally:
            await engine.dispose()

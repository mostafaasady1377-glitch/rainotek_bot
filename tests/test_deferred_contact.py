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
                async with factory() as session:
                    user = await session.scalar(select(User).where(User.telegram_id == 777))
                    user.phone_number = '09120000000'
                    await session.commit()
                await middleware(handler, event('💻 کاتالوگ و مشخصات'), {'event_from_user':actor, 'state':state})
                self.assertEqual(handler.await_count, 2)
                self.assertEqual(answer.await_count, 1)
        finally:
            await engine.dispose()

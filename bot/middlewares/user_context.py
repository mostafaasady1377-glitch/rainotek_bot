from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from database.session import AsyncSessionLocal
from database.users import ensure_user


class UserContextMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        telegram_user = data.get("event_from_user")
        if telegram_user is None:
            return await handler(event, data)

        async with AsyncSessionLocal() as session:
            user = await ensure_user(
                session=session,
                telegram_id=telegram_user.id,
                username=telegram_user.username,
                full_name=telegram_user.full_name,
            )
            if not user.is_active:
                if isinstance(event, Message):
                    await event.answer("حساب کاربری شما غیرفعال است. با مدیر سیستم تماس بگیرید.")
                return None
            data["current_user"] = user
            return await handler(event, data)
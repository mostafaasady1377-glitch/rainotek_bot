from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from datetime import datetime
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from database.session import AsyncSessionLocal
from database.users import ensure_user
from database.models import BotDailyVisit


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
            now = datetime.utcnow()
            day = now.strftime("%Y-%m-%d")
            visit = sqlite_insert(BotDailyVisit).values(day=day, telegram_id=user.telegram_id, interaction_count=1, last_seen_at=now)
            await session.execute(visit.on_conflict_do_update(
                index_elements=[BotDailyVisit.day, BotDailyVisit.telegram_id],
                set_={"interaction_count": BotDailyVisit.interaction_count + 1, "last_seen_at": now},
            ))
            await session.commit()
            data["current_user"] = user
            return await handler(event, data)

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import Settings, get_settings
from database.models import User


async def ensure_user(
    session: AsyncSession,
    telegram_id: int,
    username: str | None,
    full_name: str | None,
    settings: Settings | None = None,
    role: str | None = None,
) -> User:
    config = settings or get_settings()
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        if telegram_id in config.ADMIN_TELEGRAM_IDS:
            initial_role = "admin"
        elif role:
            initial_role = role
        elif username == "seller":
            initial_role = "seller"
        else:
            initial_role = "customer"

        user = User(
            telegram_id=telegram_id,
            username=username,
            full_name=full_name,
            role=initial_role,
            is_active=True,
        )
        session.add(user)
    else:
        user.username = username
        user.full_name = full_name
        if telegram_id in config.ADMIN_TELEGRAM_IDS:
            user.role = "admin"
        elif role:
            user.role = role

    await session.commit()
    return user
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

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
        elif role and role != 'admin':
            initial_role = role
        else:
            initial_role = "customer"

        user = User(
            telegram_id=telegram_id,
            username=username,
            full_name=full_name,
            role=initial_role,
            is_active=True,
            first_seen_at=datetime.utcnow(),
        )
        if session.get_bind().dialect.name == 'sqlite':
            await session.execute(sqlite_insert(User).values(
                telegram_id=telegram_id,username=username,full_name=full_name,
                role=initial_role,is_active=True,first_seen_at=datetime.utcnow(),
            ).on_conflict_do_nothing(index_elements=[User.telegram_id]))
            user=await session.scalar(select(User).where(User.telegram_id==telegram_id))
        else:
            try:
            # Parallel Telegram updates may both observe a new user. Isolate the
            # unique-key race without rolling back unrelated work in this session.
                async with session.begin_nested():
                    session.add(user)
                    await session.flush()
            except IntegrityError:
                user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
                if user is None:
                    raise
    else:
        user.username = username
        user.full_name = full_name
        if telegram_id in config.ADMIN_TELEGRAM_IDS:
            user.role = "admin"
        elif user.role == 'admin':
            user.role = 'customer'
        elif role and role != 'admin':
            user.role = role

    await session.commit()
    return user

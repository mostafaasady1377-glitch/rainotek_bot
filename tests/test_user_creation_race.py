import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from sqlalchemy import select,func
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from sqlalchemy.pool import NullPool
from bot.config import Settings
from database.models import Base,User
from database.users import ensure_user


class UserRaceTest(IsolatedAsyncioTestCase):
    async def test_parallel_updates_create_one_user_without_losing_access(self):
        with TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            engine=create_async_engine('sqlite+aiosqlite:///'+str(Path(directory)/'race.db'),poolclass=NullPool,connect_args={'timeout':30})
            try:
                async with engine.begin() as connection:
                    await connection.run_sync(Base.metadata.create_all)
                factory=async_sessionmaker(engine,expire_on_commit=False)
                async def create():
                    async with factory() as session:
                        user=await ensure_user(session,123,'seller','Seller',Settings(ADMIN_TELEGRAM_IDS=[]))
                        return user.id
                ids=await asyncio.gather(*(create() for _ in range(8)),return_exceptions=True)
                self.assertFalse([str(x) for x in ids if isinstance(x,Exception)])
                self.assertEqual(len(set(ids)),1)
                async with factory() as session:
                    self.assertEqual(await session.scalar(select(func.count(User.id))),1)
            finally:
                await engine.dispose()

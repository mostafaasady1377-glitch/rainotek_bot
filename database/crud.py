from __future__ import annotations

from typing import Any, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T")


class CRUDBase:
    model: type[T]

    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, instance: T) -> T:
        self.session.add(instance)
        await self.session.flush()
        return instance

    async def get_by_id(self, instance_id: int) -> T | None:
        return await self.session.get(self.model, instance_id)

    async def get_all(self) -> list[T]:
        result = await self.session.execute(select(self.model))
        return list(result.scalars().all())

    async def delete(self, instance: T) -> None:
        await self.session.delete(instance)

"""Serialize stock writes and sheet reconciliation within the bot process."""
import asyncio
from functools import wraps

class ReentrantStockLock:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._owner = None
        self._depth = 0

    async def __aenter__(self):
        task = asyncio.current_task()
        if self._owner is not task:
            await self._lock.acquire()
            self._owner = task
        self._depth += 1
        return self

    async def __aexit__(self, *args):
        self._depth -= 1
        if self._depth == 0:
            self._owner = None
            self._lock.release()


stock_lock = ReentrantStockLock()


def serialized_stock(method):
    @wraps(method)
    async def wrapped(*args, **kwargs):
        async with stock_lock:
            try:
                return await method(*args, **kwargs)
            except BaseException:
                await args[0].session.rollback()
                raise
    return wrapped

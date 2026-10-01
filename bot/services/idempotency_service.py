from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any, Callable, Coroutine

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import IdempotencyRecord


class IdempotencyService:
    """
    سرویس مدیریت یکتایی عملیات (Idempotency) و جلوگیری از ثبت تکراری عملیات
    در اثر کلیک مکرر کاربر یا دریافت چندباره پیام/وب‌هوک تلگرام.
    """

    _in_memory_locks: dict[str, asyncio.Lock] = {}
    _global_lock = asyncio.Lock()

    @classmethod
    async def _get_lock(cls, key: str) -> asyncio.Lock:
        async with cls._global_lock:
            if key not in cls._in_memory_locks:
                cls._in_memory_locks[key] = asyncio.Lock()
            return cls._in_memory_locks[key]

    @classmethod
    async def execute_idempotent(
        cls,
        session: AsyncSession,
        idempotency_key: str,
        actor_telegram_id: int,
        action_type: str,
        operation_coro: Callable[[], Coroutine[Any, Any, Any]],
        ttl_seconds: int = 3600,
    ) -> tuple[bool, Any]:
        """
        اجرای امن و تکرارناپذیر عملیات انبارداری یا سفارش.
        خروجی: (is_new_execution: bool, result_data: Any)
        اگر عملیات قبلاً انجام شده باشد، مقدار قبلی برگردانده شده و دوبار اجرا نمی‌شود.
        """
        lock = await cls._get_lock(idempotency_key)
        async with lock:
            # 1. بررسی در دیتابیس
            existing = await session.scalar(
                select(IdempotencyRecord).where(IdempotencyRecord.idempotency_key == idempotency_key)
            )
            if existing is not None:
                logger.warning(
                    f"عملیات تکراری شناسایی شد: key={idempotency_key} | actor={actor_telegram_id} | action={action_type}"
                )
                try:
                    cached_data = json.loads(existing.result_data) if existing.result_data else None
                except Exception:
                    cached_data = existing.result_data
                return False, cached_data

            # 2. ثبت رکورد در حال پردازش
            record = IdempotencyRecord(
                idempotency_key=idempotency_key,
                actor_telegram_id=actor_telegram_id,
                action_type=action_type,
                status="processing",
                created_at=datetime.utcnow(),
            )
            session.add(record)
            await session.commit()

            # 3. اجرای عملیات واقعی
            try:
                result = await operation_coro()
                record.status = "completed"
                if isinstance(result, (dict, list, str, int, float, bool)):
                    record.result_data = json.dumps(result, ensure_ascii=False)
                else:
                    record.result_data = str(result)
                await session.commit()
                return True, result
            except Exception as exc:
                record.status = "failed"
                record.result_data = str(exc)
                await session.commit()
                raise

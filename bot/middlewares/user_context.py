from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from datetime import datetime
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject, ReplyKeyboardMarkup, KeyboardButton

from database.session import AsyncSessionLocal
from database.users import ensure_user
from database.models import BotDailyVisit, BotInteraction


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

        from bot.config import get_settings
        if get_settings().GOOGLE_SHEET_BRIDGE_URL:
            from bot.services.rhinotech_sheet_reader import RhinotechSheetReader
            async with AsyncSessionLocal() as live_session:
                result = await RhinotechSheetReader.check_and_sync_changes(live_session)
            if result.get('error'):
                target = event if isinstance(event, Message) else getattr(event, 'message', None)
                if target:
                    await target.answer("دریافت اطلاعات زندهٔ گوگل‌شیت ناموفق بود؛ لطفاً دوباره تلاش کنید.")
                if hasattr(event, 'data'):
                    await event.answer()
                return None  # Never silently present stale catalog as live.

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
            payload = getattr(event, 'data', '') or getattr(event, 'text', '') or ''
            action = ('product' if payload.startswith(('tree:laptop:', 'tree:variant:', 'ai:laptop:'))
                      else 'branch' if 'branch' in payload or 'شعب' in payload
                      else 'order' if payload.startswith(('order:', 'ord:', 'buy:'))
                      else 'search' if 'جستجو' in payload or getattr(event, 'voice', None)
                      else 'other')
            session.add(BotInteraction(telegram_id=user.telegram_id, action=action, created_at=now))
            day = now.strftime("%Y-%m-%d")
            visit = sqlite_insert(BotDailyVisit).values(day=day, telegram_id=user.telegram_id, interaction_count=1, last_seen_at=now)
            await session.execute(visit.on_conflict_do_update(
                index_elements=[BotDailyVisit.day, BotDailyVisit.telegram_id],
                set_={"interaction_count": BotDailyVisit.interaction_count + 1, "last_seen_at": now},
            ))
            await session.commit()
            data["current_user"] = user
            # Public landing/menu is available before contact; the first action is gated.
            if user.role == 'customer' and not user.phone_number and isinstance(event, (Message, CallbackQuery)):
                from bot.handlers.role_panels import PanelState, CUSTOMER
                state = data.get('state')
                text = getattr(event, 'text', '') or ''
                landing = text.split(maxsplit=1)[0].split('@')[0] in {'/start', '/cancel'} if text else False
                landing = landing or text == CUSTOMER
                contact_state = isinstance(event, Message) and state and await state.get_state() == PanelState.contact.state
                if not landing and not contact_state and not getattr(event, 'contact', None):
                    if state:
                        await state.clear()
                        await state.set_state(PanelState.contact)
                        await state.update_data(customer_panel=True)
                    target = event if isinstance(event, Message) else event.message
                    await target.answer('برای ادامه، فقط یک‌بار شمارهٔ خودتان را با دکمهٔ زیر به اشتراک بگذارید؛ سپس گزینهٔ موردنظر را دوباره انتخاب کنید.', reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text='📱 اشتراک‌گذاری شماره من', request_contact=True)]], resize_keyboard=True, one_time_keyboard=True))
                    if isinstance(event, CallbackQuery):
                        await event.answer()
                    return None
            from bot.services.sales_contact import resolve_sales_expert, sales_contact, sales_expert
            expert = await resolve_sales_expert(session, user)
            referred = user.role == 'customer' and bool(user.referrer_telegram_id)
            token = sales_contact.set((expert.phone_number or '') if expert else ('' if referred else None))
            expert_token = sales_expert.set(expert)
            try:
                return await handler(event, data)
            finally:
                sales_contact.reset(token)
                sales_expert.reset(expert_token)

"""Separate owner-only Telegram bot for RAINOTEK AI operations."""

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from loguru import logger

from bot.config import get_settings
from bot.handlers.ai_owner_panel import router
from database.session import create_db


async def main() -> None:
    settings = get_settings()
    if not settings.AI_ADMIN_BOT_TOKEN or not settings.AI_OWNER_TELEGRAM_ID:
        raise RuntimeError("AI_ADMIN_BOT_TOKEN and AI_OWNER_TELEGRAM_ID must be configured")
    if not settings.BOT_TOKEN or settings.BOT_TOKEN == settings.AI_ADMIN_BOT_TOKEN:
        raise RuntimeError("The AI admin bot needs its own token, distinct from the customer bot")
    await create_db()
    admin_bot = Bot(settings.AI_ADMIN_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    customer_bot = Bot(settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    try:
        logger.info("RAINOTEK AI owner bot started")
        await dispatcher.start_polling(admin_bot, customer_bot=customer_bot)
    finally:
        await admin_bot.session.close()
        await customer_bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

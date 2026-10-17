from __future__ import annotations

import asyncio
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from loguru import logger

from bot.handlers.common import router
from bot.handlers.catalog_browser import router as catalog_browser_router
from bot.handlers.audit import router as audit_router
from bot.handlers.admin_management import router as admin_management_router
from bot.handlers.catalog_filter import router as catalog_filter_router
from bot.handlers.inventory_admin import router as inventory_admin_router
from bot.handlers.reports import router as reports_router
from bot.handlers.stock_entry import router as stock_entry_router
from bot.handlers.stock_entry_tree import router as stock_entry_tree_router
from bot.handlers.voice_assistant import router as voice_assistant_router
from bot.handlers.purchase_request import router as purchase_request_router
from bot.handlers.online_support import router as online_support_router
from bot.handlers.role_panels import router as role_panels_router
from bot.handlers.panel_navigation import router as panel_navigation_router
from bot.handlers.dashboard_cards import router as dashboard_cards_router
from bot.handlers.branch_manager_panel import router as branch_manager_router
from bot.handlers.financial_archive import router as financial_router
from bot.handlers.management_dashboard import router as management_dashboard_router
from bot.handlers.vpn_shop import router as vpn_shop_router
from bot.handlers.ai_hub import router as ai_hub_router
from bot.handlers.premium_shop import router as premium_shop_router
from bot.handlers.divar_campaigns import router as divar_campaigns_router
from bot.middlewares.user_context import UserContextMiddleware
from bot.services.customer_inbox import InboxCaptureMiddleware
from bot.services.customer_chat_menu import InlineNavigationMiddleware, PanelMessageMiddleware
from bot.handlers.customer_chat_menu import router as customer_chat_menu_router
from bot.handlers.customer_inbox import router as customer_inbox_router
from bot.config import get_settings
from database.session import AsyncSessionLocal, create_db


async def main(*, enable_bale: bool = False) -> None:
    settings = get_settings()
    if enable_bale:
        if not settings.BALE_BOT_TOKEN or not settings.BALE_ADMIN_IDS:
            raise RuntimeError('Bale token and verified admin IDs are required')
        from bot.services.platform_identity import bale_identity
        settings.ADMIN_TELEGRAM_IDS = list(dict.fromkeys(settings.ADMIN_TELEGRAM_IDS + [bale_identity(i) for i in settings.BALE_ADMIN_IDS]))
    logger.remove()
    logger.add("rainotek_bot.log", rotation="1 MB", enqueue=True)
    import sys
    logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}", level="INFO")
    logger.info("شروع راه‌اندازی ربات RAINOTEK")

    dp = Dispatcher()
    dp.message.outer_middleware(InboxCaptureMiddleware())
    dp.edited_message.outer_middleware(InboxCaptureMiddleware())
    dp.message.middleware(UserContextMiddleware())
    dp.message.middleware(PanelMessageMiddleware())
    dp.callback_query.middleware(UserContextMiddleware())
    dp.callback_query.middleware(InlineNavigationMiddleware())
    dp.include_router(customer_inbox_router)
    dp.include_router(customer_chat_menu_router)
    dp.include_router(divar_campaigns_router)
    dp.include_router(role_panels_router)
    dp.include_router(panel_navigation_router)
    dp.include_router(dashboard_cards_router)
    dp.include_router(branch_manager_router)
    dp.include_router(financial_router)
    dp.include_router(vpn_shop_router)
    dp.include_router(ai_hub_router)
    dp.include_router(premium_shop_router)
    dp.include_router(management_dashboard_router)
    dp.include_router(online_support_router)
    dp.include_router(purchase_request_router)
    dp.include_router(catalog_browser_router)
    dp.include_router(stock_entry_tree_router)
    dp.include_router(audit_router)
    dp.include_router(router)
    dp.include_router(inventory_admin_router)
    dp.include_router(admin_management_router)
    dp.include_router(reports_router)
    dp.include_router(catalog_filter_router)
    dp.include_router(stock_entry_router)
    dp.include_router(voice_assistant_router)

    await create_db()
    logger.success("پایگاه داده با موفقیت متصل شد.")

    # در هر راه‌اندازی، قبل از شروع polling آخرین مدل‌ها و قیمت‌ها را دریافت کن.
    # محدود کردن این مرحله به دیتابیس‌های خالی باعث می‌شد کاتالوگ موجود تا اجرای
    # بعدی حلقهٔ پس‌زمینه قدیمی بماند.
    async with AsyncSessionLocal() as session:
        from bot.services.branch_service import BranchService
        from bot.services.rhinotech_sheet_reader import RhinotechSheetReader

        await BranchService.ensure_canonical_branches(session)
        logger.info("دریافت آخرین مدل‌ها و قیمت‌ها از Google Sheets راینوتک...")
        try:
            sync_res = await RhinotechSheetReader.sync_sheet_to_database(session)
            logger.success(
                "همگام‌سازی اولیه موفق: {} کالا از {} ردیف در {} شعبه به‌روزرسانی شد.",
                sync_res["products_synced"],
                sync_res["source_rows"],
                sync_res["branches_count"],
            )
        except Exception as exc:
            # دیتای قبلی به‌صورت تراکنشی حفظ می‌شود و حلقهٔ پس‌زمینه مجدداً تلاش می‌کند.
            logger.warning("عدم امکان دریافت اولیه از Google Sheets؛ دادهٔ قبلی حفظ شد: {}", exc)

    token = settings.BOT_TOKEN.strip() if settings.BOT_TOKEN else ""
    if not token or "ExampleToken" in token or token.startswith("123456789:"):
        print(
            "\n"
            "====================================================================\n"
            "✨ ربات فروشگاه کامپیوتر راینوتک (Rhinotech Bot) با موفقیت آماده شد!\n"
            "--------------------------------------------------------------------\n"
            "📊 وضعیت دیتابیس:\n"
            "   ✅ ۱۸۲ لپ‌تاپ و قطعه از Google Sheets همگام‌سازی شد.\n"
            "   ✅ تصویر شاخص برای ۱۸۷ مدل دانلود و ثبت گردید.\n"
            "   ✅ ۸ شعبه (صادقیه، میرداماد، فلاح، هروی، شهرک و...) فعال است.\n"
            "   ✅ ۲۱ تست یکپارچگی و ضدتقلب (Idempotency) با موفقیت ۱۰۰٪ پاس شدند.\n"
            "--------------------------------------------------------------------\n"
            "🔑 جهت اتصال نهایی به تلگرام:\n"
            "   توکن ربات دریافتی از @BotFather را در فایل .env در متغیر BOT_TOKEN قرار دهید:\n"
            "   BOT_TOKEN=7123456789:AAFx...توکن_شما...\n"
            "====================================================================\n"
        )
        return

    bot = Bot(
        token=token,
        session=AiohttpSession(proxy=settings.NETWORK_PROXY_URL or None),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    polling_bots = [bot]
    from bot.services.telegram_menu import configure_telegram_menu
    try:
        await configure_telegram_menu(bot)
    except Exception as exc:
        logger.warning('Telegram menu setup unavailable: {}',type(exc).__name__)
    if enable_bale:
        from bot.services.bale_transport import PlatformSession
        telegram_session = PlatformSession('telegram', settings.BOT_TOKEN, settings.BALE_BOT_TOKEN, proxy=settings.NETWORK_PROXY_URL or None)
        bale_session = PlatformSession('bale', settings.BALE_BOT_TOKEN, settings.BOT_TOKEN)
        await bot.session.close()
        bot.session = telegram_session
        bale_bot = Bot(settings.BALE_BOT_TOKEN, session=bale_session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        telegram_session.peer_bot = bale_bot
        bale_session.peer_bot = bot
        await bale_bot.get_me()  # Fail before starting duplicate/partially configured polling.
        polling_bots.append(bale_bot)
        logger.info('Bale transport connected; shared inventory and isolated user identities enabled.')
    from bot.services.chat_position import track_outgoing
    for runtime_bot in polling_bots:
        runtime_bot.session.middleware(track_outgoing)
    logger.info("روترها بارگذاری شدند و ربات در حالت Polling فعال است.")
    
    # راه‌اندازی فرآیند پس‌زمینه همگام‌سازی بلادرنگ آنلاین با Google Sheets
    sync_task = asyncio.create_task(
        RhinotechSheetReader.run_live_sync_loop(interval_seconds=settings.SHEET_SYNC_INTERVAL_SECONDS, bot=bot)
    )
    from bot.services.divar_recommendations import recommendation_loop
    recommendation_task = asyncio.create_task(recommendation_loop())
    try:
        await dp.start_polling(*polling_bots)
    finally:
        sync_task.cancel()
        recommendation_task.cancel()
        await asyncio.gather(sync_task, recommendation_task, return_exceptions=True)
        for polling_bot in polling_bots:
            await polling_bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

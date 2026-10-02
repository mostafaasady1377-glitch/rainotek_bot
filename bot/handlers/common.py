from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.handlers.catalog_browser import start_catalog

router = Router()


from database.models import User
from database.session import AsyncSessionLocal
from bot.services.google_sheets_service import GoogleSheetsService

router = Router()


@router.message(Command("help"))
async def cmd_help(message: Message, current_user: User | None = None) -> None:
    role = current_user.role if current_user else "customer"
    if role in ("admin", "branch_manager", "seller"):
        await message.answer(
            "📋 <b>راهنمای دستورات مدیر و کارکنان راینوتک:</b>\n\n"
            "• /start - شروع مجدد و نمایش منو\n"
            "• /stock یا /catalog - کاتالوگ و مشخصات کالا\n"
            "• /search [کلمه] - جست‌وجوی سریع لپ‌تاپ\n"
            "• /orders - مشاهده درخواست‌های خرید مشتریان\n"
            "• /stock_wizard - ویزارد ثبت موجودی\n"
            "• /stock_add کد_کالا کد_شعبه تعداد - افزایش موجودی (ورود)\n"
            "• /stock_deduct کد_کالا کد_شعبه تعداد - کاهش موجودی (خروج)\n"
            "• /stock_transfer کد_کالا مبدأ مقصد تعداد - انتقال بین شعب\n"
            "• /audit_wizard - شروع انبارگردانی شعبه\n"
            "• /low_stock - گزارش اقلام کم‌موجود\n"
            "• /stock_export - خروجی CSV موجودی\n"
            "• /sync_sheets - همگام‌سازی مشخصات از گوگل شیت\n"
            "• /user_role شناسه نقش - تغییر نقش کاربر (admin / branch_manager / seller / customer)"
        )
    else:
        await message.answer(
            "ℹ️ <b>راهنمای مشتریان راینوتک:</b>\n\n"
            "• 💻 <b>کاتالوگ و مشخصات:</b> مشاهده لیست دسته‌بندی‌شده برندها، مدل‌ها و مشخصات فنی کامل\n"
            "• 🔎 <b>جستجو:</b> جستجوی مدل، برند، رم یا پردازنده دلخواه\n"
            "• 🏷 <b>لپ‌تاپ‌های نو و استوک:</b> تفکیک دستگاه‌های آکبند و استوک با گارانتی\n"
            "• 🛍 <b>ثبت درخواست خرید:</b> برای هر مدل دکمه ثبت درخواست خرید وجود دارد تا کارشناسان فروش با شما تماس بگیرند.\n"
            "• 🏢 <b>شعب راینوتک:</b> آدرس، تلفن و لوکیشن شعب برای خرید حضوری\n"
            "• 🛒 <b>پیگیری سفارش من:</b> مشاهده وضعیت آخرین درخواست‌های ثبت‌شده"
        )


@router.message(F.text == "🔄 همگام‌سازی شیت")
@router.message(Command("sync_sheets"))
async def cmd_sync_sheets(message: Message, current_user: User | None = None) -> None:
    if not current_user or current_user.role != "admin":
        await message.answer("همگام‌سازی با گوگل شیت فقط برای مدیر کل مجاز است.")
        return

    from bot.services.rhinotech_sheet_reader import RhinotechSheetReader

    msg = await message.answer("⏳ در حال دریافت زنده موجودی و مشخصات از Google Sheets فروشگاه راینوتک...")
    async with AsyncSessionLocal() as session:
        try:
            res = await RhinotechSheetReader.sync_sheet_to_database(session)
            await msg.edit_text(
                f"✅ <b>همگام‌سازی موفق کاتالوگ از Google Sheets راینوتک!</b>\n\n"
                f"💻 تعداد محصولات فعال: <b>{res['products_synced']}</b> قلم کالا\n"
                f"🏢 شعب شناسایی‌شده: <b>{res['branches_count']}</b> شعبه (صادقیه، میرداماد، فلاح، هروی، شهرک)\n"
                f"🕒 زمان دریافت: <code>{res['sync_time']} UTC</code>\n\n"
                f"<i>کلیه مدل‌ها و قیمت‌ها در کاتالوگ و جستجوی بات بروز شدند.</i>"
            )
        except Exception as exc:
            info = RhinotechSheetReader.get_last_sync_info()
            last_time = info.get("last_sync_time") or "ثبت نشده"
            await msg.edit_text(
                f"❌ <b>خطا در ارتباط با گوگل شیت:</b> {exc}\n\n"
                f"⚠️ <b>هشدار:</b> آخرین دریافت موفق مربوط به <code>{last_time}</code> است و به دلیل خطای اتصال، اطلاعات قدیمی موجودی تأییدشدهٔ فعلی تلقی نمی‌گردد."
            )


@router.message(Command("sheet_status"))
async def cmd_sheet_status(message: Message, current_user: User | None = None) -> None:
    from bot.services.rhinotech_sheet_reader import RhinotechSheetReader
    info = RhinotechSheetReader.get_last_sync_info()
    last_time = info.get("last_sync_time")
    status = "🟢 متصل و معتبر" if info.get("last_sync_status") else "🔴 نیاز به بررسی / خطا"

    lines = [
        "📊 <b>وضعیت اتصال Google Sheets فروشگاه راینوتک:</b>\n",
        f"• وضعیت ارتباط: <b>{status}</b>",
        f"• زمان آخرین دریافت: <code>{last_time or 'هنوز همگام نشده'}</code>",
        f"• تعداد اقلام در حافظه: <b>{info.get('cached_items_count', 0)}</b> کالا",
        f"• شناسه شیت: <code>1jDWTufbdaTqG8gAn8xHp096j91wl9_pDXeKbrZ8KLYg</code>",
    ]
    if info.get("error_message"):
        lines.append(f"• آخرین خطا: <i>{info['error_message']}</i>")

    await message.answer("\n".join(lines))



@router.message(Command("stock"))
async def cmd_stock(message: Message, state) -> None:
    await start_catalog(message, state)


from bot.handlers.catalog_browser import open_smart_search_menu, start_catalog


@router.message(Command("search"))
async def cmd_search(message: Message) -> None:
    await open_smart_search_menu(message)


@router.message(F.text == "📦 موجودی")
async def menu_stock(message: Message, state) -> None:
    await start_catalog(message, state)


@router.message(F.text == "🔎 جستجو")
@router.message(F.text == "🔎 جستجوی هوشمند")
async def menu_search(message: Message) -> None:
    await open_smart_search_menu(message)


@router.message(F.text == "ℹ️ راهنما")
@router.message(F.text == "ℹ️ راهنمای خرید و تماس")
async def menu_help(message: Message, current_user: User | None = None) -> None:
    await cmd_help(message, current_user=current_user)




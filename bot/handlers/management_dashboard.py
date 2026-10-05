"""Read-only operational reporting for the administrator panel."""

from __future__ import annotations

from datetime import datetime, timedelta
from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, or_, select

from bot.handlers.role_panels import _is_admin
from database.models import (
    BotDailyVisit, Branch, BranchInventory, InventoryAuditLog,
    LaptopStaffOverride, PurchaseRequest, StaffActivity, StockAuditChecklist,
    SupportRequest, User,
)
from database.session import AsyncSessionLocal
from bot.services.local_time import format_local, period_bounds
from bot.services.entry_reports import entry_report, LABELS
from bot.services.sales_contact import telegram_chat_url

router = Router()
PAGE_SIZE = 10
ROLE_LABEL = {"branch_manager": "مدیر شعبه", "seller": "کارشناس فروش"}
ACTION_LABEL = {"product_edit": "ویرایش محصول", "order_status": "تغییر وضعیت درخواست", "role_change": "تغییر دسترسی"}


def _day_bounds(days_ago: int = 0) -> tuple[datetime, datetime]:
    start, end = period_bounds()
    return start - timedelta(days=days_ago), end - timedelta(days=days_ago)


def _staff_keyboard(page: int, total: int, rows: list[User]) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text=f"{ROLE_LABEL.get(user.role, user.role)} · {user.full_name or user.username or user.telegram_id}"[:64], callback_data=f"mgmt:person:{user.id}")] for user in rows]
    navigation = []
    if page > 0:
        navigation.append(InlineKeyboardButton(text="صفحهٔ قبل", callback_data=f"mgmt:staff:{page-1}"))
    if (page + 1) * PAGE_SIZE < total:
        navigation.append(InlineKeyboardButton(text="صفحهٔ بعد", callback_data=f"mgmt:staff:{page+1}"))
    if navigation:
        buttons.append(navigation)
    buttons.append([InlineKeyboardButton(text='➕ افزودن عضو و تعیین نقش', callback_data='panel:addmember')])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def _send_staff(message: Message, page: int) -> None:
    page = max(0, page)
    async with AsyncSessionLocal() as session:
        base = select(User).where(User.role.in_(ROLE_LABEL))
        total = await session.scalar(select(func.count(User.id)).where(User.role.in_(ROLE_LABEL))) or 0
        rows = list((await session.scalars(base.order_by(User.role, User.id).offset(page * PAGE_SIZE).limit(PAGE_SIZE))).all())
    if total and not rows:
        page = 0
        return await _send_staff(message, page)
    await message.answer(
        f"👥 مدیران و کارشناسان فروش\nتعداد کل: {total}\nصفحهٔ {page+1} از {max(1, (total + PAGE_SIZE - 1)//PAGE_SIZE)}\nبرای دیدن فعالیت هر نفر، نام او را انتخاب کنید.",
        reply_markup=_staff_keyboard(page, total, rows),
    )


@router.message(F.text == "👥 مدیران و کارشناسان")
async def staff_directory(message: Message, current_user: User) -> None:
    if not _is_admin(current_user):
        await message.answer("دسترسی مجاز نیست.")
        return
    await _send_staff(message, 0)


@router.callback_query(F.data.startswith("mgmt:staff:"))
async def staff_directory_page(callback: CallbackQuery, current_user: User) -> None:
    if not _is_admin(current_user):
        await callback.answer("دسترسی مجاز نیست.", show_alert=True)
        return
    try:
        page = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer("صفحه نامعتبر است.", show_alert=True)
        return
    await callback.answer()
    await _send_staff(callback.message, page)


@router.callback_query(F.data.startswith("mgmt:person:"))
async def staff_detail(callback: CallbackQuery, current_user: User) -> None:
    if not _is_admin(current_user):
        await callback.answer("دسترسی مجاز نیست.", show_alert=True)
        return
    try:
        user_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer("شناسه نامعتبر است.", show_alert=True)
        return
    since = datetime.utcnow() - timedelta(days=7)
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if user is None or user.role not in ROLE_LABEL:
            await callback.answer("این حساب دیگر جزو پرسنل نیست.", show_alert=True)
            return
        branch = await session.get(Branch, user.managed_branch_id) if user.managed_branch_id else None
        edits = await session.scalar(select(func.count(StaffActivity.id)).where(StaffActivity.actor_telegram_id == user.telegram_id, StaffActivity.action == "product_edit", StaffActivity.created_at >= since)) or 0
        orders = await session.scalar(select(func.count(StaffActivity.id)).where(StaffActivity.actor_telegram_id == user.telegram_id, StaffActivity.action == "order_status", StaffActivity.created_at >= since)) or 0
        inventory = await session.scalar(select(func.count(InventoryAuditLog.id)).where(InventoryAuditLog.actor_telegram_id == user.telegram_id, InventoryAuditLog.timestamp >= since)) or 0
        audits = await session.scalar(select(func.count(StockAuditChecklist.id)).where(StockAuditChecklist.auditor_telegram_id == user.telegram_id, StockAuditChecklist.created_at >= since)) or 0
        support = await session.scalar(select(func.count(SupportRequest.id)).where(SupportRequest.assigned_user_id == user.id, SupportRequest.created_at >= since)) or 0
        recent = list((await session.scalars(select(StaffActivity).where(StaffActivity.actor_telegram_id == user.telegram_id).order_by(StaffActivity.created_at.desc()).limit(5))).all())
    lines = [
        f"👤 {escape(user.full_name or user.username or str(user.telegram_id))}",
        f"نقش: {ROLE_LABEL[user.role]} | وضعیت: {'فعال' if user.is_active else 'غیرفعال'}",
        f"شعبهٔ ثبت‌شده: {escape(branch.name) if branch else 'تعیین نشده'}",
        f"Chat ID: <code>{user.telegram_id}</code>",
        "", "عملکرد ۷ روز اخیر:",
        f"• ویرایش محصول: {edits}", f"• تغییر وضعیت درخواست خرید: {orders}",
        f"• عملیات انبار: {inventory}", f"• انبارگردانی آغازشده: {audits}",
        f"• پشتیبانی ارجاع‌شده: {support}",
    ]
    if recent:
        lines.extend(["", "آخرین فعالیت‌های ثبت‌شده:"])
        lines.extend(f"• {format_local(event.created_at)} | {ACTION_LABEL.get(event.action, event.action)} | {escape(event.detail or '')}" for event in recent)
    await callback.answer()
    lines.insert(4, f"📱 {escape(user.phone_number or 'شماره ثبت نشده')}")
    await callback.message.answer("\n\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💬 چت با {user.full_name or user.username or 'این کارشناس'}"[:64], url=telegram_chat_url(user))],
        [InlineKeyboardButton(text='نقش کارشناس فروش', callback_data=f'panel:role:{user.id}:seller'), InlineKeyboardButton(text='ارتقاء به مدیر شعبه', callback_data=f'panel:role:{user.id}:branch_manager')],
        [InlineKeyboardButton(text='⛔ لغو ویرایش محصول و فایل' if user.product_edit_allowed else '✅ اجازهٔ ویرایش محصول و فایل', callback_data=f'panel:editaccess:{user.id}:{0 if user.product_edit_allowed else 1}')],
        [InlineKeyboardButton(text='عزل از پرسنل', callback_data=f'panel:role:{user.id}:customer')],
        [InlineKeyboardButton(text='لغو دسترسی امور مالی' if user.accounting_access else 'اعطای دسترسی حسابداری', callback_data=f'finaccess:{user.id}:{0 if user.accounting_access else 1}')],
        [InlineKeyboardButton(text="بازگشت به فهرست", callback_data="mgmt:staff:0")],
        [InlineKeyboardButton(text="🏠 پنل من", callback_data="panel:home")],
    ]))


@router.message(F.text == "🏢 عملکرد شعب")
async def branch_performance(message: Message, current_user: User, state=None) -> None:
    if not _is_admin(current_user):
        await message.answer("دسترسی مجاز نیست.")
        return
    from bot.handlers.dashboard_cards import send_branch_dashboard
    if state:
        await state.clear()
    await send_branch_dashboard(message)


@router.message(F.text == "📈 ورودی‌های روزانه")
async def daily_entries(message: Message, current_user: User) -> None:
    if not _is_admin(current_user):
        await message.answer("دسترسی مجاز نیست.")
        return
    await send_entry_report(message)


async def send_entry_report(message, period='daily', page=0):
    async with AsyncSessionLocal() as session:
        text, total = await entry_report(session, period, page)
    buttons = [[InlineKeyboardButton(text=label, callback_data=f'mgmt:entries:{key}:0') for key, label in LABELS.items()]]
    navigation = []
    if page:
        navigation.append(InlineKeyboardButton(text='قبلی', callback_data=f'mgmt:entries:{period}:{page-1}'))
    if (page + 1) * 5 < total:
        navigation.append(InlineKeyboardButton(text='بعدی', callback_data=f'mgmt:entries:{period}:{page+1}'))
    if navigation:
        buttons.append(navigation)
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith('mgmt:entries:'))
async def entry_period(callback, current_user: User):
    if not _is_admin(current_user):
        await callback.answer('دسترسی مجاز نیست.', show_alert=True)
        return
    try:
        _, _, period, raw_page = callback.data.split(':')
        page = int(raw_page)
        if period not in LABELS or page < 0:
            raise ValueError
    except (ValueError, TypeError):
        await callback.answer('بازه نامعتبر است.', show_alert=True)
        return
    await callback.answer()
    await send_entry_report(callback.message, period, page)

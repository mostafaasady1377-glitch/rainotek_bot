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

router = Router()
PAGE_SIZE = 10
ROLE_LABEL = {"branch_manager": "مدیر فروش", "seller": "کارشناس فروش"}
ACTION_LABEL = {"product_edit": "ویرایش محصول", "order_status": "تغییر وضعیت درخواست", "role_change": "تغییر دسترسی"}


def _day_bounds(days_ago: int = 0) -> tuple[datetime, datetime]:
    start = datetime.combine(datetime.utcnow().date() - timedelta(days=days_ago), datetime.min.time())
    return start, start + timedelta(days=1)


def _staff_keyboard(page: int, total: int, rows: list[User]) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text=f"{ROLE_LABEL.get(user.role, user.role)} · {user.full_name or user.username or user.telegram_id}"[:64], callback_data=f"mgmt:person:{user.id}")] for user in rows]
    navigation = []
    if page > 0:
        navigation.append(InlineKeyboardButton(text="صفحهٔ قبل", callback_data=f"mgmt:staff:{page-1}"))
    if (page + 1) * PAGE_SIZE < total:
        navigation.append(InlineKeyboardButton(text="صفحهٔ بعد", callback_data=f"mgmt:staff:{page+1}"))
    if navigation:
        buttons.append(navigation)
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
        reply_markup=_staff_keyboard(page, total, rows) if rows else None,
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
        lines.extend(f"• {event.created_at:%Y-%m-%d %H:%M} UTC | {ACTION_LABEL.get(event.action, event.action)} | {escape(event.detail or '')}" for event in recent)
    await callback.answer()
    await callback.message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="بازگشت به فهرست", callback_data="mgmt:staff:0")]]))


@router.message(F.text == "🏢 عملکرد شعب")
async def branch_performance(message: Message, current_user: User) -> None:
    if not _is_admin(current_user):
        await message.answer("دسترسی مجاز نیست.")
        return
    since = datetime.utcnow() - timedelta(days=7)
    async with AsyncSessionLocal() as session:
        branches = list((await session.scalars(select(Branch).where(Branch.is_active.is_(True)).order_by(Branch.id))).all())
        lines = ["🏢 وضعیت شعب راینوتک | ۷ روز اخیر"]
        for branch in branches:
            managers = (await session.scalars(select(User).where(User.role == "branch_manager", User.managed_branch_id == branch.id, User.is_active.is_(True)))).all()
            inventory = await session.scalar(select(func.coalesce(func.sum(BranchInventory.quantity - BranchInventory.reserved_count), 0)).where(BranchInventory.branch_id == branch.id)) or 0
            requests = await session.scalar(select(func.count(PurchaseRequest.id)).where(PurchaseRequest.branch_id == branch.id, PurchaseRequest.created_at >= since)) or 0
            actions = await session.scalar(select(func.count(InventoryAuditLog.id)).where(or_(InventoryAuditLog.source_branch_id == branch.id, InventoryAuditLog.destination_branch_id == branch.id), InventoryAuditLog.timestamp >= since)) or 0
            manager_names = "، ".join(user.full_name or user.username or str(user.telegram_id) for user in managers)
            lines.extend(["", f"{escape(branch.name)}", f"مدیر: {escape(manager_names or 'تعیین نشده')}", f"موجودی قابل فروش: {inventory} دستگاه | درخواست خرید: {requests} | عملیات انبار: {actions}"])
    await message.answer("\n".join(lines))


@router.message(F.text == "📈 ورودی‌های روزانه")
async def daily_entries(message: Message, current_user: User) -> None:
    if not _is_admin(current_user):
        await message.answer("دسترسی مجاز نیست.")
        return
    async with AsyncSessionLocal() as session:
        lines = ["📈 ورودی‌های روزانهٔ بات | ۷ روز اخیر (UTC)", "ثبت مراجعه از زمان فعال‌شدن این پنل آغاز شده است."]
        for days_ago in range(7):
            start, end = _day_bounds(days_ago)
            day = start.strftime("%Y-%m-%d")
            visitors = await session.scalar(select(func.count(BotDailyVisit.telegram_id)).where(BotDailyVisit.day == day)) or 0
            interactions = await session.scalar(select(func.coalesce(func.sum(BotDailyVisit.interaction_count), 0)).where(BotDailyVisit.day == day)) or 0
            new_users = await session.scalar(select(func.count(User.id)).where(User.first_seen_at >= start, User.first_seen_at < end)) or 0
            crm = await session.scalar(select(func.count(User.id)).where(User.joined_at >= start, User.joined_at < end)) or 0
            lines.append(f"{day}: {visitors} مراجعه‌کننده، {interactions} تعامل، {new_users} ورودی جدید، {crm} شمارهٔ ثبت‌شده")
    await message.answer("\n".join(lines))

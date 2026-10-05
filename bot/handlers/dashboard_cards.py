from html import escape
from aiogram import F, Router
from aiogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup as K
from sqlalchemy import select, func, or_
from database.models import User, Branch, BranchInventory, PurchaseRequest, InventoryAuditLog, StockAuditChecklist
from database.session import AsyncSessionLocal
from bot.handlers.role_panels import _is_admin
from bot.handlers.panel_navigation import report_data
from bot.services.local_time import period_bounds, format_local
from bot.services.entry_reports import LABELS
from bot.services.sales_contact import telegram_chat_url

router = Router()


def period_buttons(prefix, period):
    return [B(text=('✓ ' if key == period else '') + label, callback_data=f'{prefix}:{key}') for key, label in LABELS.items()]


async def crm_data(session, user, period, page):
    start, end = period_bounds(period)
    new = await session.scalar(select(func.count(User.id)).where(User.first_seen_at >= start, User.first_seen_at < end)) or 0
    phones = await session.scalar(select(func.count(User.id)).where(User.joined_at >= start, User.joined_at < end)) or 0
    customers = await session.scalar(select(func.count(User.id)).where(User.role == 'customer')) or 0
    referred = await session.scalar(select(func.count(User.id)).where(User.role == 'customer', User.referrer_telegram_id.is_not(None))) or 0
    stages = (await session.execute(select(User.crm_stage, func.count(User.id)).where(User.role == 'customer').group_by(User.crm_stage))).all()
    orders = (await session.execute(select(PurchaseRequest.status, func.count(PurchaseRequest.id)).where(PurchaseRequest.created_at >= start, PurchaseRequest.created_at < end).group_by(PurchaseRequest.status))).all()
    report, keyboard = await report_data(session, user, 'visits', period, page)
    text = f'📊 <b>داشبورد CRM · {LABELS[period]}</b>\n━━━━━━━━━━━━\n🆕 کاربران جدید: {new}\n📱 ثبت شماره در بازه: {phones}\n🛍 کل مشتریان: {customers}\n🔗 کل مشتریان ارجاعی: {referred}\n\nمراحل CRM (کل مشتریان):\n' + '\n'.join(f'• {escape(stage or "ثبت نشده")}: {number}' for stage, number in stages)
    text += '\n\nدرخواست‌های خرید در بازه:\n' + ('\n'.join(f'• {escape(status)}: {number}' for status, number in orders) or 'موردی ثبت نشده است.')
    keyboard.inline_keyboard[0] = period_buttons('dash:crm:0', period)
    keyboard.inline_keyboard.append([B(text='🔄 تازه‌سازی', callback_data=f'dash:crm:{page}:{period}')])
    return text + '\n━━━━━━━━━━━━\n' + report, keyboard


async def send_crm_dashboard(message, user, period='daily', page=0):
    async with AsyncSessionLocal() as session:
        text, keyboard = await crm_data(session, user, period, page)
    await message.answer(text, reply_markup=keyboard)


async def send_branch_dashboard(message, period='daily'):
    async with AsyncSessionLocal() as session:
        branches = list((await session.scalars(select(Branch).where(Branch.is_active.is_(True)).order_by(Branch.id))).all())
    rows = [period_buttons('dash:branches', period)]
    rows += [[B(text=f'🏢 {b.name}'[:64], callback_data=f'dash:branch:{b.id}:{period}')] for b in branches]
    rows += [[B(text='🏠 پنل من', callback_data='panel:home')]]
    await message.answer(f'🏢 عملکرد شعب · {LABELS[period]}\n\nشعبه را برای دیدن موجودی، رزرو، درخواست خرید و عملیات انبار انتخاب کنید.' + ('\nشعبهٔ فعالی ثبت نشده است.' if not branches else ''), reply_markup=K(inline_keyboard=rows))


async def branch_data(session, branch_id, period):
    branch = await session.get(Branch, branch_id)
    if not branch or not branch.is_active:
        raise ValueError('شعبهٔ فعال پیدا نشد.')
    start, end = period_bounds(period)
    stock, reserved = (await session.execute(select(func.coalesce(func.sum(BranchInventory.quantity), 0), func.coalesce(func.sum(BranchInventory.reserved_count), 0)).where(BranchInventory.branch_id == branch_id))).one()
    staff = list((await session.scalars(select(User).where(User.managed_branch_id == branch_id, User.role.in_(('seller', 'branch_manager')), User.is_active.is_(True)).order_by(User.id))).all())
    orders = (await session.execute(select(PurchaseRequest.status, func.count(PurchaseRequest.id)).where(PurchaseRequest.branch_id == branch_id, PurchaseRequest.created_at >= start, PurchaseRequest.created_at < end).group_by(PurchaseRequest.status))).all()
    filters = [or_(InventoryAuditLog.source_branch_id == branch_id, InventoryAuditLog.destination_branch_id == branch_id), InventoryAuditLog.timestamp >= start, InventoryAuditLog.timestamp < end]
    movements = await session.scalar(select(func.count(InventoryAuditLog.id)).where(*filters)) or 0
    recent = list((await session.scalars(select(InventoryAuditLog).where(*filters).order_by(InventoryAuditLog.timestamp.desc(), InventoryAuditLog.id.desc()).limit(5))).all())
    audits = await session.scalar(select(func.count(StockAuditChecklist.id)).where(StockAuditChecklist.branch_id == branch_id, StockAuditChecklist.status.in_(('in_progress', 'pending_approval')))) or 0
    text = f'🏢 <b>{escape(branch.name)} · {LABELS[period]}</b>\n\n🗓 {format_local(start)} تا {format_local(end)}\n━━━━━━━━━━━━\n📦 موجودی فعلی: {stock}\n🔒 رزرو فعلی: {reserved}\n✅ قابل فروش: {max(0, stock-reserved)}\n👥 پرسنل فعال منتسب: {len(staff)}\n🔄 عملیات انبار در بازه: {movements}\n🧾 انبارگردانی باز: {audits}\n━━━━━━━━━━━━\nدرخواست‌های خرید در بازه:\n' + ('\n'.join(f'• {escape(status)}: {number}' for status, number in orders) or 'موردی ثبت نشده است.')
    text += '\n\nآخرین عملیات انبار:\n' + ('\n'.join(f'• {format_local(log.timestamp)} · {escape(log.change_type)} · {log.quantity} عدد · کالا #{log.laptop_id}' for log in recent) or 'عملیاتی ثبت نشده است.')
    text += '\n\nموجودی، وضعیت فعلی است؛ درخواست خرید معادل فروش قطعی نیست. ورودی کاربران بدون اطلاعات شعبه به شعبه‌ای نسبت داده نمی‌شود.'
    rows = [period_buttons(f'dash:branch:{branch_id}', period)]
    rows += [[B(text=f'💬 {u.full_name or u.username or u.telegram_id}'[:64], url=telegram_chat_url(u))] for u in staff[:10]]
    rows += [[B(text='👥 فهرست کامل پرسنل', callback_data='mgmt:staff:0')], [B(text='🔄 تازه‌سازی', callback_data=f'dash:branch:{branch_id}:{period}'), B(text='🔙 شعب', callback_data=f'dash:branches:{period}')], [B(text='🏠 پنل من', callback_data='panel:home')]]
    return text, K(inline_keyboard=rows)


@router.callback_query(F.data.startswith('dash:'))
async def dashboard_callback(callback, current_user):
    if not _is_admin(current_user):
        await callback.answer('دسترسی مجاز نیست.', show_alert=True)
        return
    try:
        parts = callback.data.split(':')
        kind, period = parts[1], parts[-1]
        if period not in LABELS:
            raise ValueError
        if kind == 'crm' and len(parts) == 4:
            page = int(parts[2])
            if page < 0:
                raise ValueError
        elif kind == 'branch' and len(parts) == 4:
            branch_id = int(parts[2])
            async with AsyncSessionLocal() as session:
                text, keyboard = await branch_data(session, branch_id, period)
        elif kind != 'branches' or len(parts) != 3:
            raise ValueError
    except (ValueError, TypeError, IndexError):
        await callback.answer('درخواست یا شعبه نامعتبر است.', show_alert=True)
        return
    await callback.answer()
    if kind == 'crm':
        await send_crm_dashboard(callback.message, current_user, period, page)
    elif kind == 'branches':
        await send_branch_dashboard(callback.message, period)
    else:
        await callback.message.answer(text, reply_markup=keyboard)

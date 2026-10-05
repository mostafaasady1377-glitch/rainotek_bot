"""Scoped clickable reports and configuration selection; no changes to role grants."""
from html import escape
from aiogram import F, Router
from aiogram.types import InlineKeyboardButton as Button, InlineKeyboardMarkup as Keyboard
from sqlalchemy import select, func
from database.models import User, BotInteraction, PurchaseRequest, Laptop, BranchInventory
from database.session import AsyncSessionLocal
from bot.handlers.role_panels import _is_admin, _is_staff, _can_edit, _admin_menu, _staff_menu, panel_menu
from bot.services.local_time import format_local, period_bounds
from bot.services.entry_reports import LABELS
from bot.services.sales_contact import telegram_chat_url

router = Router()
PAGE_SIZE = 5


def report_keyboard(kind, period, page, total, users, include_crm=False):
    buttons = [[Button(text=label, callback_data=f'nav:report:{kind}:{key}:0') for key, label in LABELS.items()]]
    buttons += [[Button(text=f'👤 {u.first_name or u.full_name or u.username or u.telegram_id}'[:64], callback_data=f'nav:customer:{u.id}:{kind}:{period}:{page}')] for u in users]
    nav = []
    if page:
        nav.append(Button(text='⬅️ قبلی', callback_data=f'nav:report:{kind}:{period}:{page-1}'))
    if (page + 1) * PAGE_SIZE < total:
        nav.append(Button(text='بعدی ➡️', callback_data=f'nav:report:{kind}:{period}:{page+1}'))
    if nav:
        buttons.append(nav)
    if include_crm:
        buttons.append([Button(text='🔙 داشبورد CRM', callback_data='dash:crm:0:daily')])
    buttons += [[Button(text='👤 کاربران جدید', callback_data='nav:report:new:daily:0'), Button(text='📈 گزارش ورودی‌ها', callback_data='nav:report:visits:daily:0')], [Button(text='🏠 پنل من', callback_data='panel:home')]]
    return Keyboard(inline_keyboard=buttons)


def user_scope(current_user):
    return [] if _is_admin(current_user) else [User.role == 'customer', User.referrer_telegram_id == current_user.telegram_id]


async def report_data(session, current_user, kind, period, page):
    start, end = period_bounds(period)
    conditions = user_scope(current_user)
    if kind == 'new':
        conditions += [User.first_seen_at >= start, User.first_seen_at < end]
    else:
        conditions += [User.telegram_id.in_(select(BotInteraction.telegram_id).where(BotInteraction.created_at >= start, BotInteraction.created_at < end))]
    total = await session.scalar(select(func.count(User.id)).where(*conditions)) or 0
    page = min(page, max(0, (total - 1) // PAGE_SIZE))
    users = list((await session.scalars(select(User).where(*conditions).order_by(User.first_seen_at.desc(), User.id).offset(page * PAGE_SIZE).limit(PAGE_SIZE))).all())
    allowed = select(User.telegram_id).where(*user_scope(current_user))
    events = await session.scalar(select(func.count(BotInteraction.id)).where(BotInteraction.telegram_id.in_(allowed), BotInteraction.created_at >= start, BotInteraction.created_at < end)) or 0
    text = f"{'👤 کاربران جدید' if kind == 'new' else '📈 گزارش ورودی‌ها'} · {LABELS[period]}\n\n🗓 {format_local(start)} تا {format_local(end)}\n\n━━━━━━━━━━━━\n👥 تعداد کاربران: {total}\n💬 تعامل‌های ثبت‌شده در بازه: {events}\n━━━━━━━━━━━━\n\nبرای دیدن مشخصات و عملکرد، نام فرد را انتخاب کنید.\nصفحهٔ {page + 1}"
    if not users:
        text += '\nموردی در این بازه ثبت نشده است.'
    return text, report_keyboard(kind, period, page, total, users, include_crm=_is_admin(current_user))


@router.message(F.text.in_({'👤 کاربران جدید', '📈 گزارش ورودی‌ها', '👤 مشتریان جدید من', '📈 گزارش ورودی‌های من'}))
async def open_report(message, state, current_user):
    if not _is_staff(current_user):
        await message.answer('دسترسی مجاز نیست.')
        return
    await state.clear()
    kind = 'new' if 'جدید' in message.text else 'visits'
    async with AsyncSessionLocal() as session:
        text, keyboard = await report_data(session, current_user, kind, 'daily', 0)
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith('nav:report:'))
async def report_page(callback, state, current_user):
    if not _is_staff(current_user):
        await callback.answer('دسترسی مجاز نیست.', show_alert=True)
        return
    try:
        _, _, kind, period, raw_page = callback.data.split(':')
        page = int(raw_page)
        if kind not in {'new', 'visits'} or period not in LABELS or page < 0:
            raise ValueError
    except (ValueError, TypeError):
        await callback.answer('درخواست نامعتبر است.', show_alert=True)
        return
    await state.clear()
    async with AsyncSessionLocal() as session:
        text, keyboard = await report_data(session, current_user, kind, period, page)
    await callback.answer()
    await callback.message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith('nav:customer:'))
async def customer_detail(callback, current_user):
    if not _is_staff(current_user):
        await callback.answer('دسترسی مجاز نیست.', show_alert=True)
        return
    try:
        _, _, raw_id, kind, period, raw_page = callback.data.split(':')
        user_id, page = int(raw_id), int(raw_page)
        if period not in LABELS or kind not in {'new', 'visits'} or page < 0:
            raise ValueError
    except (ValueError, TypeError):
        await callback.answer('درخواست نامعتبر است.', show_alert=True)
        return
    start, end = period_bounds(period)
    async with AsyncSessionLocal() as session:
        user = await session.scalar(select(User).where(User.id == user_id, *user_scope(current_user)))
        if not user:
            await callback.answer('این مشتری در محدودهٔ دسترسی شما نیست.', show_alert=True)
            return
        counts = dict((await session.execute(select(BotInteraction.action, func.count(BotInteraction.id)).where(BotInteraction.telegram_id == user.telegram_id, BotInteraction.created_at >= start, BotInteraction.created_at < end).group_by(BotInteraction.action))).all())
        last = await session.scalar(select(func.max(BotInteraction.created_at)).where(BotInteraction.telegram_id == user.telegram_id))
        orders = await session.scalar(select(func.count(PurchaseRequest.id)).where(PurchaseRequest.customer_telegram_id == user.telegram_id, PurchaseRequest.created_at >= start, PurchaseRequest.created_at < end)) or 0
    text = f"👤 <b>{escape(user.first_name or user.full_name or 'بدون نام')}</b>\n━━━━━━━━━━━━\nشناسه: <code>{user.telegram_id}</code>\n📱 {escape(user.phone_number or 'شماره ثبت نشده')}\nورود: {format_local(user.first_seen_at)}\nآخرین فعالیت: {format_local(last)}\nمعرف: <code>{user.referrer_telegram_id or '-'}</code>\n━━━━━━━━━━━━\n📊 عملکرد {LABELS[period]}\nتعامل‌ها: {sum(counts.values())}\nمدل و مشخصات: {counts.get('product', 0)}\nشعب: {counts.get('branch', 0)}\nجستجو: {counts.get('search', 0)}\nسفارش‌های ثبت‌شده: {orders}"
    keyboard = Keyboard(inline_keyboard=[[Button(text='💬 چت تلگرام', url=telegram_chat_url(user))], [Button(text='🔙 گزارش', callback_data=f'nav:report:{kind}:{period}:{page}')], [Button(text='🏠 پنل من', callback_data='panel:home')]])
    await callback.answer()
    await callback.message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data == 'panel:home')
async def panel_home(callback, state, current_user):
    if not _is_staff(current_user):
        await callback.answer('دسترسی مجاز نیست.', show_alert=True)
        return
    await state.clear()
    await callback.answer()
    await callback.message.answer('پنل شما', reply_markup=panel_menu(current_user))


@router.callback_query(F.data.startswith('panel:editmodel:'))
async def edit_configurations(callback, state, current_user):
    if not _can_edit(current_user):
        await callback.answer('دسترسی مجاز نیست.', show_alert=True)
        return
    try:
        parts = callback.data.split(':')
        laptop_id, page = int(parts[2]), int(parts[3])
        mode = parts[4] if len(parts) == 5 else 'all'
        if len(parts) not in (4, 5) or page < 0 or mode not in {'all', 'stock'}:
            raise ValueError
    except (ValueError, TypeError, IndexError):
        await callback.answer('درخواست نامعتبر است.', show_alert=True)
        return
    await state.clear()
    async with AsyncSessionLocal() as session:
        model = await session.get(Laptop, laptop_id)
        if not model:
            await callback.answer('مدل پیدا نشد.', show_alert=True)
            return
        conditions = [Laptop.brand_id == model.brand_id, Laptop.model == model.model, Laptop.status == 'active']
        if mode == 'stock':
            conditions += [Laptop.id.in_(select(BranchInventory.laptop_id).where(BranchInventory.quantity > BranchInventory.reserved_count))]
        total = await session.scalar(select(func.count(Laptop.id)).where(*conditions)) or 0
        page = min(page, max(0, (total - 1) // 8))
        products = list((await session.scalars(select(Laptop).where(*conditions).order_by(Laptop.id).offset(page * 8).limit(8))).all())
    rows = [[Button(text=f"{p.cpu or '-'} | {p.ram or '-'} | {p.storage or '-'} | #{p.id}"[:64], callback_data=f'panel:edit:{p.id}')] for p in products]
    rows.insert(0, [Button(text='همه کانفیگ‌ها', callback_data=f'panel:editmodel:{laptop_id}:0:all'), Button(text='📦 فقط موجود', callback_data=f'panel:editmodel:{laptop_id}:0:stock')])
    nav = []
    if page:
        nav.append(Button(text='قبلی', callback_data=f'panel:editmodel:{laptop_id}:{page-1}:{mode}'))
    if (page + 1) * 8 < total:
        nav.append(Button(text='بعدی', callback_data=f'panel:editmodel:{laptop_id}:{page+1}:{mode}'))
    if nav:
        rows.append(nav)
    rows += [[Button(text='🔙 مدل‌ها', callback_data=f'panel:editbrand:{model.brand_id}:0')], [Button(text='🏠 پنل من', callback_data='panel:home')]]
    await callback.answer()
    await callback.message.answer(f'✏️ <b>{escape(model.model)}</b>\n\nکانفیگ موردنظر را انتخاب کنید.\nفیلتر: {"فقط موجود" if mode == "stock" else "همه کانفیگ‌ها"}\nتعداد: {total}', reply_markup=Keyboard(inline_keyboard=rows))

from html import escape
from aiogram import Router, F
from aiogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup as K
from sqlalchemy import select, func, or_
from database.models import User, Branch, BranchInventory, Laptop, BotInteraction, PurchaseRequest, StaffActivity, InventoryAuditLog
from database.session import AsyncSessionLocal
from bot.handlers.role_panels import _is_admin, _is_staff
from bot.services.local_time import period_bounds
from bot.services.entry_reports import LABELS
from bot.services.sales_contact import telegram_chat_url

router = Router()
ACTIONS = {'📊 عملکرد کارشناسان شعبه': 'staff', '📦 مدل‌ها و موجودی شعبه': 'stock', '📦 موجودی شعبه': 'stock', '💻 مدل‌های شعبه': 'models', '📋 رزروها و سفارش‌های شعبه': 'orders', '📥 گزارش عملیات شعبه': 'operations'}


def allowed_branch(user, branch_id):
    if getattr(user, 'view_panel', None) == 'branch_manager':
        return _is_staff(user) and user.managed_branch_id == branch_id
    return _is_staff(user) and (_is_admin(user) or (user.role == 'branch_manager' and user.managed_branch_id == branch_id))


async def manager_data(session, user, branch_id, kind, period='daily', page=0):
    if not allowed_branch(user, branch_id):
        raise PermissionError
    branch = await session.get(Branch, branch_id)
    if not branch or not branch.is_active or kind not in {'staff', 'stock', 'models', 'orders', 'operations'} or period not in LABELS or page < 0:
        raise ValueError
    start, end = period_bounds(period)
    title = {'staff':'عملکرد کارشناسان', 'stock':'موجودی', 'models':'مدل‌ها', 'orders':'رزروها و سفارش‌ها', 'operations':'گزارش عملیات'}[kind]
    text = f'🏢 <b>{escape(branch.name)} · {title}</b>\n\n'
    rows = []
    if kind == 'staff':
        query = select(User).where(User.managed_branch_id == branch_id, User.role == 'seller')
        total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
        page = min(page, max(0, (total - 1) // 8))
        staff = list((await session.scalars(query.order_by(User.id).offset(page*8).limit(8))).all())
        text += f'بازه: {LABELS[period]}\n'
        for expert in staff:
            customers = select(User.telegram_id).where(User.role == 'customer', User.referrer_telegram_id == expert.telegram_id)
            new = await session.scalar(select(func.count(User.id)).where(User.referrer_telegram_id == expert.telegram_id, User.role == 'customer', User.first_seen_at >= start, User.first_seen_at < end)) or 0
            visits = await session.scalar(select(func.count(func.distinct(BotInteraction.telegram_id))).where(BotInteraction.telegram_id.in_(customers), BotInteraction.created_at >= start, BotInteraction.created_at < end)) or 0
            orders = await session.scalar(select(func.count(PurchaseRequest.id)).where(PurchaseRequest.referrer_telegram_id == expert.telegram_id, PurchaseRequest.created_at >= start, PurchaseRequest.created_at < end)) or 0
            edits = await session.scalar(select(func.count(StaffActivity.id)).where(StaffActivity.actor_telegram_id == expert.telegram_id, StaffActivity.action == 'product_edit', StaffActivity.created_at >= start, StaffActivity.created_at < end)) or 0
            text += f'\n━━━━━━━━━━━━\n👤 {escape(expert.full_name or expert.username or str(expert.telegram_id))}\nحساب: {"فعال" if expert.is_active else "غیرفعال"}\nمشتری جدید: {new} · مشتری مراجعه‌کننده: {visits}\nدرخواست خرید ارجاعی: {orders} · ویرایش ثبت‌شده: {edits}\n'
            rows.append([B(text=f'💬 {expert.full_name or expert.username or expert.telegram_id}'[:64], url=telegram_chat_url(expert))])
        if not staff:
            text += '\nکارشناسی به این شعبه منتسب نشده؛ ادمین باید شعبهٔ کارشناس را ثبت کند.'
        text += '\nدرخواست خرید، فروش قطعی محسوب نمی‌شود.'
        rows.insert(0, [B(text=label, callback_data=f'mgr:{kind}:{branch_id}:0:{key}') for key, label in LABELS.items()])
    elif kind in {'orders', 'operations'}:
        if kind == 'orders':
            query = select(PurchaseRequest).where(PurchaseRequest.branch_id == branch_id, PurchaseRequest.created_at >= start, PurchaseRequest.created_at < end)
            order_by = PurchaseRequest.created_at.desc()
        else:
            query = select(InventoryAuditLog).where(or_(InventoryAuditLog.source_branch_id == branch_id, InventoryAuditLog.destination_branch_id == branch_id), InventoryAuditLog.timestamp >= start, InventoryAuditLog.timestamp < end)
            order_by = InventoryAuditLog.timestamp.desc()
        total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
        page = min(page, max(0, (total-1)//8))
        entries = list((await session.scalars(query.order_by(order_by).offset(page*8).limit(8))).all())
        text += f'بازه: {LABELS[period]} · تعداد: {total}\n'
        from bot.services.local_time import format_local
        for entry in entries:
            if kind == 'orders':
                laptop = await session.get(Laptop, entry.laptop_id)
                expert = await session.scalar(select(User).where(User.telegram_id == entry.referrer_telegram_id)) if entry.referrer_telegram_id else None
                status = {'pending':'در انتظار بررسی', 'confirmed':'تأییدشده', 'cancelled':'لغوشده'}.get(entry.status, entry.status)
                text += f'\n━━━━━━━━━━━━\nدرخواست #{entry.id} · {escape(status)}\nمشتری: {escape(entry.customer_name)}\nمدل: {escape(laptop.model if laptop else str(entry.laptop_id))} · تعداد: {entry.quantity}\nمعرف: {escape((expert.full_name or expert.username or str(expert.telegram_id)) if expert else "بدون معرف ثبت‌شده")}\n{format_local(entry.created_at)}\n'
            else:
                text += f'\n━━━━━━━━━━━━\nعملیات #{entry.id} · {escape(entry.change_type)}\nکالا #{entry.laptop_id} · تعداد: {entry.quantity}\n{format_local(entry.timestamp)}\n'
        if not entries:
            text += '\nرکوردی در این بازه ثبت نشده است.'
        text += '\n\nاین گزارش از رکوردهای ثبت‌شده است؛ تأیید درخواست به معنی پرداخت بیعانه یا تحویل نیست. ثبت فاکتور، پرداخت، اقساط و چک هنوز به دفتر مالی متصل نیست.'
        rows.insert(0, [B(text=label, callback_data=f'mgr:{kind}:{branch_id}:0:{key}') for key, label in LABELS.items()])
    else:
        query = select(Laptop, BranchInventory).join(BranchInventory, BranchInventory.laptop_id == Laptop.id).where(BranchInventory.branch_id == branch_id, Laptop.status == 'active', BranchInventory.quantity > 0)
        total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
        page = min(page, max(0, (total-1)//8))
        products = (await session.execute(query.order_by(Laptop.model, Laptop.id).offset(page*8).limit(8))).all()
        for laptop, stock in products:
            text += f'\n{escape(laptop.model)} · #{laptop.id}\n{escape(laptop.cpu or "—")} | {escape(laptop.ram or "—")} | {escape(laptop.storage or "—")}\nموجودی: {stock.quantity} · رزرو: {stock.reserved_count} · قابل فروش: {max(0, stock.quantity-stock.reserved_count)}\n'
            rows.append([B(text=f'💻 {laptop.model}'[:64], callback_data=f'tree:laptop:{laptop.id}')])
        if not products:
            text += 'کالای موجودی برای این شعبه ثبت نشده است.'
    nav = []
    if page:
        nav.append(B(text='قبلی', callback_data=f'mgr:{kind}:{branch_id}:{page-1}:{period}'))
    if (page+1)*8 < total:
        nav.append(B(text='بعدی', callback_data=f'mgr:{kind}:{branch_id}:{page+1}:{period}'))
    if nav:
        rows.append(nav)
    rows.append([B(text='🔄 تازه‌سازی', callback_data=f'mgr:{kind}:{branch_id}:{page}:{period}')])
    return text + f'\nصفحهٔ {page+1}', K(inline_keyboard=rows)


@router.message(F.text.in_(ACTIONS))
async def manager_section(message, state, current_user):
    if not _is_staff(current_user) or not (_is_admin(current_user) or current_user.role == 'branch_manager'):
        await message.answer('دسترسی مجاز نیست.')
        return
    await state.clear()
    kind = ACTIONS[message.text]
    async with AsyncSessionLocal() as session:
        if not current_user.managed_branch_id:
            await message.answer('ابتدا ادمین باید شعبهٔ شما را مشخص کند.')
            return
        try:
            text, keyboard = await manager_data(session, current_user, current_user.managed_branch_id, kind)
        except (ValueError, PermissionError):
            await message.answer('شعبهٔ فعال و مجاز پیدا نشد.')
            return
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith('mgr:'))
async def manager_callback(callback, current_user):
    try:
        prefix, kind, branch_id, page, period = callback.data.split(':')
        async with AsyncSessionLocal() as session:
            text, keyboard = await manager_data(session, current_user, int(branch_id), kind, period, int(page))
    except (ValueError, PermissionError):
        await callback.answer('شعبه یا دسترسی معتبر نیست.', show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(text, reply_markup=keyboard)

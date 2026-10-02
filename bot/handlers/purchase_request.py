from __future__ import annotations

from html import escape
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from bot.config import get_settings
from bot.services.inventory_service import InventoryService
from database.models import Branch, Laptop, LaptopVariant, PurchaseRequest, User
from database.session import AsyncSessionLocal

router = Router()


class OrderStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_phone = State()
    waiting_for_branch = State()
    waiting_for_notes = State()


@router.callback_query(F.data.startswith("order:laptop:"))
async def start_laptop_order(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        laptop_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer("شناسه محصول نامعتبر است.", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        statement = select(Laptop).options(joinedload(Laptop.brand)).where(Laptop.id == laptop_id)
        laptop = await session.scalar(statement)
        if not laptop:
            await callback.answer("محصول یافت نشد.", show_alert=True)
            return

        brand_name = laptop.brand.name if laptop.brand else ""
        item_title = f"{brand_name} {laptop.model}"
        specs = f"پردازنده: {laptop.cpu or '-'} | رم: {laptop.ram or '-'} | گرافیک: {laptop.gpu or '-'} | حافظه: {laptop.storage or '-'}"
        price_val = laptop.price or 0
        price_str = f"{price_val:,} تومان" if price_val > 0 else "تماس بگیرید"

    await state.clear()
    await state.set_state(OrderStates.waiting_for_name)
    await state.update_data(
        order_laptop_id=laptop.id,
        order_variant_id=None,
        order_title=item_title,
        order_specs=specs,
        order_price=price_str,
    )

    await callback.message.answer(
        f"🛍 <b>ثبت درخواست خرید لپ‌تاپ</b>\n\n"
        f"💻 <b>کالا:</b> {escape(item_title)}\n"
        f"⚙️ <b>مشخصات:</b> {escape(specs)}\n"
        f"💰 <b>قیمت:</b> {price_str}\n\n"
        f"لطفاً <b>نام و نام خانوادگی</b> خود را ارسال فرمایید:",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("order:variant:"))
async def start_variant_order(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        variant_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer("شناسه محصول نامعتبر است.", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        service = InventoryService(session)
        details = await service.catalog_variant_details(variant_id)

    if not details:
        await callback.answer("اطلاعات محصول یافت نشد.", show_alert=True)
        return

    variant = details["variant"]
    legacy_laptop_id = variant.legacy_laptop_id
    if not legacy_laptop_id:
        async with AsyncSessionLocal() as session:
            db_var = await session.get(LaptopVariant, variant_id)
            legacy_laptop_id = db_var.legacy_laptop_id if db_var else None

    if not legacy_laptop_id:
        await callback.answer("این مدل در سیستم برای سفارش فعال نیست.", show_alert=True)
        return

    item_title = f"{details['brand']} {details['series']} {details['model']}"
    specs = f"پردازنده: {variant.cpu or '-'} | رم: {variant.ram or '-'} | گرافیک: {variant.gpu or '-'} | حافظه: {variant.storage or '-'}"
    price_val = variant.price or 0
    price_str = f"{price_val:,} تومان" if price_val > 0 else "تماس بگیرید"

    await state.clear()
    await state.set_state(OrderStates.waiting_for_name)
    await state.update_data(
        order_laptop_id=legacy_laptop_id,
        order_variant_id=variant_id,
        order_title=item_title,
        order_specs=specs,
        order_price=price_str,
    )

    await callback.message.answer(
        f"🛍 <b>ثبت درخواست خرید لپ‌تاپ</b>\n\n"
        f"💻 <b>کالا:</b> {escape(item_title)}\n"
        f"⚙️ <b>مشخصات:</b> {escape(specs)}\n"
        f"💰 <b>قیمت:</b> {price_str}\n\n"
        f"لطفاً <b>نام و نام خانوادگی</b> خود را ارسال فرمایید:",
    )
    await callback.answer()


@router.message(OrderStates.waiting_for_name)
async def process_order_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if len(name) < 3:
        await message.answer("لطفاً نام و نام خانوادگی معتبر وارد نمایید:")
        return

    await state.update_data(order_customer_name=name)
    await state.set_state(OrderStates.waiting_for_phone)
    await message.answer(
        f"متشکرم {escape(name)} عزیز.\n"
        f"لطفاً <b>شماره تماس همراه</b> خود را جهت هماهنگی کارشناسان ارسال فرمایید (مثال: 09123456789):"
    )


@router.message(OrderStates.waiting_for_phone)
async def process_order_phone(message: Message, state: FSMContext) -> None:
    phone = (message.text or "").strip()
    clean_digits = "".join(ch for ch in phone if ch.isdigit())
    if len(clean_digits) < 10:
        await message.answer("شماره تماس نامعتبر است. لطفاً حداقل ۱۰ رقم وارد کنید:")
        return

    await state.update_data(order_customer_phone=phone)

    # انتخاب شعبه
    async with AsyncSessionLocal() as session:
        branches = list((await session.scalars(
            select(Branch).where(Branch.is_active.is_(True)).order_by(Branch.name)
        )).all())

    buttons = [
        [InlineKeyboardButton(text=f"📍 {b.name}", callback_data=f"order_branch:{b.id}")]
        for b in branches
    ]
    buttons.append([InlineKeyboardButton(text="🚚 ارسال پستی / پیک (تحویل درب منزل)", callback_data="order_branch:0")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await state.set_state(OrderStates.waiting_for_branch)
    await message.answer("شعبه تحویل حضوری یا نحوه دریافت را انتخاب کنید:", reply_markup=keyboard)


@router.callback_query(OrderStates.waiting_for_branch, F.data.startswith("order_branch:"))
async def process_order_branch(callback: CallbackQuery, state: FSMContext) -> None:
    branch_id_raw = int(callback.data.rsplit(":", 1)[1])
    branch_id = branch_id_raw if branch_id_raw > 0 else None

    branch_name = "ارسال پستی / پیک"
    if branch_id:
        async with AsyncSessionLocal() as session:
            b = await session.get(Branch, branch_id)
            if b:
                branch_name = b.name

    await state.update_data(order_branch_id=branch_id, order_branch_name=branch_name)
    await state.set_state(OrderStates.waiting_for_notes)

    skip_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="ندارم (ثبت نهایی)", callback_data="order_notes:skip")]
    ])
    await callback.message.edit_text(
        f"محل تحویل انتخابی: <b>{escape(branch_name)}</b>\n\n"
        f"اگر توضیح یا درخواستی درباره فاکتور، زمان تحویل یا موارد خاص دارید بفرمایید، یا دکمه «ندارم» را بزنید:",
        reply_markup=skip_kb,
    )
    await callback.answer()


@router.callback_query(OrderStates.waiting_for_notes, F.data == "order_notes:skip")
async def process_order_notes_skip(callback: CallbackQuery, state: FSMContext) -> None:
    await _finalize_order(callback.message, state, notes="بدون یادداشت", customer_tg_id=callback.from_user.id)
    await callback.answer()


@router.message(OrderStates.waiting_for_notes)
async def process_order_notes_text(message: Message, state: FSMContext) -> None:
    await _finalize_order(message, state, notes=message.text or "", customer_tg_id=message.from_user.id)


async def _finalize_order(
    message: Message,
    state: FSMContext,
    notes: str,
    customer_tg_id: int,
) -> None:
    data = await state.get_data()
    laptop_id = int(data["order_laptop_id"])
    customer_name = str(data["order_customer_name"])
    customer_phone = str(data["order_customer_phone"])
    branch_id = data.get("order_branch_id")
    branch_name = data.get("order_branch_name", "ثبت نشده")
    item_title = data.get("order_title", "")
    item_specs = data.get("order_specs", "")
    item_price = data.get("order_price", "")

    async with AsyncSessionLocal() as session:
        customer = await session.scalar(select(User).where(User.telegram_id == customer_tg_id))
        referrer_id = customer.referrer_telegram_id if customer else None
        admin_ids = set((await session.scalars(select(User.telegram_id).where(User.role == "admin", User.is_active.is_(True)))).all())
        req = PurchaseRequest(
            customer_telegram_id=customer_tg_id,
            customer_name=customer_name,
            customer_phone=customer_phone,
            referrer_telegram_id=referrer_id,
            laptop_id=laptop_id,
            branch_id=branch_id,
            quantity=1,
            status="pending",
            notes=notes,
            created_at=datetime.utcnow(),
        )
        session.add(req)
        await session.commit()
        order_id = req.id

    await state.clear()

    # پیام تأیید به مشتری
    await message.answer(
        f"✅ <b>درخواست خرید و رزرو شما با موفقیت ثبت شد!</b>\n\n"
        f"کد رهگیری سفارش: <code>#{order_id}</code>\n"
        f"💻 محصول: <b>{escape(item_title)}</b>\n"
        f"⚙️ مشخصات: {escape(item_specs)}\n"
        f"💰 قیمت: {escape(item_price)}\n"
        f"📍 محل تحویل: {escape(branch_name)}\n"
        f"📞 شماره تماس: {escape(customer_phone)}\n\n"
        f"همکاران بخش فروش راینوتک به زودی با شما تماس خواهند گرفت. با تشکر از انتخاب شما 🙏"
    )

    # اطلاع‌رسانی به مدیران و مسئول شعبه
    settings = get_settings()
    admin_text = (
        f"🔔 <b>درخواست خرید جدید ثبت شد! (#ORD-{order_id})</b>\n\n"
        f"👤 مشتری: <b>{escape(customer_name)}</b> (شناسه: <code>{customer_tg_id}</code>)\n"
        f"📞 تماس: <code>{escape(customer_phone)}</code>\n"
        f"💻 کالا: <b>{escape(item_title)}</b>\n"
        f"⚙️ مشخصات: {escape(item_specs)}\n"
        f"🏢 شعبه: {escape(branch_name)}\n"
        f"📝 یادداشت مشتری: {escape(notes)}\n"
        f"🔗 کارشناس معرف: <code>{referrer_id or '-'}</code>\n"
    )

    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ تایید و هماهنگ شد", callback_data=f"ord_act:{order_id}:contacted"),
            InlineKeyboardButton(text="❌ لغو سفارش", callback_data=f"ord_act:{order_id}:cancelled"),
        ]
    ])

    recipients = set(settings.ADMIN_TELEGRAM_IDS) | admin_ids
    if referrer_id:
        recipients.add(referrer_id)
    for admin_id in recipients:
        try:
            await message.bot.send_message(admin_id, admin_text, reply_markup=admin_kb)
        except Exception as e:
            logger.debug(f"عدم امکان ارسال نوتیفیکیشن به ادمین {admin_id}: {e}")


@router.callback_query(F.data.startswith("ord_act:"))
async def handle_order_admin_action(callback: CallbackQuery, current_user: User) -> None:
    if current_user.role not in ("admin", "branch_manager", "seller"):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return

    parts = callback.data.split(":")
    order_id = int(parts[1])
    new_status = parts[2]
    if new_status not in {"contacted", "completed", "cancelled", "pending"}:
        await callback.answer("وضعیت نامعتبر است.", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        from database.models import StaffActivity
        order = await session.get(PurchaseRequest, order_id)
        if not order:
            await callback.answer("سفارش یافت نشد.", show_alert=True)
            return
        order.status = new_status
        session.add(StaffActivity(actor_telegram_id=current_user.telegram_id, action="order_status", target_id=order_id, detail=new_status))
        await session.commit()

    status_labels = {
        "contacted": "✅ هماهنگ شد",
        "completed": "🎉 تحویل شد",
        "cancelled": "❌ لغو شد",
        "pending": "⏳ در انتظار بررسی",
    }
    label = status_labels.get(new_status, new_status)
    await callback.message.edit_text(
        callback.message.text + f"\n\n<b>وضعیت سفارش بروزرسانی شد: {label}</b>"
    )
    await callback.answer(f"وضعیت به {label} تغییر یافت.")


@router.message(F.text == "🛒 پیگیری سفارش من")
@router.message(Command("my_orders"))
async def my_orders_command(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        orders = list((await session.scalars(
            select(PurchaseRequest)
            .options(joinedload(PurchaseRequest.laptop))
            .where(PurchaseRequest.customer_telegram_id == message.from_user.id)
            .order_by(PurchaseRequest.id.desc())
            .limit(10)
        )).all())

    if not orders:
        await message.answer("شما هنوز سفارش یا درخواست خریدی ثبت نکرده‌اید.")
        return

    status_labels = {
        "pending": "⏳ در حال بررسی کارشناسان",
        "contacted": "📞 در حال هماهنگی / تماس گرفته شده",
        "completed": "✅ تکمیل و تحویل شده",
        "cancelled": "❌ لغو شده",
    }

    lines = ["📋 <b>درخواست‌های خرید اخیر شما:</b>\n"]
    for o in orders:
        model_name = o.laptop.model if o.laptop else "مدل نامشخص"
        status_text = status_labels.get(o.status, o.status)
        date_str = o.created_at.strftime("%Y-%m-%d %H:%M")
        lines.append(
            f"🔹 <b>سفارش #{o.id}</b> | تاریخ: {date_str}\n"
            f"   مدل: {escape(model_name)}\n"
            f"   وضعیت: {status_text}\n"
        )

    await message.answer("\n".join(lines))


@router.message(F.text == "📋 درخواست‌های خرید")
@router.message(Command("orders"))
async def admin_orders_command(message: Message, current_user: User) -> None:
    if current_user.role not in ("admin", "branch_manager", "seller"):
        await message.answer("این بخش مخصوص همکاران و مدیران سیستم است.")
        return

    async with AsyncSessionLocal() as session:
        statement = (
            select(PurchaseRequest)
            .options(joinedload(PurchaseRequest.laptop), joinedload(PurchaseRequest.branch))
            .order_by(PurchaseRequest.id.desc())
            .limit(15)
        )
        if current_user.role == "branch_manager" and current_user.managed_branch_id:
            statement = statement.where(PurchaseRequest.branch_id == current_user.managed_branch_id)
        elif current_user.role == "seller":
            statement = statement.where(PurchaseRequest.referrer_telegram_id == current_user.telegram_id)

        orders = list((await session.scalars(statement)).all())

    if not orders:
        await message.answer("درخواست خریدی در سامانه یافت نشد.")
        return

    status_labels = {
        "pending": "⏳ در انتظار",
        "contacted": "📞 تماس گرفته شده",
        "completed": "✅ تکمیل",
        "cancelled": "❌ لغو",
    }

    lines = ["📋 <b>آخرین سفارشات و درخواست‌های خرید مشتریان:</b>\n"]
    for o in orders:
        model_name = o.laptop.model if o.laptop else "نامشخص"
        b_name = o.branch.name if o.branch else "ارسال پستی"
        lines.append(
            f"🔸 <b>#{o.id}</b> | {escape(o.customer_name)} (<code>{escape(o.customer_phone)}</code>)\n"
            f"   💻 {escape(model_name)} | 📍 {escape(b_name)}\n"
            f"   معرف: <code>{o.referrer_telegram_id or '-'}</code> | وضعیت: {status_labels.get(o.status, o.status)}\n"
        )

    await message.answer("\n".join(lines))


@router.message(F.text == "📋 رزروهای ارجاعی من")
async def referred_orders(message: Message, current_user: User) -> None:
    if current_user.role not in {"seller", "branch_manager", "admin"} and current_user.telegram_id not in get_settings().ADMIN_TELEGRAM_IDS:
        await message.answer("دسترسی مجاز نیست.")
        return
    async with AsyncSessionLocal() as session:
        orders = list((await session.scalars(
            select(PurchaseRequest).options(joinedload(PurchaseRequest.laptop), joinedload(PurchaseRequest.branch))
            .where(PurchaseRequest.referrer_telegram_id == current_user.telegram_id)
            .order_by(PurchaseRequest.id.desc()).limit(15)
        )).all())
    if not orders:
        await message.answer("هنوز رزروی از لینک معرفی شما ثبت نشده است.")
        return
    lines = ["📋 <b>رزروهای ارجاعی شما:</b>"]
    for order in orders:
        lines.append(f"#{order.id} · {escape(order.customer_name)} · <code>{escape(order.customer_phone)}</code>\n"
                     f"💻 {escape(order.laptop.model if order.laptop else 'نامشخص')} · "
                     f"{escape(order.branch.name if order.branch else 'ارسال')}\n"
                     f"⚙️ {escape(order.laptop.cpu or '-' if order.laptop else '-')} / {escape(order.laptop.ram or '-' if order.laptop else '-')} · "
                     f"💰 {(order.laptop.price or 0) if order.laptop else 0:,} تومان\n"
                     f"📝 {escape(order.notes or '-')} · {order.created_at:%Y-%m-%d %H:%M} · {escape(order.status)}")
    await message.answer("\n\n".join(lines))


@router.message(F.text == "📋 رزروهای مشتریان")
async def all_reservations(message: Message, current_user: User) -> None:
    if current_user.role != "admin" and current_user.telegram_id not in get_settings().ADMIN_TELEGRAM_IDS:
        await message.answer("دسترسی مجاز نیست.")
        return
    async with AsyncSessionLocal() as session:
        orders = list((await session.scalars(
            select(PurchaseRequest).options(joinedload(PurchaseRequest.laptop), joinedload(PurchaseRequest.branch))
            .order_by(PurchaseRequest.id.desc()).limit(15)
        )).all())
    if not orders:
        await message.answer("رزروی ثبت نشده است.")
        return
    lines = ["📋 <b>آخرین رزروهای مشتریان:</b>"]
    for order in orders:
        lines.append(f"#{order.id} · {escape(order.customer_name)} · <code>{escape(order.customer_phone)}</code>\n"
                     f"💻 {escape(order.laptop.model if order.laptop else 'نامشخص')} · "
                     f"{escape(order.branch.name if order.branch else 'ارسال')}\n"
                     f"⚙️ {escape(order.laptop.cpu or '-' if order.laptop else '-')} / {escape(order.laptop.ram or '-' if order.laptop else '-')} · "
                     f"💰 {(order.laptop.price or 0) if order.laptop else 0:,} تومان\n"
                     f"🔗 معرف: <code>{order.referrer_telegram_id or '-'}</code> · "
                     f"📝 {escape(order.notes or '-')} · {order.created_at:%Y-%m-%d %H:%M} · {escape(order.status)}")
    await message.answer("\n\n".join(lines))

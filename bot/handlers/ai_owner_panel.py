"""Private developer-owned operations for RAINOTEK AI and VPN orders."""

from __future__ import annotations

from io import BytesIO
from html import escape
from datetime import datetime, timedelta
from bot.services.local_time import format_local, period_bounds
from urllib.parse import urlparse

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.filters.command import CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from loguru import logger
from sqlalchemy import func, select, update

from bot.config import get_settings
from bot.handlers.vpn_shop import SOURCE_PRICES, _deliver_available_config, money, review_vpn_order
from database.models import AiApiInquiry, AiFeatureVisit, PremiumOrder, PremiumPlan, User, VpnConfig, VpnOrder
from database.session import AsyncSessionLocal

router = Router()
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")


class ConfigEntry(StatesGroup):
    waiting_for_link = State()
    waiting_for_ssh = State()


def _owner(user) -> bool:
    owner_id = get_settings().AI_OWNER_TELEGRAM_ID
    return bool(user and owner_id and user.id == owner_id)


def _keyboard(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=data) for label, data in row] for row in rows
    ])


PROJECTS = _keyboard([[("🏢 راینوتک", "ai:project:rainotek")]])

HOME = _keyboard([
    [("🧾 سفارش‌های در انتظار", "ai:orders"), ("📊 وضعیت اتصال امن", "ai:dashboard")],
    [("👥 CRM مشتریان", "ai:crm:all:0"), ("🤖 ورودی‌های هوش مصنوعی", "ai:crm:ai:0")],
    [("🛍 خریداران اشتراک", "ai:crm:buyers:0")],
    [("🧩 درخواست‌های API", "ai:api_inquiries")],
    [("🛍 سفارش‌های اشتراک پرمیوم", "ai:premium:orders")],
    [("🔐 اکانت‌های نگه‌داری‌شده", "ai:held:list")],
    [("🔗 افزودن لینک اختصاصی", "ai:config:select")],
    [("🔙 پروژه‌ها", "ai:projects")],
])


@router.message(Command("start"))
async def owner_start(message: Message) -> None:
    if not _owner(message.from_user):
        await message.answer("دسترسی به پنل مدیریتی راینوتک مجاز نیست.")
        return
    await message.answer(
        "🧠 پنل مرکزی AI Developer\nپروژهٔ مورد نظر را انتخاب کنید. هر پروژه در این پنل بخش مدیریتی جداگانه دارد.",
        reply_markup=PROJECTS,
    )


@router.callback_query(F.data == "ai:projects")
async def owner_projects(callback: CallbackQuery) -> None:
    if not _owner(callback.from_user):
        await callback.answer("دسترسی مجاز نیست.", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer("🧠 پروژه‌های متصل به پنل مرکزی", reply_markup=PROJECTS)


@router.callback_query(F.data == "ai:project:rainotek")
async def owner_rainotek(callback: CallbackQuery) -> None:
    if not _owner(callback.from_user):
        await callback.answer("دسترسی مجاز نیست.", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer("🏢 مدیریت راینوتک\nسفارش‌ها و موجودی اتصال امن", reply_markup=HOME)


@router.callback_query(F.data == "ai:dashboard")
async def owner_dashboard(callback: CallbackQuery) -> None:
    if not _owner(callback.from_user):
        await callback.answer("دسترسی مجاز نیست.", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        start, end = period_bounds()
        crm_total = await session.scalar(select(func.count(User.id)).where(User.role == "customer", User.phone_number.is_not(None))) or 0
        crm_today = await session.scalar(select(func.count(User.id)).where(User.role == "customer", User.phone_number.is_not(None), User.joined_at >= start, User.joined_at < end)) or 0
        ai_total = await session.scalar(select(func.count(func.distinct(AiFeatureVisit.telegram_id)))) or 0
        pending = await session.scalar(select(func.count(VpnOrder.id)).where(VpnOrder.status == "pending_review")) or 0
        approved = await session.scalar(select(func.count(VpnOrder.id)).where(VpnOrder.status == "approved")) or 0
        delivered = await session.scalar(select(func.count(VpnOrder.id)).where(VpnOrder.status == "delivered")) or 0
        stock = await session.scalar(select(func.count(VpnConfig.id)).where(VpnConfig.status == "available")) or 0
        held = await session.scalar(select(func.count(VpnConfig.id)).where(VpnConfig.status == "held")) or 0
    await callback.answer()
    await callback.message.answer(
        f"📊 وضعیت راینوتک\nمشتریان CRM: {crm_total}\nورودی امروز: {crm_today}\n"
        f"بازدیدکنندگان ثبت‌شدهٔ AI: {ai_total}\n\nرسیدهای در انتظار بررسی: {pending}\n"
        f"پرداخت تأییدشده، منتظر تحویل: {approved}\nتحویل‌شده: {delivered}\n"
        f"لینک‌های آزاد: {stock}\nاکانت‌های نگه‌داری‌شده: {held}",
        reply_markup=HOME,
    )


@router.callback_query(F.data == "ai:api_inquiries")
async def owner_api_inquiries(callback: CallbackQuery) -> None:
    if not _owner(callback.from_user):
        await callback.answer("دسترسی مجاز نیست.", show_alert=True)
        return
    from bot.handlers.ai_hub import API_PRODUCTS
    async with AsyncSessionLocal() as session:
        inquiries = (await session.scalars(select(AiApiInquiry).where(
            AiApiInquiry.status == "awaiting_quote"
        ).order_by(AiApiInquiry.id.desc()).limit(20))).all()
        ids = {item.customer_telegram_id for item in inquiries}
        users = (await session.scalars(select(User).where(User.telegram_id.in_(ids)))).all() if ids else []
    names = {user.telegram_id: user for user in users}
    await callback.answer()
    if not inquiries:
        await callback.message.answer("هنوز درخواست API در انتظار استعلام ثبت نشده است.", reply_markup=HOME)
        return
    lines = ["🧩 <b>درخواست‌های API در انتظار استعلام</b>"]
    for item in inquiries:
        user = names.get(item.customer_telegram_id)
        label = API_PRODUCTS.get(item.provider_key, (item.provider_key, ""))[0]
        contact = f" · {escape(user.phone_number)}" if user and user.phone_number else ""
        lines.append(f"#{item.id} · {escape(label)} · <a href=\"tg://user?id={item.customer_telegram_id}\">مشتری {item.customer_telegram_id}</a>{contact}")
    await callback.message.answer("\n".join(lines), reply_markup=HOME)


@router.message(Command("premium_plan_set"))
async def owner_premium_plan_set(message: Message, command: CommandObject) -> None:
    """Enter supplier-verified price and stock; never infer them from another shop."""
    if not _owner(message.from_user):
        await message.answer("دسترسی مجاز نیست.")
        return
    parts = [part.strip() for part in (command.args or "").split("|", 5)]
    if len(parts) < 5:
        await message.answer("روش ثبت: /premium_plan_set کد|دسته|عنوان|قیمت_پایه_تومان|موجودی|توضیح\nدسته: chat / creative / editing")
        return
    sku, category, title, raw_price, raw_stock = parts[:5]
    description = parts[5] if len(parts) > 5 else ""
    from bot.handlers.premium_shop import CATEGORIES
    try:
        price, stock = int(raw_price), int(raw_stock)
    except ValueError:
        await message.answer("قیمت و موجودی باید عدد باشند.")
        return
    if (not sku or len(sku) > 80 or category not in CATEGORIES or not title or len(title) > 160
            or price <= 0 or stock < 0 or len(description) > 1000):
        await message.answer("اطلاعات پلن نامعتبر است.")
        return
    async with AsyncSessionLocal() as session:
        plan = await session.scalar(select(PremiumPlan).where(PremiumPlan.source_sku == sku))
        if plan is None:
            plan = PremiumPlan(source_sku=sku, category=category, title=title,
                               source_price_toman=price, stock=stock, description=description,
                               checked_at=datetime.utcnow())
            session.add(plan)
        else:
            reserved = await session.scalar(select(func.count(PremiumOrder.id)).where(
                PremiumOrder.plan_id == plan.id,
                PremiumOrder.status.in_(("awaiting_receipt", "pending_review", "approved")),
            )) or 0
            plan.category, plan.title, plan.description = category, title, description
            plan.source_price_toman, plan.stock, plan.checked_at = price, max(0, stock - reserved), datetime.utcnow()
            plan.active = True
        await session.commit()
    from bot.handlers.premium_shop import sale_price
    await message.answer(f"✅ پلن #{plan.id} ثبت شد. قیمت فروش با ۱۰٪ افزایش: {sale_price(price):,} تومان؛ موجودی قابل سفارش: {plan.stock}.")


@router.callback_query(F.data == "ai:premium:orders")
async def owner_premium_orders(callback: CallbackQuery) -> None:
    if not _owner(callback.from_user):
        await callback.answer("دسترسی مجاز نیست.", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        orders = (await session.scalars(select(PremiumOrder).where(
            PremiumOrder.status.in_(("pending_review", "approved"))
        ).order_by(PremiumOrder.id.desc()).limit(20))).all()
    await callback.answer()
    if not orders:
        await callback.message.answer("سفارش اشتراک در انتظار بررسی یا تحویل وجود ندارد.", reply_markup=HOME)
        return
    rows = [[(f"#{o.id} · {o.title_snapshot} · {o.status}", f"ai:premium:order:{o.id}")] for o in orders]
    rows.append([("🔙 پنل راینوتک", "ai:home")])
    await callback.message.answer("🛍 سفارش‌های پرمیوم", reply_markup=_keyboard(rows))


@router.callback_query(F.data.startswith("ai:premium:order:"))
async def owner_premium_order(callback: CallbackQuery, customer_bot) -> None:
    if not _owner(callback.from_user):
        await callback.answer("دسترسی مجاز نیست.", show_alert=True)
        return
    try:
        order_id = int(callback.data.rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("سفارش نامعتبر است.", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        order = await session.get(PremiumOrder, order_id)
    if not order:
        await callback.answer("سفارش پیدا نشد.", show_alert=True)
        return
    await callback.answer()
    if order.receipt_file_id:
        try:
            receipt = await customer_bot.download(order.receipt_file_id)
            file = BufferedInputFile(receipt.read(), filename=f"premium-receipt-{order.id}.jpg")
            if order.receipt_kind == "photo":
                await callback.message.answer_photo(file, caption=f"رسید سفارش #{order.id}")
            else:
                await callback.message.answer_document(file, caption=f"رسید سفارش #{order.id}")
        except Exception as exc:
            logger.warning("Premium receipt fetch failed for order {}: {}", order.id, type(exc).__name__)
            await callback.message.answer("⚠️ تصویر رسید بارگیری نشد؛ تا مشاهده آن را تأیید نکنید.")
            return
    text = f"سفارش #{order.id}\n{escape(order.title_snapshot)}\nمبلغ: {order.price_toman:,} تومان\nمشتری: <code>{order.customer_telegram_id}</code>\nوضعیت: {order.status}"
    rows = []
    if order.status == "pending_review" and order.receipt_file_id:
        rows.append([("✅ تأیید رسید", f"ai:premium:review:{order.id}:approve"),
                     ("❌ رد رسید", f"ai:premium:review:{order.id}:reject")])
    rows.append([("🔙 سفارش‌ها", "ai:premium:orders")])
    await callback.message.answer(text, reply_markup=_keyboard(rows))


@router.callback_query(F.data.startswith("ai:premium:review:"))
async def owner_premium_review(callback: CallbackQuery, customer_bot) -> None:
    if not _owner(callback.from_user):
        await callback.answer("دسترسی مجاز نیست.", show_alert=True)
        return
    try:
        _, _, _, raw_id, decision = callback.data.split(":")
        order_id = int(raw_id)
    except ValueError:
        await callback.answer("درخواست نامعتبر است.", show_alert=True)
        return
    if decision not in {"approve", "reject"}:
        await callback.answer("تصمیم نامعتبر است.", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        result = await session.execute(update(PremiumOrder).where(
            PremiumOrder.id == order_id, PremiumOrder.status == "pending_review"
        ).values(status="approved" if decision == "approve" else "rejected", reviewed_at=datetime.utcnow()))
        if result.rowcount != 1:
            await session.rollback()
            await callback.answer("رسید قبلاً بررسی شده است.", show_alert=True)
            return
        order = await session.get(PremiumOrder, order_id)
        if decision == "reject":
            await session.execute(update(PremiumPlan).where(PremiumPlan.id == order.plan_id).values(stock=PremiumPlan.stock + 1))
        customer_id = order.customer_telegram_id
        await session.commit()
    await callback.answer("رسید بررسی شد.")
    await callback.message.answer("✅ تأیید شد؛ اطلاعات اشتراک را با /premium_deliver شناسه متن بفرستید." if decision == "approve" else "❌ رسید رد شد و موجودی رزروشده آزاد شد.")
    try:
        await customer_bot.send_message(customer_id,
            f"✅ پرداخت سفارش اشتراک #{order_id} تأیید شد؛ اطلاعات دسترسی پس از آماده‌سازی ارسال می‌شود."
            if decision == "approve" else f"❌ رسید سفارش اشتراک #{order_id} تأیید نشد. با پشتیبانی تماس بگیرید.")
    except Exception as exc:
        logger.warning("Premium review notice failed for order {}: {}", order_id, type(exc).__name__)


@router.message(Command("premium_deliver"))
async def owner_premium_deliver(message: Message, command: CommandObject, customer_bot) -> None:
    if not _owner(message.from_user):
        await message.answer("دسترسی مجاز نیست.")
        return
    parts = (command.args or "").split(maxsplit=1)
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].strip() or len(parts[1]) > 3000:
        await message.answer("روش تحویل: /premium_deliver شناسه_سفارش متن_اختصاصی_اشتراک")
        return
    order_id, delivery = int(parts[0]), parts[1].strip()
    async with AsyncSessionLocal() as session:
        order = await session.get(PremiumOrder, order_id)
        if not order or order.status != "approved":
            await message.answer("فقط سفارش تأییدشده و تحویل‌داده‌نشده قابل ارسال است.")
            return
        try:
            await customer_bot.send_message(order.customer_telegram_id,
                f"🎉 اشتراک <b>{escape(order.title_snapshot)}</b> آماده است.\n\n<code>{escape(delivery)}</code>")
        except Exception as exc:
            logger.warning("Premium delivery failed for order {}: {}", order_id, type(exc).__name__)
            await message.answer("ارسال ناموفق بود؛ سفارش برای تلاش دوباره تأییدشده ماند.")
            return
        order.status, order.delivery_text, order.delivered_at = "delivered", delivery, datetime.utcnow()
        await session.commit()
    await message.answer(f"✅ اشتراک سفارش #{order_id} به خریدار ارسال شد.")


@router.callback_query(F.data == "ai:held:list")
async def owner_held_configs(callback: CallbackQuery) -> None:
    if not _owner(callback.from_user):
        await callback.answer("دسترسی مجاز نیست.", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        configs = (await session.scalars(select(VpnConfig).where(
            VpnConfig.status == "held"
        ).order_by(VpnConfig.id).limit(20))).all()
    await callback.answer()
    if not configs:
        await callback.message.answer("اکانت نگه‌داری‌شده‌ای وجود ندارد.", reply_markup=HOME)
        return
    rows = [[(f"#{config.id} · {config.duration_days} روز · {config.user_count} کاربر", f"ai:held:config:{config.id}")] for config in configs]
    rows.append([("🔙 پنل راینوتک", "ai:home")])
    await callback.message.answer("🔐 اکانت‌های نگه‌داری‌شده؛ انتخاب هر مورد، سفارش‌های تأییدشدهٔ هم‌طرح را نشان می‌دهد.", reply_markup=_keyboard(rows))


@router.callback_query(F.data.startswith("ai:held:config:"))
async def owner_held_config_detail(callback: CallbackQuery) -> None:
    if not _owner(callback.from_user):
        await callback.answer("دسترسی مجاز نیست.", show_alert=True)
        return
    try:
        config_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer("شناسه نامعتبر است.", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        config = await session.get(VpnConfig, config_id)
        if config is None or config.status != "held":
            await callback.answer("این اکانت دیگر در نگه‌داری نیست.", show_alert=True)
            return
        orders = (await session.scalars(select(VpnOrder).where(
            VpnOrder.status == "approved", VpnOrder.duration_days == config.duration_days,
            VpnOrder.user_count == config.user_count,
        ).order_by(VpnOrder.reviewed_at, VpnOrder.id).limit(10))).all()
    await callback.answer()
    buttons = [[(f"تحویل اکانت #{config_id} به سفارش #{order.id}", f"ai:held:assign:{config_id}:{order.id}")] for order in orders]
    buttons.append([("🔙 اکانت‌های نگه‌داری‌شده", "ai:held:list")])
    text = f"اکانت #{config_id} · {config.duration_days} روز · {config.user_count} کاربر\n"
    text += "فقط سفارش تأییدشدهٔ هم‌طرح می‌تواند آن را دریافت کند.\n"
    if not orders:
        text += "اکنون سفارش تأییدشدهٔ هم‌طرح وجود ندارد."
    await callback.message.answer(text, reply_markup=_keyboard(buttons))


@router.callback_query(F.data.startswith("ai:held:assign:"))
async def owner_assign_held_config(callback: CallbackQuery, customer_bot) -> None:
    if not _owner(callback.from_user):
        await callback.answer("دسترسی مجاز نیست.", show_alert=True)
        return
    try:
        _, _, _, raw_config_id, raw_order_id = callback.data.split(":")
        config_id, order_id = int(raw_config_id), int(raw_order_id)
    except ValueError:
        await callback.answer("درخواست نامعتبر است.", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        config = await session.get(VpnConfig, config_id)
        order = await session.get(VpnOrder, order_id)
        if (config is None or order is None or config.status != "held" or order.status != "approved"
                or config.duration_days != order.duration_days or config.user_count != order.user_count):
            await callback.answer("اکانت و سفارش تأییدشده با هم منطبق نیستند.", show_alert=True)
            return
        existing = await session.scalar(select(VpnConfig.id).where(VpnConfig.order_id == order_id))
        if existing is not None:
            await callback.answer("این سفارش قبلاً یک اکانت اختصاصی دارد.", show_alert=True)
            return
        result = await session.execute(update(VpnConfig).where(
            VpnConfig.id == config_id, VpnConfig.status == "held", VpnConfig.order_id.is_(None)
        ).values(status="reserved", order_id=order_id))
        if result.rowcount != 1:
            await session.rollback()
            await callback.answer("این اکانت قبلاً انتخاب شده است.", show_alert=True)
            return
        await session.commit()
    await callback.answer("اکانت به سفارش اختصاص یافت.")
    result = await _deliver_available_config(order_id, customer_bot)
    await callback.message.answer(
        f"سفارش #{order_id}: " +
        ("✅ متن اشتراک به خریدار ارسال شد." if result == "delivered" else "⚠️ ارسال ناموفق بود؛ اکانت برای همین سفارش رزرو ماند."),
        reply_markup=HOME,
    )


@router.callback_query(F.data.startswith("ai:crm:person:"))
async def owner_crm_person(callback: CallbackQuery) -> None:
    if not _owner(callback.from_user):
        await callback.answer("دسترسی مجاز نیست.", show_alert=True)
        return
    try:
        user_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer("شناسه نامعتبر است.", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if user is None or user.role != "customer" or not user.phone_number:
            await callback.answer("مشتری در CRM پیدا نشد.", show_alert=True)
            return
        visits = (await session.scalars(select(AiFeatureVisit).where(
            AiFeatureVisit.telegram_id == user.telegram_id
        ))).all()
        orders = (await session.scalars(select(VpnOrder).where(
            VpnOrder.customer_telegram_id == user.telegram_id
        ).order_by(VpnOrder.id.desc()).limit(5))).all()
    await callback.answer()
    feature_labels = {"ai_menu": "ورود به هوش مصنوعی", "smart_search": "جستجوی هوشمند", "vpn_menu": "اتصال امن"}
    lines = [
        "👤 پروندهٔ CRM راینوتک",
        f"نام: {escape(user.first_name or user.full_name or 'ثبت نشده')}",
        f"اکانت: {('@' + escape(user.username)) if user.username else 'بدون نام کاربری'}",
        f"شمارهٔ اشتراک‌گذاری‌شده: <code>{escape(user.phone_number)}</code>",
        f"Chat ID: <code>{user.telegram_id}</code>",
        f"عضویت: {format_local(user.joined_at)}" if user.joined_at else "عضویت: ثبت نشده",
        f"مرحلهٔ CRM: {escape(user.crm_stage or 'ثبت نشده')}",
    ]
    if visits:
        lines.append("\n🤖 فعالیت در بخش AI:")
        lines.extend(f"• {feature_labels.get(visit.feature, visit.feature)}: {visit.interaction_count} بار؛ آخرین بازدید {format_local(visit.last_seen_at)}" for visit in visits)
    else:
        lines.append("\n🤖 فعالیت در بخش AI هنوز ثبت نشده است.")
    if orders:
        status_names = {"awaiting_receipt": "در انتظار رسید", "pending_review": "رسید در بررسی", "approved": "تأییدشده، منتظر تحویل", "rejected": "رسید ردشده", "delivered": "تحویل‌شده"}
        lines.append("\n🌐 سفارش‌ها و اشتراک‌های اتصال امن:")
        lines.extend(
            f"• #{order.id} · {order.duration_days} روز · {order.user_count} کاربر · {money(order.price_toman)} · {status_names.get(order.status, order.status)}"
            for order in orders
        )
    else:
        lines.append("\n🌐 هنوز سفارشی برای اتصال امن ثبت نشده است.")
    await callback.message.answer("\n".join(lines), reply_markup=_keyboard([
        [("🔙 همهٔ مشتریان", "ai:crm:all:0"), ("🤖 ورودی‌های AI", "ai:crm:ai:0")],
    ]))


@router.callback_query(F.data.startswith("ai:crm:"))
async def owner_crm_list(callback: CallbackQuery) -> None:
    if not _owner(callback.from_user):
        await callback.answer("دسترسی مجاز نیست.", show_alert=True)
        return
    try:
        _, _, scope, raw_page = callback.data.split(":")
        page = max(0, int(raw_page))
        if scope not in {"all", "ai", "buyers"}:
            raise ValueError
    except ValueError:
        await callback.answer("درخواست نامعتبر است.", show_alert=True)
        return
    base = select(User).where(User.role == "customer", User.phone_number.is_not(None), User.joined_at.is_not(None))
    if scope == "ai":
        base = base.where(select(AiFeatureVisit.telegram_id).where(
            AiFeatureVisit.telegram_id == User.telegram_id
        ).exists())
    elif scope == "buyers":
        base = base.where(select(VpnOrder.customer_telegram_id).where(
            VpnOrder.customer_telegram_id == User.telegram_id,
            VpnOrder.status == "delivered",
        ).exists())
    async with AsyncSessionLocal() as session:
        total = await session.scalar(select(func.count()).select_from(base.subquery())) or 0
        rows = (await session.scalars(base.order_by(User.joined_at.desc(), User.id.desc()).offset(page * 10).limit(10))).all()
    if not rows and page:
        await callback.answer("این صفحه خالی است.", show_alert=True)
        return
    await callback.answer()
    heading = {"ai": "🤖 مشتریانِ واردشده به بخش AI", "buyers": "🛍 خریداران اشتراک اتصال امن", "all": "👥 مشتریان CRM راینوتک"}[scope]
    lines = [f"{heading}\nتعداد: {total} · صفحهٔ {page + 1}"]
    lines.extend(f"• {escape(user.first_name or user.full_name or 'بدون نام')} · <code>{escape(user.phone_number)}</code>" for user in rows)
    if not rows:
        lines.append("هنوز موردی ثبت نشده است.")
    buttons = [[(f"👤 {user.first_name or user.full_name or user.telegram_id}"[:58], f"ai:crm:person:{user.id}")] for user in rows]
    nav = []
    if page:
        nav.append(("◀️ قبلی", f"ai:crm:{scope}:{page - 1}"))
    if (page + 1) * 10 < total:
        nav.append(("بعدی ▶️", f"ai:crm:{scope}:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([("🔙 پنل راینوتک", "ai:home")])
    await callback.message.answer("\n".join(lines), reply_markup=_keyboard(buttons))


@router.callback_query(F.data == "ai:orders")
async def owner_orders(callback: CallbackQuery) -> None:
    if not _owner(callback.from_user):
        await callback.answer("دسترسی مجاز نیست.", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        orders = (await session.scalars(select(VpnOrder).where(
            VpnOrder.status.in_(("pending_review", "approved"))
        ).order_by(VpnOrder.id.desc()).limit(20))).all()
    await callback.answer()
    if not orders:
        await callback.message.answer("سفارش در انتظار بررسی یا تحویل وجود ندارد.", reply_markup=HOME)
        return
    rows = [[(f"#{order.id} · {order.duration_days} روز · {order.status}", f"ai:order:{order.id}")] for order in orders]
    rows.append([("🔙 پنل اصلی", "ai:home")])
    await callback.message.answer("🧾 سفارش‌های اتصال امن", reply_markup=_keyboard(rows))


@router.callback_query(F.data == "ai:home")
async def owner_home(callback: CallbackQuery) -> None:
    if not _owner(callback.from_user):
        await callback.answer("دسترسی مجاز نیست.", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer("🧠 پنل AI Developer راینوتک", reply_markup=HOME)


@router.callback_query(F.data.startswith("ai:order:"))
async def owner_order(callback: CallbackQuery, customer_bot) -> None:
    if not _owner(callback.from_user):
        await callback.answer("دسترسی مجاز نیست.", show_alert=True)
        return
    try:
        order_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer("شناسه نامعتبر است.", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        order = await session.get(VpnOrder, order_id)
    if order is None:
        await callback.answer("سفارش پیدا نشد.", show_alert=True)
        return
    await callback.answer()
    summary = (f"🧾 سفارش #{order.id}\nکاربر: <code>{order.customer_telegram_id}</code>\n"
               f"{order.duration_days} روز · {order.user_count} کاربر · {money(order.price_toman)}\nوضعیت: {order.status}")
    buttons = [[("✅ تأیید رسید", f"ai:review:{order.id}:approve"),
                ("❌ رد رسید", f"ai:review:{order.id}:reject")]] if order.status == "pending_review" else []
    buttons.append([("🔙 سفارش‌ها", "ai:orders")])
    if order.receipt_file_id and order.status == "pending_review":
        try:
            stream = BytesIO()
            await customer_bot.download(order.receipt_file_id, destination=stream)
            receipt = BufferedInputFile(stream.getvalue(), filename=f"receipt-{order.id}.jpg")
            if order.receipt_kind == "photo":
                await callback.message.answer_photo(receipt, caption=summary, reply_markup=_keyboard(buttons))
            else:
                await callback.message.answer_document(receipt, caption=summary, reply_markup=_keyboard(buttons))
            return
        except Exception as exc:
            logger.warning("AI admin receipt retrieval failed for order {}: {}", order_id, type(exc).__name__)
            summary += "\n⚠️ تصویر رسید دریافت نشد؛ پیش از تأیید دوباره بررسی کنید."
            buttons = [[("🔙 سفارش‌ها", "ai:orders")]]
    await callback.message.answer(summary, reply_markup=_keyboard(buttons))


@router.callback_query(F.data.startswith("ai:review:"))
async def owner_review(callback: CallbackQuery, customer_bot) -> None:
    if not _owner(callback.from_user):
        await callback.answer("دسترسی مجاز نیست.", show_alert=True)
        return
    try:
        _, _, raw_id, decision = callback.data.split(":")
        order_id = int(raw_id)
    except ValueError:
        await callback.answer("درخواست نامعتبر است.", show_alert=True)
        return
    customer_id = await review_vpn_order(order_id, decision)
    if customer_id is None:
        await callback.answer("رسید قبلاً بررسی شده یا نامعتبر است.", show_alert=True)
        return
    await callback.answer("رسید بررسی شد.")
    if decision == "approve":
        result = await _deliver_available_config(order_id, customer_bot)
        await callback.message.answer(
            f"✅ سفارش #{order_id} تأیید شد. " +
            ("لینک اختصاصی به مشتری تحویل شد." if result == "delivered" else "تحویل منتظر لینک متناسب یا تلاش مجدد است."),
            reply_markup=HOME,
        )
        if result == "delivered":
            return
        notice = f"✅ پرداخت سفارش #{order_id} تأیید شد. لینک اتصال پس از آماده‌سازی برایتان ارسال می‌شود."
    else:
        await callback.message.answer(f"رسید سفارش #{order_id} رد شد.", reply_markup=HOME)
        notice = f"رسید سفارش #{order_id} تأیید نشد. از بخش «اکانت‌های من» می‌توانید دوباره رسید بفرستید."
    try:
        await customer_bot.send_message(customer_id, notice)
    except Exception as exc:
        logger.warning("VPN customer notice failed for order {}: {}", order_id, type(exc).__name__)


@router.callback_query(F.data == "ai:config:select")
async def owner_config_select(callback: CallbackQuery) -> None:
    if not _owner(callback.from_user):
        await callback.answer("دسترسی مجاز نیست.", show_alert=True)
        return
    await callback.answer()
    rows = [[(f"{days} روز", f"ai:config:days:{days}")] for days in SOURCE_PRICES]
    rows.append([("🔙 پنل اصلی", "ai:home")])
    await callback.message.answer("مدت لینک اختصاصی را انتخاب کنید:", reply_markup=_keyboard(rows))


@router.callback_query(F.data.startswith("ai:config:days:"))
async def owner_config_days(callback: CallbackQuery) -> None:
    if not _owner(callback.from_user):
        await callback.answer("دسترسی مجاز نیست.", show_alert=True)
        return
    try:
        days = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer("طرح نامعتبر است.", show_alert=True)
        return
    if days not in SOURCE_PRICES:
        await callback.answer("طرح نامعتبر است.", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer("تعداد کاربران هم‌زمان را انتخاب کنید:", reply_markup=_keyboard([
        [(f"{users} کاربر", f"ai:config:plan:{days}:{users}")] for users in SOURCE_PRICES[days]
    ]))


@router.callback_query(F.data.startswith("ai:config:plan:"))
async def owner_config_plan(callback: CallbackQuery, state: FSMContext) -> None:
    if not _owner(callback.from_user):
        await callback.answer("دسترسی مجاز نیست.", show_alert=True)
        return
    try:
        _, _, _, raw_days, raw_users = callback.data.split(":")
        days, users = int(raw_days), int(raw_users)
        if users not in SOURCE_PRICES[days]:
            raise ValueError
    except (ValueError, KeyError):
        await callback.answer("طرح نامعتبر است.", show_alert=True)
        return
    await state.set_state(ConfigEntry.waiting_for_link)
    await state.update_data(days=days, users=users)
    await callback.answer()
    await callback.message.answer(f"لینک اختصاصی طرح {days} روزه، {users} کاربر را بفرستید. هر لینک فقط یک بار قابل تحویل است. برای لغو /cancel را بزنید.")


@router.message(Command("cancel"))
async def owner_cancel(message: Message, state: FSMContext) -> None:
    if not _owner(message.from_user):
        await message.answer("دسترسی مجاز نیست.")
        return
    await state.clear()
    await message.answer("عملیات لغو شد.", reply_markup=HOME)


@router.message(ConfigEntry.waiting_for_link)
async def owner_config_link(message: Message, state: FSMContext) -> None:
    if not _owner(message.from_user):
        await message.answer("دسترسی مجاز نیست.")
        return
    url = (message.text or "").strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or len(url) > 2500:
        await message.answer("لینک HTTPS معتبر بفرستید یا /cancel را بزنید.")
        return
    await state.update_data(url=url)
    await state.set_state(ConfigEntry.waiting_for_ssh)
    await message.answer(
        "لینک دریافت شد و هنوز به خریدار تحویل نشده است. اگر اطلاعات SSH دارد، بلوک شامل "
        "SSH Host، SSH Port، Username و Password را در یک پیام بفرستید. "
        "برای ثبت بدون SSH دستور /skip و برای لغو /cancel را بزنید."
    )


async def _finish_config_registration(message: Message, state: FSMContext, customer_bot, details=None) -> None:
    from bot.handlers.vpn_shop import _register_config

    data = await state.get_data()
    result, order_id = await _register_config(
        data["days"], data["users"], data["url"], customer_bot, ssh_details=details,
    )
    await state.clear()
    if result == "duplicate":
        await message.answer("این لینک قبلاً ثبت شده است.", reply_markup=HOME)
    elif result == "delivered":
        await message.answer(f"✅ لینک به سفارش #{order_id} اختصاص یافت و برای مشتری ارسال شد.", reply_markup=HOME)
    elif result == "send_failed":
        await message.answer(f"لینک برای سفارش #{order_id} رزرو شد، اما ارسال ناموفق بود. پس از رفع مشکل دوباره تلاش کنید.", reply_markup=HOME)
    else:
        await message.answer("✅ لینک ثبت شد و منتظر سفارش تأییدشدهٔ هم‌طرح است.", reply_markup=HOME)


@router.message(Command("skip"), ConfigEntry.waiting_for_ssh)
async def owner_config_skip_ssh(message: Message, state: FSMContext, customer_bot) -> None:
    if not _owner(message.from_user):
        await message.answer("دسترسی مجاز نیست.")
        return
    await _finish_config_registration(message, state, customer_bot)


@router.message(ConfigEntry.waiting_for_ssh)
async def owner_config_ssh(message: Message, state: FSMContext, customer_bot) -> None:
    if not _owner(message.from_user):
        await message.answer("دسترسی مجاز نیست.")
        return
    from bot.handlers.vpn_shop import parse_ssh_details

    try:
        details = parse_ssh_details(message.text or "")
    except ValueError:
        await message.answer("اطلاعات SSH کامل نیست. SSH Host، SSH Port، Username و Password را بفرستید یا /skip را بزنید.")
        return
    await _finish_config_registration(message, state, customer_bot, details)

"""Premium AI subscriptions with explicit stock, receipt review and manual delivery."""

from __future__ import annotations

from datetime import datetime, timedelta
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from loguru import logger
from sqlalchemy import select, update

from bot.config import get_settings
from database.models import PremiumOrder, PremiumPlan
from database.session import AsyncSessionLocal

router = Router()
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")

CATEGORIES = {
    "chat": "💬 گفت‌وگو و پژوهش",
    "api": "🧩 اعتبار و دسترسی API",
    "creative": "🎨 تصویر و ویدئو",
    "editing": "🎬 تدوین و تولید محتوا",
}


class PremiumState(StatesGroup):
    awaiting_receipt = State()


def sale_price(source_price_toman: int) -> int:
    if source_price_toman <= 0:
        raise ValueError("A positive source price is required")
    return (source_price_toman * 110 + 99) // 100


def menu(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=data) for label, data in row]
        for row in rows
    ])


def money(amount: int) -> str:
    return f"{amount:,} تومان"


def plan_available(plan: PremiumPlan) -> bool:
    return bool(plan.active and plan.stock > 0 and plan.source_price_toman > 0
                and plan.checked_at >= datetime.utcnow() - timedelta(hours=24))


@router.callback_query(F.data == "premium:home")
async def premium_home(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with AsyncSessionLocal() as session:
        plans = (await session.scalars(select(PremiumPlan).where(PremiumPlan.active.is_(True)).order_by(PremiumPlan.id))).all()
    await callback.answer()
    rows = [[(f"{plan.title} · {money(sale_price(plan.source_price_toman))}" if plan_available(plan)
              else f"{plan.title} · فعلاً ناموجود", f"premium:plan:{plan.id}")] for plan in plans]
    rows += [[("📦 سفارش‌های من", "premium:orders")], [("🔙 سرویس‌های هوش مصنوعی", "ai:apis")]]
    await callback.message.answer(
        "🛍 <b>اشتراک‌ها و اعتبار API هوش مصنوعی</b>\n\n"
        + ("پلن‌های بررسی‌شده را انتخاب کنید:" if plans else "فعلاً پلن دارای قیمت و موجودی تأییدشده‌ای ثبت نشده است."),
        reply_markup=menu(rows),
    )


@router.callback_query(F.data.startswith("premium:category:"))
async def premium_category(callback: CallbackQuery) -> None:
    category = callback.data.rsplit(":", 1)[-1]
    if category not in CATEGORIES:
        await callback.answer("دسته نامعتبر است.", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        plans = (await session.scalars(select(PremiumPlan).where(
            PremiumPlan.category == category, PremiumPlan.active.is_(True)
        ).order_by(PremiumPlan.title, PremiumPlan.id))).all()
    await callback.answer()
    rows = [[(f"{p.title} · {money(sale_price(p.source_price_toman))}" if plan_available(p)
              else f"{p.title} · فعلاً ناموجود", f"premium:plan:{p.id}")] for p in plans]
    rows.append([("🔙 دسته‌بندی‌ها", "premium:home")])
    await callback.message.answer(
        f"{CATEGORIES[category]}\n" + ("پلن‌های بررسی‌شده:" if plans else "فعلاً پلن قیمت‌گذاری‌شده و تأییدشده‌ای ثبت نشده است."),
        reply_markup=menu(rows),
    )


@router.callback_query(F.data.startswith("premium:plan:"))
async def premium_plan(callback: CallbackQuery) -> None:
    try:
        plan_id = int(callback.data.rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("پلن نامعتبر است.", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        plan = await session.get(PremiumPlan, plan_id)
    if not plan or not plan.active:
        await callback.answer("این پلن فعال نیست.", show_alert=True)
        return
    available = plan_available(plan)
    await callback.answer()
    text = f"🛍 <b>{escape(plan.title)}</b>\n{escape(plan.description or '')}\n\n"
    text += (f"قیمت نهایی: <b>{money(sale_price(plan.source_price_toman))}</b>\nموجودی ثبت‌شده: {plan.stock}\n"
             if available else "قیمت یا موجودی این پلن نیاز به بررسی دوباره دارد.\n")
    text += "پرداخت فقط برای پلنِ دارای قیمت و موجودی تأییدشده فعال است."
    rows = [[("💳 ادامه خرید", f"premium:quote:{plan.id}")]] if available else []
    rows.append([("🔙 اشتراک‌های پرمیوم", "premium:home")])
    await callback.message.answer(text, reply_markup=menu(rows))


@router.callback_query(F.data.startswith("premium:quote:"))
async def premium_quote(callback: CallbackQuery) -> None:
    try:
        plan_id = int(callback.data.rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("پلن نامعتبر است.", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        plan = await session.get(PremiumPlan, plan_id)
    if not plan or not plan_available(plan):
        await callback.answer("قیمت یا موجودی این پلن نیاز به بررسی دارد.", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(
        f"🧾 <b>{escape(plan.title)}</b>\nمبلغ نهایی با ۱۰٪ افزایش: <b>{money(sale_price(plan.source_price_toman))}</b>\n\n"
        "نحوه پرداخت را انتخاب کنید:",
        reply_markup=menu([[("💳 کارت به کارت", f"premium:buy:{plan.id}")], [("🔙 بازگشت", f"premium:plan:{plan.id}")]]),
    )


@router.callback_query(F.data.startswith("premium:buy:"))
async def premium_buy(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        plan_id = int(callback.data.rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("پلن نامعتبر است.", show_alert=True)
        return
    settings = get_settings()
    if not settings.VPN_PAYMENT_CARD or not settings.VPN_PAYMENT_HOLDER:
        await callback.answer("پرداخت فعلاً آماده نیست.", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        existing = await session.scalar(select(PremiumOrder).where(
            PremiumOrder.customer_telegram_id == callback.from_user.id,
            PremiumOrder.plan_id == plan_id,
            PremiumOrder.status == "awaiting_receipt",
        ).order_by(PremiumOrder.id.desc()).limit(1))
        if existing:
            order = existing
        else:
            result = await session.execute(update(PremiumPlan).where(
                PremiumPlan.id == plan_id, PremiumPlan.active.is_(True), PremiumPlan.stock > 0,
                PremiumPlan.source_price_toman > 0,
                PremiumPlan.checked_at >= datetime.utcnow() - timedelta(hours=24),
            ).values(stock=PremiumPlan.stock - 1).returning(PremiumPlan.title, PremiumPlan.source_price_toman))
            row = result.one_or_none()
            if row is None:
                await session.rollback()
                await callback.answer("موجودی یا قیمت نیاز به بررسی دوباره دارد.", show_alert=True)
                return
            order = PremiumOrder(
                customer_telegram_id=callback.from_user.id, plan_id=plan_id,
                title_snapshot=row.title, source_price_toman=row.source_price_toman,
                price_toman=sale_price(row.source_price_toman),
            )
            session.add(order)
            await session.commit()
    await state.set_state(PremiumState.awaiting_receipt)
    await state.update_data(premium_order_id=order.id)
    await callback.answer("سفارش ثبت شد.")
    await callback.message.answer(
        f"🛍 {escape(order.title_snapshot)}\nمبلغ: <b>{money(order.price_toman)}</b>\n\n"
        "شماره کارت را در صفحه جداگانه ببینید؛ پس از واریز، تصویر رسید را همین‌جا بفرستید.",
        reply_markup=menu([[("💳 مشاهده شماره کارت", f"premium:card:{order.id}")], [("📦 سفارش‌های من", "premium:orders")]]),
    )


@router.callback_query(F.data.startswith("premium:card:"))
async def premium_card(callback: CallbackQuery) -> None:
    try:
        order_id = int(callback.data.rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("سفارش نامعتبر است.", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        order = await session.get(PremiumOrder, order_id)
    if not order or order.customer_telegram_id != callback.from_user.id or order.status != "awaiting_receipt":
        await callback.answer("به این سفارش دسترسی ندارید.", show_alert=True)
        return
    settings = get_settings()
    await callback.answer()
    await callback.message.answer(
        f"💳 <b>اطلاعات پرداخت سفارش #{order.id}</b>\n\n"
        f"شماره کارت:\n<code>{escape(settings.VPN_PAYMENT_CARD)}</code>\n\n"
        f"صاحب کارت: <b>{escape(settings.VPN_PAYMENT_HOLDER)}</b>\n"
        f"مبلغ: <b>{money(order.price_toman)}</b>\n\nتصویر رسید را همین‌جا بفرستید.",
        protect_content=True,
        reply_markup=menu([[("🔙 سفارش‌های من", "premium:orders")]]),
    )


@router.message(PremiumState.awaiting_receipt, F.photo | F.document)
async def premium_receipt(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    order_id = data.get("premium_order_id")
    if message.photo:
        file_id, kind = message.photo[-1].file_id, "photo"
    elif message.document and (message.document.mime_type or "").startswith("image/"):
        file_id, kind = message.document.file_id, "document"
    else:
        await message.answer("لطفاً رسید را به صورت تصویر بفرستید.")
        return
    async with AsyncSessionLocal() as session:
        order = await session.get(PremiumOrder, order_id) if isinstance(order_id, int) else None
        if not order or order.customer_telegram_id != message.from_user.id or order.status != "awaiting_receipt":
            await state.clear()
            await message.answer("سفارشِ منتظر رسید پیدا نشد.")
            return
        order.receipt_file_id, order.receipt_kind, order.status = file_id, kind, "pending_review"
        await session.commit()
    await state.clear()
    settings = get_settings()
    notified = False
    if settings.AI_ADMIN_BOT_TOKEN and settings.AI_OWNER_TELEGRAM_ID:
        from aiogram import Bot
        from aiogram.client.default import DefaultBotProperties
        from aiogram.enums import ParseMode
        owner_bot = Bot(settings.AI_ADMIN_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        try:
            await owner_bot.send_message(
                settings.AI_OWNER_TELEGRAM_ID,
                f"🧾 رسید اشتراک #{order.id}\n{escape(order.title_snapshot)}\nمبلغ: {money(order.price_toman)}\n"
                f"مشتری: <code>{order.customer_telegram_id}</code>",
                reply_markup=menu([[("🔎 بررسی رسید", f"ai:premium:order:{order.id}")]]),
            )
            notified = True
        except Exception as exc:
            logger.warning("Premium receipt notification failed for order {}: {}", order.id, type(exc).__name__)
        finally:
            await owner_bot.session.close()
    await message.answer(
        "✅ رسید شما ثبت شد. نتیجه بررسی و اطلاعات اشتراک همین‌جا ارسال می‌شود."
        + ("" if notified else "\n⚠️ اعلان پشتیبانی ارسال نشد؛ لطفاً با پشتیبانی تماس بگیرید."),
    )


@router.callback_query(F.data == "premium:orders")
async def premium_orders(callback: CallbackQuery) -> None:
    async with AsyncSessionLocal() as session:
        orders = (await session.scalars(select(PremiumOrder).where(
            PremiumOrder.customer_telegram_id == callback.from_user.id
        ).order_by(PremiumOrder.id.desc()).limit(15))).all()
    await callback.answer()
    if not orders:
        await callback.message.answer("هنوز سفارش اشتراک پرمیوم ثبت نکرده‌اید.", reply_markup=menu([[("🔙 اشتراک‌ها", "premium:home")]]))
        return
    labels = {"awaiting_receipt": "منتظر رسید", "pending_review": "در حال بررسی", "approved": "تأییدشده؛ منتظر تحویل", "rejected": "ردشده", "delivered": "تحویل‌شده"}
    rows = [[(f"#{o.id} · {o.title_snapshot} · {labels.get(o.status, o.status)}", f"premium:order:{o.id}")] for o in orders]
    rows.append([("🔙 اشتراک‌ها", "premium:home")])
    await callback.message.answer("📦 سفارش‌های اشتراک من", reply_markup=menu(rows))


@router.callback_query(F.data.startswith("premium:order:"))
async def premium_order(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        order_id = int(callback.data.rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("سفارش نامعتبر است.", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        order = await session.get(PremiumOrder, order_id)
    if not order or order.customer_telegram_id != callback.from_user.id:
        await callback.answer("به این سفارش دسترسی ندارید.", show_alert=True)
        return
    await callback.answer()
    rows = []
    if order.status == "awaiting_receipt":
        await state.set_state(PremiumState.awaiting_receipt)
        await state.update_data(premium_order_id=order.id)
        rows.append([("💳 مشاهده شماره کارت", f"premium:card:{order.id}")])
        rows.append([("لغو سفارش", f"premium:cancel:{order.id}")])
    rows.append([("🔙 سفارش‌ها", "premium:orders")])
    text = f"سفارش <code>#{order.id}</code>\n{escape(order.title_snapshot)}\nمبلغ: {money(order.price_toman)}\nوضعیت: {escape(order.status)}"
    if order.status == "delivered":
        text += "\n\nاطلاعات اشتراک برای شما ارسال شده است."
    await callback.message.answer(text, reply_markup=menu(rows))


@router.callback_query(F.data.startswith("premium:cancel:"))
async def premium_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        order_id = int(callback.data.rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("سفارش نامعتبر است.", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        result = await session.execute(update(PremiumOrder).where(
            PremiumOrder.id == order_id,
            PremiumOrder.customer_telegram_id == callback.from_user.id,
            PremiumOrder.status == "awaiting_receipt",
        ).values(status="cancelled"))
        if result.rowcount != 1:
            await session.rollback()
            await callback.answer("این سفارش قابل لغو نیست.", show_alert=True)
            return
        order = await session.get(PremiumOrder, order_id)
        await session.execute(update(PremiumPlan).where(PremiumPlan.id == order.plan_id).values(stock=PremiumPlan.stock + 1))
        await session.commit()
    await state.clear()
    await callback.answer("سفارش لغو شد.")
    await callback.message.answer(f"سفارش #{order_id} لغو و موجودی آن آزاد شد.", reply_markup=menu([[("🔙 اشتراک‌ها", "premium:home")]]))


@router.message(PremiumState.awaiting_receipt)
async def premium_receipt_prompt(message: Message) -> None:
    await message.answer("لطفاً تصویر رسید را بفرستید یا با /start به منوی اصلی برگردید.")

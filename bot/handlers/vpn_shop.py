"""Receipt-reviewed VPN orders with one-time subscription delivery."""

from __future__ import annotations

from datetime import datetime, timedelta
from html import escape
from hashlib import sha256
from urllib.parse import urlparse

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.filters.command import CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from bot.config import get_settings
from bot.services.ai_feature_visits import record_ai_visit
from database.models import VpnConfig, VpnConfigConnection, VpnOrder
from database.session import AsyncSessionLocal

router = Router()
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")

# Prices observed in the screenshots of @Infinity_RocketTunnel_Bot (toman).
SOURCE_PRICES = {
    30: {1: 379_000, 2: 699_000, 3: 999_000},
    60: {1: 499_000, 2: 899_000, 3: 1_299_000},
    90: {1: 699_000, 2: 1_299_000, 3: 1_799_000},
    180: {1: 1_299_000, 2: 2_299_000, 3: 3_099_000},
}
MARKUP_PERCENT = 15


class VpnState(StatesGroup):
    awaiting_receipt = State()


def sale_price(duration: int, users: int) -> int:
    """Exact 15 percent increase; source prices are multiples of 1,000 toman."""
    return SOURCE_PRICES[duration][users] * (100 + MARKUP_PERCENT) // 100


def money(amount: int) -> str:
    return f"{amount:,} تومان"


def _connection_text(connection: str) -> str:
    parsed = urlparse(connection)
    if parsed.scheme == "https" and parsed.netloc:
        return f'<a href="{escape(connection, quote=True)}">🔗 لینک اتصال شما</a>'
    return f"<code>{escape(connection)}</code>"


def _delivery_message(order: VpnOrder, config: VpnConfig, details: VpnConfigConnection | None) -> str:
    label = details.account_label if details and details.account_label else f"اشتراک #{order.id}"
    lines = [
        "🥳 <b>اکانت جدید شما آماده است!</b>",
        f"پلن: <b>{order.duration_days} روز نامحدود ({order.user_count} کاربر)</b>",
        "مدت اعتبار از اولین اتصال محاسبه می‌شود.",
        "",
        "ابتدا آخرین نسخهٔ برنامهٔ RocketTunnel را نصب کنید: "
        '<a href="https://rcktnl.site/download">لینک دانلود</a>',
        "سپس روی لینک زیر بزنید تا کانفیگ اسمارت به برنامه اضافه شود؛ آن را انتخاب کنید و متصل شوید.",
        "",
        "لینک شما 👇🏼👇🏼👇🏼",
        f'<a href="{escape(config.connection_url, quote=True)}">🚀 {escape(label)} · لینک اسمارت</a>',
        "",
        "مزایای کانفیگ اسمارت در RocketTunnel:",
        "📡 تنظیم اتصال متناسب با اپراتور و منطقه",
        "📍 انتخاب لوکیشن",
        "⚡️ به‌روزرسانی خودکار کانفیگ",
        "✅ دسترسی به سرویس‌های داخلی بدون خاموش‌کردن اتصال",
    ]
    if details:
        lines.extend([
            "", "────────────", "<b>اتصال به روش SSH-Direct</b>",
            "Protocol: SSH-Direct",
            f"Name: <code>{escape(details.ssh_name or 'RAINOTEK')}</code>",
            f"SSH Host: <code>{escape(details.ssh_host)}</code>",
            f"SSH Port: <code>{details.ssh_port}</code>",
        ])
        if details.udpgw_port:
            lines.append(f"Udpgw Port: <code>{details.udpgw_port}</code>")
        lines.extend([
            f"Username: <code>{escape(details.ssh_username)}</code>",
            f"Password: <code>{escape(details.ssh_password)}</code>",
        ])
    return "\n".join(lines)


def parse_ssh_details(text: str) -> dict[str, str | int]:
    """Accept the provider's SSH field block, not arbitrary customer-facing prose."""
    aliases = {
        "name": "ssh_name", "ssh host": "ssh_host", "ssh port": "ssh_port",
        "udpgw port": "udpgw_port", "username": "ssh_username", "password": "ssh_password",
    }
    values: dict[str, str | int] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        field = aliases.get(key.strip().lower())
        if field:
            values[field] = value.strip().strip("`")
    for required in ("ssh_host", "ssh_port", "ssh_username", "ssh_password"):
        if not values.get(required):
            raise ValueError("SSH details are incomplete")
    for field in ("ssh_port", "udpgw_port"):
        if field in values:
            try:
                values[field] = int(values[field])
            except (ValueError, TypeError):
                raise ValueError("Invalid SSH port") from None
            if not 1 <= values[field] <= 65535:
                raise ValueError("Invalid SSH port")
    if any(len(str(value)) > 255 for value in values.values()) or " " in str(values["ssh_host"]):
        raise ValueError("Invalid SSH details")
    values["account_label"] = str(values["ssh_username"])
    return values


def _vpn_admin_ids() -> list[int]:
    settings = get_settings()
    owner_id = getattr(settings, "AI_OWNER_TELEGRAM_ID", 0)
    return [owner_id] if owner_id else settings.ADMIN_TELEGRAM_IDS


async def review_vpn_order(order_id: int, decision: str) -> int | None:
    """Change a pending receipt exactly once, even if two review buttons are tapped."""
    if decision not in {"approve", "reject"}:
        return None
    async with AsyncSessionLocal() as session:
        result = await session.execute(update(VpnOrder).where(
            VpnOrder.id == order_id, VpnOrder.status == "pending_review"
        ).values(status="approved" if decision == "approve" else "rejected", reviewed_at=datetime.utcnow()))
        if result.rowcount != 1:
            await session.rollback()
            return None
        customer_id = await session.scalar(select(VpnOrder.customer_telegram_id).where(VpnOrder.id == order_id))
        await session.commit()
        return customer_id


async def _deliver_available_config(order_id: int, bot) -> str:
    """Reserve exactly one matching URL before sending; retry keeps that reservation."""
    async with AsyncSessionLocal() as session:
        order = await session.get(VpnOrder, order_id)
        if order is None or order.status != "approved":
            return "unavailable"
        config = await session.scalar(select(VpnConfig).where(VpnConfig.order_id == order_id).limit(1))
        if config is None:
            candidates = (await session.scalars(select(VpnConfig).where(
                VpnConfig.duration_days == order.duration_days,
                VpnConfig.user_count == order.user_count,
                VpnConfig.status == "available",
            ).order_by(VpnConfig.id).limit(10))).all()
            for candidate in candidates:
                result = await session.execute(update(VpnConfig).where(
                    VpnConfig.id == candidate.id, VpnConfig.status == "available"
                ).values(status="reserved", order_id=order_id))
                if result.rowcount:
                    config = candidate
                    await session.commit()
                    break
        if config is None:
            return "no_stock"
        details = await session.get(VpnConfigConnection, config.id)
        try:
            await bot.send_message(
                order.customer_telegram_id,
                _delivery_message(order, config, details),
            )
        except Exception as exc:
            logger.warning("VPN delivery failed for order {}: {}", order_id, type(exc).__name__)
            return "send_failed"
        order.delivery_text = config.connection_url
        order.delivered_at = datetime.utcnow()
        order.status = "delivered"
        config.status = "delivered"
        await session.commit()
        return "delivered"


def _menu(buttons: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=callback) for label, callback in row]
        for row in buttons
    ])


HOME = _menu([
    [("📱 اکانت‌های من", "vpn:accounts"), ("💰 کیف پول", "vpn:wallet")],
    [("🛍 خرید / تمدید", "vpn:durations"), ("🧪 دریافت اکانت تست", "vpn:trial")],
    [("👩🏻‍💻 پشتیبانی", "vpn:support"), ("📕 راهنمای نصب", "vpn:guide")],
    [("🛠 رفع مشکل اتصال", "vpn:troubleshoot"), ("📍 لوکیشن‌ها", "vpn:locations")],
    [("🔙 بازگشت به هوش مصنوعی", "vpn:back_ai")],
])


@router.callback_query(F.data == "vpn:home")
async def vpn_home(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await record_ai_visit(callback.from_user.id, "vpn_menu")
    await callback.answer()
    await callback.message.answer(
        "🌐 <b>اتصال امن راینوتک</b>\n\n"
        "طرح‌های ۳۰ تا ۱۸۰ روزه را انتخاب کنید. سفارش پس از بررسی رسید توسط پشتیبانی فعال می‌شود.",
        reply_markup=HOME,
    )


@router.callback_query(F.data == "vpn:back_ai")
async def vpn_back_ai(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    from bot.handlers.ai_hub import AI_MENU
    await callback.message.answer("🤖 هوش مصنوعی ai", reply_markup=AI_MENU)


@router.callback_query(F.data == "vpn:durations")
async def vpn_durations(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer(
        "📅 مدت طرح را انتخاب کنید. هر طرح برای یک، دو یا سه کاربر هم‌زمان عرضه می‌شود:",
        reply_markup=_menu([
            [("۳۰ روز", "vpn:duration:30"), ("۶۰ روز", "vpn:duration:60")],
            [("۹۰ روز", "vpn:duration:90"), ("۱۸۰ روز", "vpn:duration:180")],
            [("🔙 بازگشت", "vpn:home")],
        ]),
    )


@router.callback_query(F.data.startswith("vpn:duration:"))
async def vpn_seats(callback: CallbackQuery) -> None:
    try:
        duration = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer("مدت نامعتبر است.", show_alert=True)
        return
    if duration not in SOURCE_PRICES:
        await callback.answer("این طرح ارائه نمی‌شود.", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(
        f"مدت: <b>{duration} روز</b>\nتعداد کاربر هم‌زمان را انتخاب کنید.\n\n"
        "هر کاربرِ هم‌زمان برای یک دستگاهِ متصل در همان لحظه در نظر گرفته شده است.",
        reply_markup=_menu([
            [(f"۱ کاربر · {money(sale_price(duration, 1))}", f"vpn:quote:{duration}:1")],
            [(f"۲ کاربر · {money(sale_price(duration, 2))}", f"vpn:quote:{duration}:2")],
            [(f"۳ کاربر · {money(sale_price(duration, 3))}", f"vpn:quote:{duration}:3")],
            [("🔙 بازگشت", "vpn:durations")],
        ]),
    )


@router.callback_query(F.data.startswith("vpn:quote:"))
async def vpn_quote(callback: CallbackQuery) -> None:
    try:
        _, _, duration_raw, users_raw = callback.data.split(":")
        duration, users = int(duration_raw), int(users_raw)
        amount = sale_price(duration, users)
    except (ValueError, KeyError):
        await callback.answer("طرح نامعتبر است.", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(
        f"🌐 طرح اتصال امن راینوتک\nمدت: <b>{duration} روز</b>\nکاربر هم‌زمان: <b>{users}</b>\n"
        f"مبلغ نهایی: <b>{money(amount)}</b>\n\nلطفاً نحوهٔ پرداخت را انتخاب کنید:",
        reply_markup=_menu([
            [("💳 کارت به کارت", f"vpn:create:{duration}:{users}"),
             ("💰 کیف پول", f"vpn:wallet_pay:{duration}:{users}")],
            [("🔙 انتخاب تعداد کاربر", f"vpn:duration:{duration}")],
        ]),
    )


@router.callback_query(F.data.startswith("vpn:wallet_pay:"))
async def vpn_wallet_payment(callback: CallbackQuery) -> None:
    try:
        _, _, duration_raw, users_raw = callback.data.split(":")
        duration, users = int(duration_raw), int(users_raw)
        amount = sale_price(duration, users)
    except (ValueError, KeyError):
        await callback.answer("طرح نامعتبر است.", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(
        "💰 پرداخت با کیف پول هنوز فعال نشده و موجودی کیف پولی برای شما ثبت نمی‌شود. "
        "برای تکمیل خرید، کارت به کارت را انتخاب کنید؛ سفارش و پرداختی از کیف پول ثبت نشده است.",
        reply_markup=_menu([
            [(f"💳 کارت به کارت · {money(amount)}", f"vpn:create:{duration}:{users}")],
            [("🔙 روش‌های پرداخت", f"vpn:quote:{duration}:{users}")],
        ]),
    )


def _payment_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return _menu([
        [("💳 مشاهده شماره کارت", f"vpn:card:{order_id}")],
        [("↩️ بازگشت به منوی اصلی", "vpn:home")],
    ])


@router.callback_query(F.data.startswith("vpn:create:"))
async def vpn_create_order(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        _, _, duration_raw, users_raw = callback.data.split(":")
        duration, users = int(duration_raw), int(users_raw)
        amount = sale_price(duration, users)
    except (ValueError, KeyError):
        await callback.answer("طرح نامعتبر است.", show_alert=True)
        return
    settings = get_settings()
    if not settings.VPN_PAYMENT_CARD or not settings.VPN_PAYMENT_HOLDER:
        await callback.answer("پرداخت فعلاً آماده نیست؛ با پشتیبانی تماس بگیرید.", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        order = await session.scalar(select(VpnOrder).where(
            VpnOrder.customer_telegram_id == callback.from_user.id,
            VpnOrder.duration_days == duration,
            VpnOrder.user_count == users,
            VpnOrder.status == "awaiting_receipt",
            VpnOrder.created_at >= datetime.utcnow() - timedelta(minutes=10),
        ).order_by(VpnOrder.id.desc()).limit(1))
        if order is None:
            order = VpnOrder(
                customer_telegram_id=callback.from_user.id,
                duration_days=duration,
                user_count=users,
                base_price_toman=SOURCE_PRICES[duration][users],
                price_toman=amount,
                status="awaiting_receipt",
            )
            session.add(order)
            await session.commit()
        order_id = order.id
    await state.set_state(VpnState.awaiting_receipt)
    await state.update_data(vpn_order_id=order_id)
    await callback.answer("سفارش ثبت شد.")
    await callback.message.answer(
        f"🌐 طرح: <b>{duration} روز ({users} کاربر)</b>\n"
        f"مبلغ: <b>{money(amount)}</b>\n\n"
        "شماره کارت را با دکمهٔ زیر ببینید. مبلغ را کارت به کارت کنید و تصویر فیش واریزی را همین‌جا بفرستید. "
        "پس از بررسی و تأیید رسید، لینک اتصال برایتان ارسال می‌شود.",
        reply_markup=_payment_keyboard(order_id),
    )


@router.callback_query(F.data.startswith("vpn:card:"))
async def vpn_card(callback: CallbackQuery) -> None:
    try:
        order_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer("سفارش نامعتبر است.", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        order = await session.get(VpnOrder, order_id)
    if order is None or order.customer_telegram_id != callback.from_user.id:
        await callback.answer("به این سفارش دسترسی ندارید.", show_alert=True)
        return
    if order.status not in {"awaiting_receipt", "rejected"}:
        await callback.answer("رسید این سفارش قبلاً ثبت شده است.", show_alert=True)
        return
    settings = get_settings()
    await callback.answer()
    await callback.message.answer(
        f"💳 <b>اطلاعات کارت به کارت سفارش #{order_id}</b>\n\n"
        "شماره کارت جهت واریز:\n"
        f"<code>{escape(settings.VPN_PAYMENT_CARD)}</code>\n\n"
        f"صاحب کارت: <b>{escape(settings.VPN_PAYMENT_HOLDER)}</b>\n"
        f"مبلغ: <b>{money(order.price_toman)}</b>\n\n"
        "پس از واریز، تصویر رسید را در همین گفت‌وگو ارسال کنید.",
        protect_content=True,
        reply_markup=_menu([[("↩️ بازگشت به سفارش", f"vpn:account:{order_id}")]]),
    )


@router.message(VpnState.awaiting_receipt, F.photo | F.document)
async def vpn_receipt(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    order_id = data.get("vpn_order_id")
    if message.photo:
        file_id, kind = message.photo[-1].file_id, "photo"
    elif message.document and (message.document.mime_type or "").startswith("image/"):
        file_id, kind = message.document.file_id, "document"
    else:
        await message.answer("رسید را به‌صورت عکس یا فایل تصویری ارسال کنید.")
        return
    async with AsyncSessionLocal() as session:
        order = await session.get(VpnOrder, order_id) if isinstance(order_id, int) else None
        if order is None or order.customer_telegram_id != message.from_user.id or order.status not in {"awaiting_receipt", "rejected"}:
            await state.clear()
            await message.answer("سفارشِ منتظر رسید پیدا نشد. از «اکانت‌های من» آن را بررسی کنید.")
            return
        order.receipt_file_id = file_id
        order.receipt_kind = kind
        order.status = "pending_review"
        await session.commit()
        summary = f"رسید سفارش VPN #{order.id}\nمدت: {order.duration_days} روز | کاربران: {order.user_count}\nمبلغ: {money(order.price_toman)}\nChat ID: {order.customer_telegram_id}"
    await state.clear()
    buttons = _menu([[('✅ تأیید رسید', f'vpn:review:{order_id}:approve'), ('❌ رد رسید', f'vpn:review:{order_id}:reject')]])
    notified = 0
    settings = get_settings()
    if getattr(settings, "AI_ADMIN_BOT_TOKEN", "") and getattr(settings, "AI_OWNER_TELEGRAM_ID", 0):
        from aiogram import Bot
        from aiogram.client.default import DefaultBotProperties
        from aiogram.enums import ParseMode

        admin_bot = Bot(settings.AI_ADMIN_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        try:
            await admin_bot.send_message(
                settings.AI_OWNER_TELEGRAM_ID,
                summary + "\nبرای مشاهدهٔ رسید و بررسی سفارش، دکمهٔ زیر را بزنید.",
                reply_markup=_menu([[("🧾 مشاهده و بررسی سفارش", f"ai:order:{order_id}")]]),
            )
            notified = 1
        except Exception as exc:
            logger.warning("AI owner receipt notification failed for order {}: {}", order_id, type(exc).__name__)
        finally:
            await admin_bot.session.close()
    else:
        for admin_id in _vpn_admin_ids():
            try:
                if kind == "photo":
                    await message.bot.send_photo(admin_id, file_id, caption=summary, reply_markup=buttons)
                else:
                    await message.bot.send_document(admin_id, file_id, caption=summary, reply_markup=buttons)
                notified += 1
            except Exception as exc:
                logger.warning("VPN receipt notification failed for order {}: {}", order_id, type(exc).__name__)
    owner_id = getattr(settings, "AI_OWNER_TELEGRAM_ID", 0)
    support = f'<a href="tg://user?id={owner_id}">پشتیبانی کیان AI</a>' if owner_id else "پشتیبانی راینوتک"
    await message.answer(
        "✅ رسید شما در سیستم ثبت شد. لطفاً منتظر بمانید. نتیجه همین‌جا برای شما ارسال می‌شود.\n\n"
        "⚠️ اگر بیش از یک ساعت از زمان ارسال رسید گذشت و اطلاعات اتصال برایتان ارسال نشد، "
        f"لطفاً به {support} پیام بدهید."
        + ("" if notified else "\n\n⚠️ اعلان بررسی رسید ارسال نشد؛ لطفاً به پشتیبانی پیام بدهید."),
        reply_markup=HOME,
    )


@router.callback_query(F.data.startswith("vpn:review:"))
async def vpn_review(callback: CallbackQuery) -> None:
    if callback.from_user.id not in _vpn_admin_ids():
        await callback.answer("فقط ادمین اصلی می‌تواند رسید را بررسی کند.", show_alert=True)
        return
    try:
        _, _, order_raw, decision = callback.data.split(":")
        order_id = int(order_raw)
    except (ValueError, TypeError):
        await callback.answer("درخواست نامعتبر است.", show_alert=True)
        return
    if decision not in {"approve", "reject"}:
        await callback.answer("تصمیم نامعتبر است.", show_alert=True)
        return
    customer_id = await review_vpn_order(order_id, decision)
    if customer_id is None:
        await callback.answer("این رسید قبلاً بررسی شده یا موجود نیست.", show_alert=True)
        return
    await callback.answer("رسید بررسی شد.")
    if decision == "approve":
        result = await _deliver_available_config(order_id, callback.bot)
        if result == "delivered":
            await callback.message.answer(f"✅ رسید سفارش #{order_id} تأیید و لینک اختصاصی خودکار ارسال شد.")
        else:
            await callback.message.answer(
                f"✅ رسید سفارش #{order_id} تأیید شد؛ تحویل هنوز انجام نشده ({result}). "
                "برای افزودن لینک متناسب با طرح، از /vpn_config_add استفاده کنید."
            )
            try:
                await callback.bot.send_message(customer_id, f"✅ پرداخت سفارش VPN <code>#{order_id}</code> تأیید شد. لینک اتصال پس از آماده‌سازی برایتان ارسال می‌شود.")
            except Exception as exc:
                logger.warning("VPN approval notice failed for order {}: {}", order_id, type(exc).__name__)
    else:
        await callback.bot.send_message(customer_id, f"رسید سفارش VPN <code>#{order_id}</code> تأیید نشد. برای بررسی یا ارسال دوبارهٔ رسید، از بخش «اکانت‌های من» اقدام کنید.")


@router.message(Command("vpn_config_add"))
async def vpn_config_add(message: Message, command: CommandObject) -> None:
    if not message.from_user or message.from_user.id not in _vpn_admin_ids():
        await message.answer("این فرمان فقط برای ادمین اصلی است.")
        return
    parts = (command.args or "").split(maxsplit=2)
    if len(parts) != 3:
        await message.answer("روش استفاده: /vpn_config_add مدت_روز تعداد_کاربر لینک_اختصاصی")
        return
    try:
        duration, users = int(parts[0]), int(parts[1])
        if users not in SOURCE_PRICES[duration]:
            raise ValueError
    except (ValueError, KeyError):
        await message.answer("مدت یا تعداد کاربر معتبر نیست.")
        return
    result, order_id = await _register_config(duration, users, parts[2].strip(), message.bot)
    if result == "invalid":
        await message.answer("لینک اشتراک باید HTTPS معتبر باشد.")
    elif result == "duplicate":
        await message.answer("این لینک قبلاً ثبت شده است؛ هر لینک فقط یک بار قابل تحویل است.")
    elif result == "delivered":
        await message.answer(f"✅ لینک اختصاصی ثبت و به سفارش #{order_id} تحویل شد.")
    elif result == "send_failed":
        await message.answer(f"لینک برای سفارش #{order_id} رزرو شد، اما ارسال ناموفق بود. پس از رفع مشکل /vpn_config_retry {order_id} را بزنید.")
    else:
        await message.answer("✅ یک لینک اختصاصی برای این طرح ثبت شد و آمادهٔ تحویل پس از تأیید رسید است.")


async def _register_config(
    duration: int, users: int, url: str, customer_bot,
    ssh_details: dict[str, str | int] | None = None,
    deliver_pending: bool = True,
) -> tuple[str, int | None]:
    parsed = urlparse(url)
    if (duration not in SOURCE_PRICES or users not in SOURCE_PRICES[duration]
            or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or len(url) > 2500):
        return "invalid", None
    async with AsyncSessionLocal() as session:
        config = VpnConfig(duration_days=duration, user_count=users, connection_url=url,
                           url_hash=sha256(url.encode("utf-8")).hexdigest())
        session.add(config)
        try:
            await session.flush()
            if ssh_details:
                session.add(VpnConfigConnection(config_id=config.id, **ssh_details))
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return "duplicate", None
        pending = (await session.scalars(select(VpnOrder.id).where(
            VpnOrder.status == "approved", VpnOrder.duration_days == duration,
            VpnOrder.user_count == users,
        ).order_by(VpnOrder.reviewed_at, VpnOrder.id))).all()
    if not deliver_pending:
        return "available", None
    for order_id in pending:
        result = await _deliver_available_config(order_id, customer_bot)
        if result in {"delivered", "send_failed"}:
            return result, order_id
    return "available", None


@router.message(Command("vpn_config_retry"))
async def vpn_config_retry(message: Message, command: CommandObject) -> None:
    if not message.from_user or message.from_user.id not in _vpn_admin_ids():
        await message.answer("این فرمان فقط برای ادمین اصلی است.")
        return
    if not (command.args or "").strip().isdigit():
        await message.answer("روش استفاده: /vpn_config_retry شناسه_سفارش")
        return
    order_id = int(command.args.strip())
    result = await _deliver_available_config(order_id, message.bot)
    await message.answer(f"نتیجهٔ تحویل سفارش #{order_id}: {result}")


@router.message(Command("vpn_orders"))
@router.message(F.text == "🧾 سفارش‌های VPN")
async def vpn_admin_orders(message: Message) -> None:
    if not message.from_user or message.from_user.id not in _vpn_admin_ids():
        await message.answer("این بخش فقط برای ادمین اصلی است.")
        return
    async with AsyncSessionLocal() as session:
        orders = list((await session.scalars(select(VpnOrder).where(
            VpnOrder.status.in_(("pending_review", "approved", "awaiting_receipt"))
        ).order_by(VpnOrder.id.desc()).limit(20))).all())
    if not orders:
        await message.answer("سفارش VPN باز برای بررسی وجود ندارد.")
        return
    rows = [[(f"#{order.id} · {order.status} · {money(order.price_toman)}", f"vpn:admin:order:{order.id}")] for order in orders]
    await message.answer("🧾 سفارش‌های باز VPN | ۲۰ مورد آخر", reply_markup=_menu(rows))


@router.callback_query(F.data.startswith("vpn:admin:order:"))
async def vpn_admin_order(callback: CallbackQuery) -> None:
    if callback.from_user.id not in _vpn_admin_ids():
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
    summary = (f"سفارش VPN #{order.id}\nChat ID مشتری: <code>{order.customer_telegram_id}</code>\n"
               f"{order.duration_days} روز · {order.user_count} کاربر · {money(order.price_toman)}\nوضعیت: {escape(order.status)}")
    if order.status == "pending_review" and order.receipt_file_id:
        buttons = _menu([[('✅ تأیید رسید', f'vpn:review:{order.id}:approve'), ('❌ رد رسید', f'vpn:review:{order.id}:reject')]])
        if order.receipt_kind == "photo":
            await callback.message.answer_photo(order.receipt_file_id, caption=summary, reply_markup=buttons)
        else:
            await callback.message.answer_document(order.receipt_file_id, caption=summary, reply_markup=buttons)
    else:
        if order.status == "approved":
            summary += f"\nبرای تحویل خودکار: <code>/vpn_config_add {order.duration_days} {order.user_count} لینک_اختصاصی</code>"
        await callback.message.answer(summary)


@router.message(Command("vpn_deliver"))
async def vpn_deliver(message: Message, command: CommandObject) -> None:
    if not message.from_user or message.from_user.id not in _vpn_admin_ids():
        await message.answer("این فرمان فقط برای ادمین اصلی است.")
        return
    parts = (command.args or "").split(maxsplit=1)
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].strip():
        await message.answer("روش استفاده: /vpn_deliver شناسه_سفارش متن_اتصال")
        return
    order_id, connection = int(parts[0]), parts[1].strip()
    if len(connection) > 2500:
        await message.answer("متن اتصال بیش از حد طولانی است.")
        return
    if urlparse(connection).scheme in {"http", "https"}:
        await message.answer("لینک اشتراک را با /vpn_config_add ثبت کنید تا به یک سفارش اختصاصی تخصیص یابد.")
        return
    async with AsyncSessionLocal() as session:
        order = await session.get(VpnOrder, order_id)
        if order is None or order.status != "approved":
            await message.answer("سفارش تأییدشدهٔ قابل تحویل پیدا نشد.")
            return
        customer_id = order.customer_telegram_id
        try:
            await message.bot.send_message(customer_id, f"🚀 اکانت VPN سفارش <code>#{order_id}</code> آماده است:\n{_connection_text(connection)}\n\nبرای راه‌اندازی، از بخش پشتیبانی راهنمایی بگیرید.")
        except Exception as exc:
            logger.warning("VPN delivery failed for order {}: {}", order_id, type(exc).__name__)
            await message.answer("ارسال به مشتری ناموفق بود؛ سفارش تأییدشده باقی ماند تا دوباره تلاش کنید.")
            return
        order.delivery_text = connection
        order.delivered_at = datetime.utcnow()
        order.status = "delivered"
        await session.commit()
    await message.answer(f"✅ اطلاعات اتصال سفارش #{order_id} ارسال شد.")


@router.callback_query(F.data == "vpn:accounts")
async def vpn_accounts(callback: CallbackQuery) -> None:
    async with AsyncSessionLocal() as session:
        orders = list((await session.scalars(select(VpnOrder).where(VpnOrder.customer_telegram_id == callback.from_user.id).order_by(VpnOrder.id.desc()).limit(15))).all())
    await callback.answer()
    if not orders:
        await callback.message.answer("هنوز سفارش VPN ثبت نکرده‌اید.", reply_markup=HOME)
        return
    status = {"awaiting_receipt": "منتظر رسید", "pending_review": "در حال بررسی رسید", "approved": "پرداخت تأییدشده؛ در انتظار تحویل", "rejected": "رسید ردشده", "delivered": "تحویل‌شده"}
    buttons = []
    for order in orders:
        buttons.append([(f"#{order.id} · {order.duration_days} روز · {status.get(order.status, order.status)}", f"vpn:account:{order.id}")])
    buttons.append([("🔙 بازگشت", "vpn:home")])
    await callback.message.answer("📱 سفارش‌ها و اکانت‌های من\n۱۵ سفارش آخر:", reply_markup=_menu(buttons))


@router.callback_query(F.data.startswith("vpn:account:"))
async def vpn_account_detail(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        order_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer("سفارش نامعتبر است.", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        order = await session.get(VpnOrder, order_id)
    if order is None or order.customer_telegram_id != callback.from_user.id:
        await callback.answer("به این سفارش دسترسی ندارید.", show_alert=True)
        return
    await callback.answer()
    text = f"سفارش VPN <code>#{order.id}</code>\nمدت: {order.duration_days} روز | کاربران: {order.user_count}\nمبلغ: {money(order.price_toman)}\nوضعیت: {escape(order.status)}"
    buttons = []
    if order.status in {"awaiting_receipt", "rejected"}:
        await state.set_state(VpnState.awaiting_receipt)
        await state.update_data(vpn_order_id=order.id)
        text += "\n\nبرای این سفارش، تصویر رسید را همین‌جا ارسال کنید."
        buttons.append([("💳 مشاهده شماره کارت", f"vpn:card:{order.id}")])
    if order.status == "delivered" and order.delivery_text:
        text += f"\n\nاطلاعات اتصال:\n{_connection_text(order.delivery_text)}"
    buttons.append([("🔙 سفارش‌های من", "vpn:accounts")])
    await callback.message.answer(text, reply_markup=_menu(buttons))


@router.callback_query(F.data == "vpn:support")
async def vpn_support(callback: CallbackQuery) -> None:
    from bot.services.support_service import open_support_request

    async with AsyncSessionLocal() as session:
        request = await open_support_request(session, callback.from_user.id)
    await callback.answer()
    await callback.message.answer(f"💬 درخواست پشتیبانی شما با شمارهٔ <code>#{request.id}</code> ثبت شد. برای پیگیری، از گزینهٔ «کارشناس و پشتیبانی آنلاین» در منوی راینوتک استفاده کنید.", reply_markup=HOME)


@router.callback_query(F.data == "vpn:trial")
async def vpn_trial(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer("🧪 دریافت اکانت تست به فعال‌سازی اکانت در سرور VPN نیاز دارد. درخواست خود را از گزینهٔ پشتیبانی ارسال کنید؛ تا قبل از دریافت اکانت واقعی، تستی فعال نشده است.", reply_markup=_menu([[('💬 درخواست پشتیبانی', 'vpn:support')], [('🔙 بازگشت', 'vpn:home')]]))


@router.callback_query(F.data == "vpn:wallet")
async def vpn_wallet(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer(
        "💰 <b>کیف پول</b>\nدر حال حاضر پرداخت طرح‌ها مستقیم به کارت انجام می‌شود و موجودی کیف پولی برای این حساب ثبت نشده است. "
        "برای خرید یا تمدید، طرح را انتخاب کنید و پس از واریز تصویر رسید را بفرستید.",
        reply_markup=_menu([[('🛍 خرید / تمدید', 'vpn:durations')], [('🔙 بازگشت', 'vpn:home')]]),
    )


@router.callback_query(F.data == "vpn:guide")
async def vpn_guide(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer(
        "📕 <b>راهنمای نصب</b>\nپس از تأیید پرداخت، لینک یا اطلاعات اتصال مخصوص طرح شما فرستاده می‌شود. "
        "آن را در برنامهٔ سازگار با نوع کانفیگ وارد کنید. نام برنامه و راهنمای دقیق دستگاه شما همراه اطلاعات اتصال یا از پشتیبانی اعلام می‌شود.",
        reply_markup=_menu([[('👩🏻‍💻 پشتیبانی', 'vpn:support')], [('🔙 بازگشت', 'vpn:home')]]),
    )


@router.callback_query(F.data == "vpn:troubleshoot")
async def vpn_troubleshoot(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer(
        "🛠 <b>رفع مشکل اتصال</b>\nابتدا اتصال اینترنت، فعال‌بودن طرح و درست واردشدن لینک اتصال را بررسی کنید. "
        "اگر مشکل باقی ماند، شناسهٔ سفارش و نوع دستگاه را برای پشتیبانی بفرستید تا راهنمایی اختصاصی دریافت کنید.",
        reply_markup=_menu([[('👩🏻‍💻 پشتیبانی', 'vpn:support')], [('📱 اکانت‌های من', 'vpn:accounts')], [('🔙 بازگشت', 'vpn:home')]]),
    )


@router.callback_query(F.data == "vpn:locations")
async def vpn_locations(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer(
        "📍 <b>لوکیشن‌های اتصال</b>\nسرور و لوکیشن هر اکانت هنگام تحویل اطلاعات اتصال اعلام می‌شود. "
        "فهرست سرورهای فعال هنوز از ارائه‌دهنده به این بات متصل نشده است؛ برای انتخاب لوکیشن با پشتیبانی هماهنگ کنید.",
        reply_markup=_menu([[('👩🏻‍💻 پشتیبانی', 'vpn:support')], [('🔙 بازگشت', 'vpn:home')]]),
    )


@router.message(VpnState.awaiting_receipt)
async def vpn_receipt_required(message: Message) -> None:
    await message.answer("لطفاً تصویر رسید واریز را بفرستید یا با /start به منوی اصلی برگردید.")

from __future__ import annotations

from datetime import datetime, timedelta
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup
from sqlalchemy import func, or_, select

from bot.config import get_settings
from bot.keyboards.reply_menus import main_menu_kb
from bot.handlers.catalog_browser import start_catalog
from database.models import Branch, Laptop, LaptopStaffOverride, LaptopVariant, ProductImage, StaffActivity, User
from database.session import AsyncSessionLocal

router = Router()
CUSTOMER = "ورود مشتریان"
STAFF = "ورود کارشناسان و مدیران فروش"
ADMIN = "ورود ادمین"


class PanelState(StatesGroup):
    contact = State()
    staff_lookup = State()
    product_lookup = State()
    product_value = State()


def entry_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=CUSTOMER)],
        [KeyboardButton(text=STAFF)],
        [KeyboardButton(text=ADMIN)],
    ], resize_keyboard=True)


@router.message(Command("start"))
async def start_role_selection(message: Message, state: FSMContext, current_user: User | None = None) -> None:
    await state.clear()
    payload = (getattr(message, "text", None) or "").split(maxsplit=1)
    if current_user and len(payload) == 2 and payload[1].startswith("ref_"):
        try:
            referrer_id = int(payload[1][4:])
        except ValueError:
            referrer_id = 0
        if referrer_id and referrer_id != current_user.telegram_id and not current_user.referrer_telegram_id:
            async with AsyncSessionLocal() as session:
                expert = await session.scalar(select(User).where(User.telegram_id == referrer_id, User.is_active.is_(True), User.role.in_(("seller", "branch_manager"))))
                user = await session.get(User, current_user.id)
                if expert and user and not user.referrer_telegram_id:
                    user.referrer_telegram_id = referrer_id
                    await session.commit()
    await message.answer(
        "به راینوتک (RAINOTEK) خوش آمدید! 💻\nلپ‌تاپ‌های نو و استوک را با راهنمایی تیم فروش ما پیدا کنید. برای ادامه، بخش مورد نظر را انتخاب کنید:",
        reply_markup=entry_menu_kb(),
    )


@router.message(Command("cancel"))
async def cancel_panel_flow(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("عملیات لغو شد. بخش مورد نظر را انتخاب کنید:", reply_markup=entry_menu_kb())


def _is_admin(user: User | None) -> bool:
    return bool(user and (user.telegram_id in get_settings().ADMIN_TELEGRAM_IDS or user.role == "admin"))


def _is_staff(user: User | None) -> bool:
    return bool(user and (_is_admin(user) or user.role in {"branch_manager", "seller"}))


def _staff_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="✏️ ویرایش محصول"), KeyboardButton(text="💻 کاتالوگ و مشخصات")],
        [KeyboardButton(text="🔗 لینک معرفی من"), KeyboardButton(text="📋 رزروهای ارجاعی من")],
        [KeyboardButton(text="بازگشت به منوی اصلی")],
    ], resize_keyboard=True)


def _admin_menu() -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="📊 داشبورد CRM"), KeyboardButton(text="👥 دسترسی پرسنل")],
        [KeyboardButton(text="👥 مدیران و کارشناسان"), KeyboardButton(text="🏢 عملکرد شعب")],
        [KeyboardButton(text="📈 ورودی‌های روزانه")],
        [KeyboardButton(text="📋 رزروهای مشتریان")],
        [KeyboardButton(text="✏️ ویرایش محصول"), KeyboardButton(text="💻 کاتالوگ و مشخصات")],
        [KeyboardButton(text="بازگشت به منوی اصلی")],
    ]
    if not (get_settings().AI_ADMIN_BOT_TOKEN and get_settings().AI_OWNER_TELEGRAM_ID):
        rows.insert(3, [KeyboardButton(text="🧾 سفارش‌های VPN")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


@router.message(F.text == CUSTOMER)
async def customer_entry(message: Message, state: FSMContext, current_user: User) -> None:
    await state.clear()
    if current_user.phone_number:
        await message.answer(f"سلام {escape(message.from_user.first_name or 'دوست عزیز')} عزیز، به راینوتک خوش آمدی! 💻", reply_markup=main_menu_kb("customer"))
        return
    await state.set_state(PanelState.contact)
    await message.answer(
        "✨ به راینوتک (RAINOTEK)، مرجع لپ‌تاپ‌های نو و استوک خوش آمدید! برای دیدن مدل‌ها و پیدا کردن لپ‌تاپ مناسب، شمارهٔ خودتان را با دکمهٔ زیر به اشتراک بگذارید.",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="📱 اشتراک‌گذاری شماره من", request_contact=True)]], resize_keyboard=True, one_time_keyboard=True),
    )


@router.message(PanelState.contact, F.contact)
async def receive_customer_contact(message: Message, state: FSMContext, current_user: User) -> None:
    contact = message.contact
    if not message.from_user or contact.user_id != message.from_user.id:
        await message.answer("لطفاً شمارهٔ خودتان را با دکمهٔ اشتراک‌گذاری شماره من ارسال کنید.")
        return
    digits = "".join(str(int(char)) for char in contact.phone_number if char.isdigit())
    if digits.startswith("98") and len(digits) == 12:
        digits = "0" + digits[2:]
    elif len(digits) == 10 and digits.startswith("9"):
        digits = "0" + digits
    if len(digits) != 11 or not digits.startswith("09"):
        await message.answer("شمارهٔ همراه معتبر نیست؛ لطفاً شمارهٔ موبایل خودتان را دوباره به اشتراک بگذارید.")
        return
    async with AsyncSessionLocal() as session:
        user = await session.get(User, current_user.id)
        user.first_name = message.from_user.first_name
        user.phone_number = digits
        user.joined_at = user.joined_at or datetime.utcnow()
        user.crm_stage = user.crm_stage or "new_lead"
        user.warranty_stage = user.warranty_stage or "not_started"
        await session.commit()
    await state.clear()
    await message.answer(f"سلام {escape(message.from_user.first_name or 'دوست عزیز')} عزیز، به راینوتک خوش آمدی! 💻\nلپ‌تاپ را بیشتر برای چه کاری لازم داری؟ از کاتالوگ شروع کن یا مدل‌ها را جست‌وجو کن.", reply_markup=main_menu_kb("customer"))


@router.message(PanelState.contact)
async def require_own_contact(message: Message) -> None:
    await message.answer("برای ادامه، دکمهٔ «📱 اشتراک‌گذاری شماره من» را لمس کنید.")


@router.message(F.text == STAFF)
async def staff_entry(message: Message, state: FSMContext, current_user: User) -> None:
    await state.clear()
    if not _is_staff(current_user):
        await message.answer("دسترسی پرسنل فروش برای این حساب ثبت نشده است.", reply_markup=entry_menu_kb())
        return
    await message.answer("پنل کارشناسان و مدیران فروش راینوتک", reply_markup=_staff_menu())


@router.message(F.text == "🔗 لینک معرفی من")
async def staff_referral_link(message: Message, current_user: User) -> None:
    if not _is_staff(current_user):
        await message.answer("دسترسی مجاز نیست.")
        return
    bot_info = await message.bot.get_me()
    await message.answer(f"🔗 لینک اختصاصی معرفی شما:\nhttps://t.me/{bot_info.username}?start=ref_{current_user.telegram_id}\n\nمشتری با ورود از این لینک به نام شما ثبت می‌شود.", disable_web_page_preview=True)


@router.message(F.text == ADMIN)
async def admin_entry(message: Message, state: FSMContext, current_user: User) -> None:
    await state.clear()
    if not _is_admin(current_user):
        await message.answer("دسترسی ادمین برای این حساب ثبت نشده است.", reply_markup=entry_menu_kb())
        return
    await message.answer("پنل ادمین راینوتک", reply_markup=_admin_menu())


@router.message(F.text == "بازگشت به منوی اصلی")
async def panel_exit(message: Message, state: FSMContext, current_user: User) -> None:
    await state.clear()
    await message.answer("منوی اصلی راینوتک", reply_markup=main_menu_kb(current_user.role))


@router.message(F.text == "📊 داشبورد CRM")
async def crm_dashboard(message: Message, current_user: User) -> None:
    if not _is_admin(current_user):
        await message.answer("دسترسی مجاز نیست.")
        return
    today = datetime.utcnow().date()
    start = datetime.combine(today, datetime.min.time())
    end = start + timedelta(days=1)
    async with AsyncSessionLocal() as session:
        count = await session.scalar(select(func.count(User.id)).where(User.joined_at >= start, User.joined_at < end))
        rows = (await session.scalars(select(User).where(User.joined_at >= start, User.joined_at < end).order_by(User.joined_at.desc()).limit(20))).all()
    lines = [f"📊 ورودی‌های جدید CRM امروز (UTC): {count or 0}"]
    lines.extend(f"• {escape(u.first_name or u.full_name or 'بدون نام')} | <code>{escape(u.phone_number or '-')}</code> | {u.joined_at:%H:%M} | معرف: <code>{u.referrer_telegram_id or '-'}</code>" for u in rows)
    if (count or 0) > 20:
        lines.append("۲۰ مورد آخر نمایش داده شد.")
    await message.answer("\n".join(lines))


@router.message(F.text == "👥 دسترسی پرسنل")
async def staff_access_start(message: Message, state: FSMContext, current_user: User) -> None:
    if not _is_admin(current_user):
        await message.answer("دسترسی مجاز نیست.")
        return
    await state.set_state(PanelState.staff_lookup)
    await message.answer("شمارهٔ موبایل، Chat ID یا @نام‌کاربری تلگرامِ شخصی را که قبلاً ربات را شروع کرده بفرستید.")


@router.message(PanelState.staff_lookup)
async def staff_access_lookup(message: Message, state: FSMContext, current_user: User) -> None:
    if not _is_admin(current_user):
        await state.clear()
        return
    value = (message.text or "").strip()
    phone = "".join(str(int(c)) for c in value if c.isdigit())
    if phone.startswith("98") and len(phone) == 12:
        phone = "0" + phone[2:]
    if len(phone) == 10 and phone.startswith("9"):
        phone = "0" + phone
    async with AsyncSessionLocal() as session:
        conditions = []
        if value.lstrip("-").isdigit():
            conditions.append(User.telegram_id == int(value))
        if value.startswith("@"):
            conditions.append(func.lower(User.username) == value[1:].lower())
        if len(phone) == 11 and phone.startswith("09"):
            conditions.append(User.phone_number == phone)
        user = await session.scalar(select(User).where(or_(*conditions))) if conditions else None
    if user is None:
        await message.answer("کاربر پیدا نشد؛ ابتدا باید /start را در همین ربات اجرا و برای جست‌وجو با شماره، شمارهٔ خودش را ثبت کند.")
        return
    await state.clear()
    await message.answer(f"کاربر: {escape(user.full_name or user.username or str(user.telegram_id))}\nنقش فعلی: {escape(user.role)}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="مدیر فروش", callback_data=f"panel:role:{user.id}:branch_manager"), InlineKeyboardButton(text="کارشناس فروش", callback_data=f"panel:role:{user.id}:seller")],
        [InlineKeyboardButton(text="عزل از پرسنل", callback_data=f"panel:role:{user.id}:customer")],
    ]))


@router.callback_query(F.data.startswith("panel:role:"))
async def assign_staff_role(callback, current_user: User) -> None:
    if not _is_admin(current_user):
        await callback.answer("دسترسی مجاز نیست.", show_alert=True)
        return
    try:
        _, _, user_id, role = callback.data.split(":")
        user_id = int(user_id)
    except (ValueError, TypeError):
        await callback.answer("درخواست نامعتبر است.", show_alert=True)
        return
    if role not in {"branch_manager", "seller", "customer"}:
        await callback.answer("نقش نامعتبر است.", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if user is None or user.telegram_id in get_settings().ADMIN_TELEGRAM_IDS or user.role == "admin":
            await callback.answer("تغییر این حساب مجاز نیست.", show_alert=True)
            return
        if role == "branch_manager":
            branches = list((await session.scalars(select(Branch).where(Branch.is_active.is_(True)).order_by(Branch.id))).all())
            if not branches:
                await callback.answer("شعبهٔ فعالی برای انتساب پیدا نشد.", show_alert=True)
                return
            await callback.answer()
            await callback.message.answer("شعبهٔ مدیر فروش را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=branch.name, callback_data=f"panel:branch:{user_id}:{branch.id}")] for branch in branches
            ]))
            return
        user.role = role
        if role == "customer":
            user.managed_branch_id = None
        session.add(StaffActivity(actor_telegram_id=current_user.telegram_id, action="role_change", target_id=user.id, detail=role))
        await session.commit()
    await callback.answer("دسترسی به‌روز شد.")
    await callback.message.answer("نقش کاربر با موفقیت به‌روز شد.")


@router.callback_query(F.data.startswith("panel:branch:"))
async def assign_manager_branch(callback, current_user: User) -> None:
    if not _is_admin(current_user):
        await callback.answer("دسترسی مجاز نیست.", show_alert=True)
        return
    try:
        _, _, user_id, branch_id = callback.data.split(":")
        user_id, branch_id = int(user_id), int(branch_id)
    except (ValueError, TypeError):
        await callback.answer("درخواست نامعتبر است.", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        branch = await session.get(Branch, branch_id)
        if user is None or branch is None or not branch.is_active or user.role == "admin" or user.telegram_id in get_settings().ADMIN_TELEGRAM_IDS:
            await callback.answer("کاربر یا شعبه معتبر نیست.", show_alert=True)
            return
        user.role = "branch_manager"
        user.managed_branch_id = branch.id
        session.add(StaffActivity(actor_telegram_id=current_user.telegram_id, action="role_change", target_id=user.id, detail=f"branch_manager:{branch.code}"))
        await session.commit()
    await callback.answer("مدیر فروش به شعبه منصوب شد.")
    await callback.message.answer(f"نقش مدیر فروش برای شعبهٔ {escape(branch.name)} ثبت شد.")


@router.message(F.text == "✏️ ویرایش محصول")
async def product_edit_start(message: Message, state: FSMContext, current_user: User) -> None:
    if not _is_staff(current_user):
        await message.answer("دسترسی مجاز نیست.")
        return
    await state.set_state(PanelState.product_lookup)
    await message.answer("شناسهٔ محصول یا بخشی از نام مدل را بفرستید.")


@router.message(PanelState.product_lookup)
async def product_lookup(message: Message, state: FSMContext, current_user: User) -> None:
    if not _is_staff(current_user):
        await state.clear()
        return
    query = (message.text or "").strip()
    if not query:
        await message.answer("نام یا شناسهٔ مدل را بفرستید.")
        return
    async with AsyncSessionLocal() as session:
        stmt = select(Laptop).where(Laptop.status == "active")
        stmt = stmt.where(Laptop.id == int(query)) if query.isdigit() else stmt.where(Laptop.model.ilike(f"%{query[:80]}%"))
        laptops = (await session.scalars(stmt.limit(12))).all()
    if not laptops:
        await message.answer("مدلی پیدا نشد؛ نام دقیق‌تر یا شناسهٔ محصول را بفرستید.")
        return
    await state.clear()
    await message.answer("مدل مورد نظر را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{laptop.id} · {laptop.model}"[:60], callback_data=f"panel:edit:{laptop.id}")] for laptop in laptops
    ]))


@router.callback_query(F.data.startswith("panel:edit:"))
async def choose_edit_product(callback, current_user: User) -> None:
    if not _is_staff(current_user):
        await callback.answer("دسترسی مجاز نیست.", show_alert=True)
        return
    try:
        laptop_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer("شناسه نامعتبر است.", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        laptop = await session.get(Laptop, laptop_id)
    if laptop is None:
        await callback.answer("محصول پیدا نشد.", show_alert=True)
        return
    fields = {"cpu": "CPU", "ram": "RAM", "storage": "Storage", "gpu": "GPU", "screen_size": "صفحه‌نمایش", "color": "رنگ", "condition": "وضعیت", "price": "قیمت", "photo": "عکس واقعی"}
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f"panel:field:{laptop_id}:{field}")] for field, label in fields.items()
    ] + [[InlineKeyboardButton(text="دیدن در کاتالوگ", callback_data=f"tree:laptop:{laptop_id}")]])
    await callback.answer()
    await callback.message.answer(f"✏️ {escape(laptop.model)}\nفیلد مورد نظر را انتخاب کنید:", reply_markup=keyboard)


@router.callback_query(F.data.startswith("panel:field:"))
async def choose_edit_field(callback, state: FSMContext, current_user: User) -> None:
    if not _is_staff(current_user):
        await callback.answer("دسترسی مجاز نیست.", show_alert=True)
        return
    try:
        _, _, laptop_id, field = callback.data.split(":")
        laptop_id = int(laptop_id)
    except (ValueError, TypeError):
        await callback.answer("درخواست نامعتبر است.", show_alert=True)
        return
    if field not in {"cpu", "ram", "storage", "gpu", "screen_size", "color", "condition", "price", "photo"}:
        await callback.answer("فیلد نامعتبر است.", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        laptop = await session.get(Laptop, laptop_id)
    if laptop is None:
        await callback.answer("مدل پیدا نشد.", show_alert=True)
        return
    await state.set_state(PanelState.product_value)
    await state.update_data(laptop_id=laptop_id, edit_field=field)
    await callback.answer()
    await callback.message.answer("عکس واقعی لپ‌تاپ را ارسال کنید. برای افزودن عکس‌های بعدی، همین مسیر را دوباره انتخاب کنید." if field == "photo" else f"مقدار جدید {field} را بفرستید:")


@router.message(PanelState.product_value)
async def save_product_edit(message: Message, state: FSMContext, current_user: User) -> None:
    if not _is_staff(current_user):
        await state.clear()
        return
    data = await state.get_data()
    field = data.get("edit_field")
    laptop_id = data.get("laptop_id")
    if field == "photo":
        if not message.photo:
            await message.answer("لطفاً یک عکس واقعی لپ‌تاپ ارسال کنید.")
            return
        value = message.photo[-1].file_id
    else:
        value = (message.text or "").strip()
        if not value or len(value) > 120:
            await message.answer("مقدار معتبر و کوتاه‌تر از ۱۲۰ نویسه بفرستید.")
            return
        if field == "price":
            digits = "".join(str(int(c)) for c in value if c.isdigit())
            if not digits or len(digits) > 15 or int(digits) <= 0:
                await message.answer("قیمت معتبر به تومان وارد کنید.")
                return
            value = int(digits)
    async with AsyncSessionLocal() as session:
        laptop = await session.get(Laptop, laptop_id)
        if laptop is None:
            await state.clear()
            await message.answer("مدل پیدا نشد.")
            return
        override = await session.get(LaptopStaffOverride, laptop_id)
        if override is None:
            override = LaptopStaffOverride(laptop_id=laptop_id, edited_by=current_user.telegram_id)
            session.add(override)
        if field == "photo":
            override.image_file_id = value
            laptop.image_url = "tgfile:" + value
            real_images = list((await session.scalars(select(ProductImage).where(ProductImage.laptop_id == laptop_id, ProductImage.image_url.like("tgfile:%")))).all())
            if not any(image.telegram_file_id == value for image in real_images):
                session.add(ProductImage(laptop_id=laptop_id, image_url="tgfile:" + value,
                                         telegram_file_id=value, display_order=len(real_images), is_primary=not real_images))
        else:
            setattr(override, field, value)
            setattr(laptop, field, value)
            if field in {"cpu", "ram", "storage", "gpu", "condition", "price"}:
                variants = (await session.scalars(select(LaptopVariant).where(LaptopVariant.legacy_laptop_id == laptop_id))).all()
                for variant in variants:
                    setattr(variant, field, value)
        override.edited_by = current_user.telegram_id
        override.edited_at = datetime.utcnow()
        session.add(StaffActivity(actor_telegram_id=current_user.telegram_id, action="product_edit", target_id=laptop_id, detail=field))
        await session.commit()
    await state.clear()
    await message.answer("✅ تغییر در کاتالوگ ثبت شد و پس از همگام‌سازی شیت هم حفظ می‌شود.", reply_markup=_admin_menu() if _is_admin(current_user) else _staff_menu())

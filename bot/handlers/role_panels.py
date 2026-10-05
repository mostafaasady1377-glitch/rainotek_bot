from __future__ import annotations

from datetime import datetime, timedelta
from html import escape
import re

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup
from sqlalchemy import func, or_, select, update

from bot.config import get_settings
from bot.keyboards.reply_menus import main_menu_kb
from bot.handlers.catalog_browser import start_catalog
from database.models import Branch, BranchInventory, Laptop, LaptopBrand, LaptopStaffOverride, LaptopVariant, ProductImage, StaffActivity, User
from database.session import AsyncSessionLocal
from bot.services.local_time import format_local, period_bounds
from bot.services.rhinotech_sheet_reader import serialized_sheet
from bot.services.referral_links import get_referral_code, referral_code_id
from database.models import ReferralLink

router = Router()
CUSTOMER = "ورود مشتریان"
STAFF = "ورود کارشناسان و مدیران فروش"
MANAGER = "پنل مدیر شعبه"
SELLER = "پنل کارشناسان فروش"
ADMIN = "پنل مدیریت"


class PanelState(StatesGroup):
    contact = State()
    staff_lookup = State()
    product_lookup = State()
    product_value = State()
    product_confirm = State()


def entry_menu_kb(user=None) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=CUSTOMER)],
        [KeyboardButton(text=MANAGER)],
        [KeyboardButton(text=SELLER)],
    ]
    if _is_admin(user):
        rows.append([KeyboardButton(text=ADMIN)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def _referrer_id_from_payload(payload: str) -> int:
    for prefix in ("rino_", "ref_", "r"):
        if payload.startswith(prefix):
            suffix = payload[len(prefix):]
            return int(suffix) if re.fullmatch(r"[1-9][0-9]{0,18}", suffix) else 0
    return 0


def _short_referrer_user_id(payload: str) -> int:
    match = re.fullmatch(r'r_([1-9][0-9]{0,9})', payload)
    return int(match.group(1)) if match else 0


def referral_payload(user: User) -> str:
    # Use the stable database identity, not the long Telegram chat ID.
    return f'r_{user.id}' if user.id else f'r{user.telegram_id}'


@router.message(Command("start"))
async def start_role_selection(message: Message, state: FSMContext, current_user: User | None = None) -> None:
    await state.clear()
    payload = (getattr(message, "text", None) or "").split(maxsplit=1)
    referral_phone = None
    if current_user and current_user.role == "customer" and len(payload) == 2:
        referrer_id = _referrer_id_from_payload(payload[1])
        short_id = _short_referrer_user_id(payload[1])
        code_id = referral_code_id(payload[1])
        if code_id or short_id or (referrer_id and referrer_id != current_user.telegram_id):
            async with AsyncSessionLocal() as session:
                if code_id:
                    linked_user = select(ReferralLink.expert_user_id).where(ReferralLink.id == code_id).scalar_subquery()
                    identity = User.id == linked_user
                else:
                    identity = User.id == short_id if short_id else User.telegram_id == referrer_id
                expert = await session.scalar(select(User).where(identity, User.is_active.is_(True), User.role.in_(("seller", "branch_manager"))))
                if expert and expert.telegram_id == current_user.telegram_id:
                    expert = None
                if expert:
                    referrer_id = expert.telegram_id
                user = await session.get(User, current_user.id)
                if expert and user and not user.referrer_telegram_id:
                    # Atomic first-touch attribution: a competing /start must not replace it.
                    await session.execute(update(User).where(
                        User.id == user.id, User.role == "customer",
                        User.referrer_telegram_id.is_(None),
                    ).values(referrer_telegram_id=referrer_id))
                    await session.commit()
                    await session.refresh(user)
                # برای مشتری قدیمی نیز فقط شمارهٔ کارشناس ثبت‌شدهٔ خودش نمایش داده شود؛
                # ورود با لینک شخص دیگری معرف قبلی را عوض نمی‌کند.
                assigned_id = user.referrer_telegram_id if user else current_user.referrer_telegram_id
                if assigned_id:
                    assigned_expert = expert if expert and expert.telegram_id == assigned_id else await session.scalar(
                        select(User).where(
                            User.telegram_id == assigned_id,
                            User.is_active.is_(True),
                            User.role.in_(("seller", "branch_manager")),
                        )
                    )
                    if assigned_expert:
                        referral_phone = assigned_expert.phone_number
    welcome = "به راینوتک (RAINOTEK) خوش آمدید! 💻"
    if referral_phone:
        welcome += f"\n\n📞 کارشناس فروش راینو:\n<code>{escape(referral_phone)}</code>"
        from bot.services.sales_contact import telegram_chat_url
        welcome += f'\n<a href="{escape(telegram_chat_url(assigned_expert), quote=True)}">💬 چت تلگرام با کارشناس شما</a>'
    if current_user:
        await set_view_panel(current_user, 'admin' if _is_admin(current_user) else current_user.role)
        await message.answer(welcome)
        await customer_entry(message, state, current_user)
    else:
        await message.answer(welcome, reply_markup=entry_menu_kb())


@router.message(Command("cancel"))
async def cancel_panel_flow(message: Message, state: FSMContext, current_user: User | None = None) -> None:
    await state.clear()
    if current_user:
        await customer_entry(message, state, current_user)
    else:
        await message.answer("عملیات لغو شد.", reply_markup=main_menu_kb("customer"))


def _is_admin(user: User | None) -> bool:
    return bool(user and getattr(user, 'is_active', True) is not False and user.telegram_id in get_settings().ADMIN_TELEGRAM_IDS)


def _is_staff(user: User | None) -> bool:
    return bool(user and getattr(user, 'is_active', True) is not False and (_is_admin(user) or user.role in {"branch_manager", "seller"}))


def _can_edit(user):
    return _is_staff(user) and (_is_admin(user) or getattr(user, 'product_edit_allowed', True) is not False)


def _admin_phones() -> set[str]:
    return {phone.strip() for phone in get_settings().ADMIN_PHONE_NUMBERS.split(',') if phone.strip()}


async def show_account_menu(message, user):
    if not user.is_active:
        await message.answer("این حساب غیرفعال است.")
    elif _is_admin(user):
        title = {'branch_manager':'پنل مدیر شعبه راینوتک', 'seller':'پنل کارشناسان فروش راینوتک', 'customer':'منوی مشتری راینوتک'}.get(getattr(user, 'view_panel', None), 'پنل مدیریت کل راینوتک')
        await message.answer(title, reply_markup=panel_menu(user))
    elif _is_staff(user):
        if user.role == 'branch_manager' and not user.managed_branch_id:
            await message.answer('شعبهٔ شما تعیین نشده است؛ ادمین باید پیش از ورود، شعبه را ثبت کند.')
            return
        await message.answer("پنل مدیر شعبه راینوتک" if user.role == 'branch_manager' else "پنل کارشناسان فروش راینوتک", reply_markup=_staff_menu(user))
    else:
        await message.answer("به راینوتک خوش آمدی! 💻\nاز کاتالوگ شروع کن یا مدل‌ها را جست‌وجو کن.", reply_markup=customer_menu(user))


def _staff_menu(user=None) -> ReplyKeyboardMarkup:
    if user and user.role == 'branch_manager':
        return _manager_menu(user)
    rows = [
        [KeyboardButton(text="✏️ ویرایش محصول"), KeyboardButton(text="💻 کاتالوگ و مشخصات")],
        [KeyboardButton(text="🔗 لینک معرفی من"), KeyboardButton(text="📋 رزروهای ارجاعی من")],
        [KeyboardButton(text="👥 ورودی‌های لینک من")],
        [KeyboardButton(text="👤 مشتریان جدید من"), KeyboardButton(text="📈 گزارش ورودی‌های من")],
        [KeyboardButton(text="بازگشت به منوی اصلی")],
    ]
    if _is_admin(user):
        rows.append([KeyboardButton(text='🔙 بازگشت به پنل مدیریت')])
    if getattr(user, 'accounting_access', False):
        rows.append([KeyboardButton(text='💼 امور مالی')])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def _manager_menu(user=None):
    rows = [
        [KeyboardButton(text='📊 عملکرد کارشناسان شعبه')],
        [KeyboardButton(text='📦 مدل‌ها و موجودی شعبه')],
        [KeyboardButton(text='🔐 دسترسی ویرایش پرسنل'), KeyboardButton(text='✏️ ویرایش محصول')],
        [KeyboardButton(text='💻 کاتالوگ و مشخصات')],
        [KeyboardButton(text='📋 رزروها و سفارش‌های شعبه')],
        [KeyboardButton(text='📥 گزارش عملیات شعبه')],
        [KeyboardButton(text='🔙 انتخاب پنل')],
    ]
    if getattr(user, 'accounting_access', False):
        rows.append([KeyboardButton(text='💼 امور مالی')])
    if _is_admin(user):
        rows.append([KeyboardButton(text='🔙 بازگشت به پنل مدیریت')])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


async def set_view_panel(user, panel):
    if getattr(user, 'id', None):
        async with AsyncSessionLocal() as session:
            saved = await session.get(User, user.id)
            if saved:
                saved.view_panel = panel
                await session.commit()
    user.view_panel = panel


def panel_menu(user):
    if getattr(user, 'view_panel', None) == 'customer':
        return customer_menu(user)
    if getattr(user, 'view_panel', None) == 'branch_manager' or user.role == 'branch_manager':
        return _manager_menu(user)
    if _is_admin(user) and getattr(user, 'view_panel', None) not in {'seller', 'customer'}:
        return _admin_menu()
    return _staff_menu(user)


def customer_menu(user):
    keyboard = main_menu_kb('customer')
    if getattr(user, 'accounting_access', False):
        keyboard.keyboard.append([KeyboardButton(text='💼 امور مالی')])
    if _is_admin(user):
        keyboard.keyboard.append([KeyboardButton(text='🔙 بازگشت به پنل مدیریت')])
    return keyboard


def _admin_menu() -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="📊 داشبورد CRM"), KeyboardButton(text="👥 مدیران و کارشناسان")],
        [KeyboardButton(text="💼 امور مالی"), KeyboardButton(text="🏢 عملکرد شعب")],
        [KeyboardButton(text="✏️ ویرایش محصول"), KeyboardButton(text="💻 کاتالوگ و مشخصات")],
        [KeyboardButton(text="📋 رزروهای مشتریان")],
        [KeyboardButton(text="🔙 انتخاب پنل")],
    ]
    if not (get_settings().AI_ADMIN_BOT_TOKEN and get_settings().AI_OWNER_TELEGRAM_ID):
        rows.insert(3, [KeyboardButton(text="🧾 سفارش‌های VPN")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


@router.message(F.text == CUSTOMER)
async def selected_customer_panel(message: Message, state: FSMContext, current_user: User) -> None:
    await state.clear()
    await set_view_panel(current_user, 'customer')
    if not current_user.is_active:
        await message.answer("این حساب غیرفعال است.")
        return
    await message.answer("منوی مشتری راینوتک", reply_markup=customer_menu(current_user))


async def customer_entry(message: Message, state: FSMContext, current_user: User) -> None:
    await state.clear()
    if current_user.role == 'customer':
        await message.answer('به راینوتک خوش آمدی! 💻\nگزینهٔ موردنظر را انتخاب کن.', reply_markup=customer_menu(current_user))
        return
    if current_user.phone_number and not (current_user.phone_number in _admin_phones() and not _is_admin(current_user)):
        await show_account_menu(message, current_user)
        return
    await state.set_state(PanelState.contact)
    await message.answer(
        "✨ به راینوتک (RAINOTEK)، مرجع لپ‌تاپ‌های نو و استوک خوش آمدید! برای دیدن مدل‌ها و پیدا کردن لپ‌تاپ مناسب، شمارهٔ خودتان را با دکمهٔ زیر به اشتراک بگذارید.",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="📱 اشتراک‌گذاری شماره من", request_contact=True)]], resize_keyboard=True, one_time_keyboard=True),
    )


@router.message(PanelState.contact, F.contact)
async def receive_customer_contact(message: Message, state: FSMContext, current_user: User) -> None:
    contact = message.contact
    if not message.from_user or contact.user_id != message.from_user.id or current_user.telegram_id != message.from_user.id:
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
    from bot.services.stock_lock import stock_lock
    async with stock_lock, AsyncSessionLocal() as session:
        user = await session.get(User, current_user.id)
        if user is None or not user.is_active:
            await message.answer("این حساب غیرفعال است.")
            return
        if digits in _admin_phones():
            if user.telegram_id not in get_settings().ADMIN_TELEGRAM_IDS:
                await message.answer('این شماره مجوز انتقال دسترسی مدیریت به حساب دیگری را ایجاد نمی‌کند.')
                return
            bound = await session.scalar(select(User).where(User.phone_number == digits, User.role == 'admin', User.id != user.id))
            if bound:
                await message.answer("این شماره قبلاً به حساب مدیر دیگری متصل شده است؛ برای انتقال دسترسی با مدیر فعلی هماهنگ کنید.")
                return
            user.role = 'admin'
            user.view_panel = 'admin'
            session.add(StaffActivity(actor_telegram_id=user.telegram_id, action='role_change', target_id=user.id, detail='admin:verified_contact'))
        user.first_name = message.from_user.first_name
        user.phone_number = digits
        user.joined_at = user.joined_at or datetime.utcnow()
        user.crm_stage = user.crm_stage or "new_lead"
        user.warranty_stage = user.warranty_stage or "not_started"
        await session.commit()
    selected = (await state.get_data()).get('customer_panel', False)
    await state.clear()
    current_user.phone_number = digits
    current_user.role = user.role
    current_user.view_panel = user.view_panel
    if selected:
        await set_view_panel(current_user, 'customer')
        await message.answer("منوی مشتری راینوتک", reply_markup=customer_menu(user))
    else:
        await show_account_menu(message, user)


@router.message(PanelState.contact)
async def require_own_contact(message: Message) -> None:
    await message.answer("برای ادامه، دکمهٔ «📱 اشتراک‌گذاری شماره من» را لمس کنید.")


@router.message(F.text.in_({STAFF, SELLER}))
async def staff_entry(message: Message, state: FSMContext, current_user: User) -> None:
    await state.clear()
    if current_user.role == 'branch_manager' and getattr(message, 'text', None) == STAFF:
        await manager_entry(message, state, current_user)
        return
    if not _is_staff(current_user) or (message.text == SELLER and not (_is_admin(current_user) or current_user.role == 'seller')):
        await message.answer("دسترسی پرسنل فروش برای این حساب ثبت نشده است.", reply_markup=main_menu_kb("customer"))
        return
    async with AsyncSessionLocal() as session:
        count = await session.scalar(select(func.count(User.id)).where(
            User.role == 'customer', User.referrer_telegram_id == current_user.telegram_id,
        )) or 0
    await set_view_panel(current_user, 'seller')
    await message.answer(f"پنل کارشناسان فروش راینوتک\n👥 ورودی‌های لینک شما: {count} مشتری", reply_markup=_staff_menu(current_user))


@router.message(F.text == MANAGER)
async def manager_entry(message, state, current_user):
    await state.clear()
    if not _is_staff(current_user) or not (_is_admin(current_user) or current_user.role == 'branch_manager'):
        await message.answer('دسترسی پنل مدیر شعبه برای شما ثبت نشده است.')
        return
    if not current_user.managed_branch_id:
        if _is_admin(current_user):
            async with AsyncSessionLocal() as session:
                branches = list((await session.scalars(select(Branch).where(Branch.is_active.is_(True)))).all())
            await message.answer('برای ورود اولیه به پنل مدیر شعبه، شعبهٔ موردنظر را تعیین کنید؛ این انتخاب برای دفعات بعد ذخیره می‌شود.', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=b.name, callback_data=f'panel:bindbranch:{b.id}')] for b in branches]))
        else:
            await message.answer('شعبهٔ شما تعیین نشده است؛ ادمین باید پیش از ورود، شعبه را ثبت کند.')
        return
    await set_view_panel(current_user, 'branch_manager')
    await message.answer('پنل مدیر شعبه راینوتک', reply_markup=_manager_menu(current_user))


@router.callback_query(F.data.startswith('panel:bindbranch:'))
async def bind_admin_branch(callback, current_user):
    if not _is_admin(current_user):
        await callback.answer('دسترسی مجاز نیست.', show_alert=True)
        return
    try:
        branch_id = int(callback.data.rsplit(':', 1)[1])
        async with AsyncSessionLocal() as session:
            branch = await session.get(Branch, branch_id)
            user = await session.get(User, current_user.id)
            if not branch or not branch.is_active or not user:
                raise ValueError
            user.managed_branch_id = branch_id
            user.view_panel = 'branch_manager'
            await session.commit()
        current_user.managed_branch_id = branch_id
        current_user.view_panel = 'branch_manager'
    except ValueError:
        await callback.answer('شعبه معتبر نیست.', show_alert=True)
        return
    await callback.answer()
    await callback.message.answer('پنل مدیر شعبه · ' + escape(branch.name), reply_markup=_manager_menu(current_user))


@router.message(F.text == "👥 ورودی‌های لینک من")
async def staff_referral_count(message: Message, current_user: User) -> None:
    if not _is_staff(current_user):
        await message.answer("دسترسی مجاز نیست.")
        return
    async with AsyncSessionLocal() as session:
        count = await session.scalar(select(func.count(User.id)).where(
            User.role == 'customer', User.referrer_telegram_id == current_user.telegram_id,
        )) or 0
    await message.answer(f"👥 ورودی‌های لینک راینو شما: {count} مشتری یکتا", reply_markup=_staff_menu(current_user))


@router.message(F.text == "🔗 لینک معرفی من")
async def staff_referral_link(message: Message, current_user: User) -> None:
    if not _is_staff(current_user) or current_user.role != 'seller':
        await message.answer("دسترسی مجاز نیست.")
        return
    bot_info = await message.bot.get_me()
    try:
        code = await get_referral_code(current_user)
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await message.answer(
        f"🔗 لینک راینو:\nhttps://t.me/{bot_info.username}?start={code}",
        disable_web_page_preview=True,
    )


@router.message(F.text.in_({ADMIN, 'ورود ادمین', '🔙 بازگشت به پنل مدیریت'}))
async def admin_entry(message: Message, state: FSMContext, current_user: User) -> None:
    await state.clear()
    if not _is_admin(current_user):
        await message.answer("دسترسی ادمین برای این حساب ثبت نشده است.", reply_markup=main_menu_kb("customer"))
        return
    await set_view_panel(current_user, 'admin')
    await message.answer("پنل ادمین راینوتک", reply_markup=_admin_menu())


@router.message(F.text == "بازگشت به منوی اصلی")
async def panel_exit(message: Message, state: FSMContext, current_user: User) -> None:
    await state.clear()
    await customer_entry(message, state, current_user)


@router.message(F.text.in_({"🔙 انتخاب پنل", "🔙 بازگشت به انتخاب نقش"}))
async def admin_role_selection(message: Message, state: FSMContext, current_user: User) -> None:
    await state.clear()
    if not _is_staff(current_user):
        await message.answer("دسترسی مجاز نیست.")
        return
    menu = entry_menu_kb(current_user if getattr(current_user, 'view_panel', None) == 'admin' else None)
    if _is_admin(current_user) and getattr(current_user, 'view_panel', None) != 'admin':
        menu.keyboard.append([KeyboardButton(text='🔙 بازگشت به پنل مدیریت')])
    await message.answer("بخش موردنظر را انتخاب کنید:", reply_markup=menu)


@router.message(F.text == "📊 داشبورد CRM")
async def crm_dashboard(message: Message, current_user: User, state: FSMContext | None = None) -> None:
    if not _is_admin(current_user):
        await message.answer("دسترسی مجاز نیست.")
        return
    from bot.handlers.dashboard_cards import send_crm_dashboard
    if state:
        await state.clear()
    await send_crm_dashboard(message, current_user)


@router.message(F.text.in_({'👥 دسترسی پرسنل', '👥 مدیران و کارشناسان'}))
async def staff_access_start(message: Message, state: FSMContext, current_user: User) -> None:
    if not _is_admin(current_user):
        await message.answer("دسترسی مجاز نیست.")
        return
    await state.set_state(PanelState.staff_lookup)
    from bot.handlers.management_dashboard import _send_staff
    await _send_staff(message, 0)
    await message.answer("👥 دسترسی پرسنل\n\nبرای تعیین نقش، شمارهٔ موبایل، Chat ID یا @نام‌کاربری شخص را بفرستید.\nبرای فعال یا غیرفعال‌کردن مجوز ویرایش محصول و عکس، دکمهٔ زیر را بزنید.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🔐 مجوز ویرایش پرسنل', callback_data='panel:permissions')],
        [InlineKeyboardButton(text='🏠 پنل مدیریت', callback_data='panel:home')],
    ]))


@router.callback_query(F.data == 'panel:addmember')
async def add_staff_member(callback, state, current_user):
    if not _is_admin(current_user):
        await callback.answer('دسترسی مجاز نیست.', show_alert=True)
        return
    await callback.answer()
    await state.set_state(PanelState.staff_lookup)
    await callback.message.answer('شماره، شناسهٔ تلگرام یا @نام‌کاربری عضو جدید را بفرستید؛ شخص باید قبلاً بات را شروع کرده باشد.')


@router.callback_query(F.data == 'panel:permissions')
async def staff_permissions_submenu(callback, state, current_user):
    if not _is_admin(current_user):
        await callback.answer('دسترسی مجاز نیست.', show_alert=True)
        return
    await callback.answer()
    await edit_permissions_menu(callback.message, state, current_user)


@router.message(PanelState.staff_lookup, F.text != '💼 امور مالی')
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
        [InlineKeyboardButton(text="مدیر شعبه", callback_data=f"panel:role:{user.id}:branch_manager"), InlineKeyboardButton(text="کارشناس فروش", callback_data=f"panel:role:{user.id}:seller")],
        [InlineKeyboardButton(text="عزل از پرسنل", callback_data=f"panel:role:{user.id}:customer")],
        [InlineKeyboardButton(text='لغو مجوز حسابداری' if user.accounting_access else 'اعطای مجوز حسابداری', callback_data=f'finaccess:{user.id}:{0 if user.accounting_access else 1}')],
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
        if role in {"branch_manager", "seller"}:
            branches = list((await session.scalars(select(Branch).where(Branch.is_active.is_(True)).order_by(Branch.id))).all())
            if not branches:
                await callback.answer("شعبهٔ فعالی برای انتساب پیدا نشد.", show_alert=True)
                return
            await callback.answer()
            await callback.message.answer("شعبهٔ این پرسنل را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=branch.name, callback_data=f"panel:staffbranch:{user_id}:{branch.id}:{role}")] for branch in branches
            ]))
            return
        user.role = role
        if role == "customer":
            user.managed_branch_id = None
        session.add(StaffActivity(actor_telegram_id=current_user.telegram_id, action="role_change", target_id=user.id, detail=role))
        await session.commit()
    await callback.answer("دسترسی به‌روز شد.")
    await callback.message.answer("نقش کاربر با موفقیت به‌روز شد.")


@router.callback_query(F.data.startswith("panel:branch:") | F.data.startswith('panel:staffbranch:'))
async def assign_manager_branch(callback, current_user: User) -> None:
    if not _is_admin(current_user):
        await callback.answer("دسترسی مجاز نیست.", show_alert=True)
        return
    try:
        parts = callback.data.split(':')
        if len(parts) == 4 and parts[1] == 'branch':
            _, _, user_id, branch_id = parts
            role = 'branch_manager'
        elif len(parts) == 5 and parts[1] == 'staffbranch' and parts[4] in {'seller', 'branch_manager'}:
            _, _, user_id, branch_id, role = parts
        else:
            raise ValueError
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
        user.role = role
        user.view_panel = role
        user.managed_branch_id = branch.id
        session.add(StaffActivity(actor_telegram_id=current_user.telegram_id, action="role_change", target_id=user.id, detail=f"{role}:{branch.code}"))
        await session.commit()
    await callback.answer("نقش و شعبه ثبت شد.")
    await callback.message.answer(f"نقش {'مدیر شعبه' if role == 'branch_manager' else 'کارشناس فروش'} برای {escape(branch.name)} ثبت شد.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='👤 تنظیم دسترسی این شخص', callback_data=f'mgmt:person:{user.id}')]]))


@router.message(F.text == '🔐 دسترسی ویرایش پرسنل')
async def edit_permissions_menu(message, state, current_user):
    if not _is_staff(current_user) or current_user.role not in {'admin', 'branch_manager'}:
        await message.answer('دسترسی مجاز نیست.')
        return
    await state.clear()
    async with AsyncSessionLocal() as session:
        query = select(User).where(User.role.in_(['seller', 'branch_manager'])).order_by(User.id)
        if not _is_admin(current_user) or getattr(current_user, 'view_panel', None) == 'branch_manager':
            if not current_user.managed_branch_id:
                await message.answer('ابتدا ادمین باید شعبهٔ شما و پرسنل را تعیین کند.')
                return
            query = query.where(User.managed_branch_id == current_user.managed_branch_id, User.role == 'seller')
        users = list((await session.scalars(query)).all())
    rows = [[InlineKeyboardButton(text=f"{'✅' if u.product_edit_allowed else '⛔'} {u.full_name or u.username or u.telegram_id}"[:64], callback_data=f'panel:editaccess:{u.id}:{0 if u.product_edit_allowed else 1}')] for u in users]
    rows.append([InlineKeyboardButton(text='🏠 پنل من', callback_data='panel:home')])
    await message.answer('دسترسی ویرایش محصول و عکس\nپیش‌فرض همهٔ پرسنل فعال مجازند. هر دکمه وضعیت نمایش‌داده‌شده را تغییر می‌دهد؛ سایر امکانات حساب تغییر نمی‌کند.', reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith('panel:editaccess:'))
async def change_edit_access(callback, current_user):
    from bot.services.edit_permissions import set_edit_permission
    from bot.services.stock_lock import stock_lock
    try:
        parts = callback.data.split(':')
        if len(parts) != 4 or parts[3] not in {'0', '1'}:
            raise ValueError
        async with stock_lock, AsyncSessionLocal() as session:
            actor = await session.get(User, current_user.id)
            target = await session.get(User, int(parts[2]))
            allowed = parts[3] == '1'
            if not actor or not target:
                raise PermissionError
            await set_edit_permission(session, actor, target, allowed)
    except (ValueError, PermissionError):
        await callback.answer('کاربر یا دسترسی معتبر نیست.', show_alert=True)
        return
    await callback.answer('دسترسی ویرایش فعال شد.' if allowed else 'دسترسی ویرایش لغو شد.')
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer('دسترسی ذخیره شد؛ برای مشاهدهٔ وضعیت جدید «🔐 دسترسی ویرایش پرسنل» را بزنید.')


@router.message(F.text == "✏️ ویرایش محصول")
async def product_edit_start(message: Message, state: FSMContext, current_user: User) -> None:
    if not _can_edit(current_user):
        await message.answer("دسترسی مجاز نیست.")
        return
    await state.clear()
    await show_edit_brands(message)


async def show_edit_brands(message: Message, *, edit: bool = False) -> None:
    async with AsyncSessionLocal() as session:
        brands = (await session.execute(
            select(LaptopBrand.id, LaptopBrand.name, func.count(Laptop.id))
            .join(Laptop, Laptop.brand_id == LaptopBrand.id)
            .where(Laptop.status == "active")
            .group_by(LaptopBrand.id, LaptopBrand.name)
            .order_by(LaptopBrand.name)
        )).all()
    rows = [[InlineKeyboardButton(text=f"{name} · {count} مدل", callback_data=f"panel:editbrand:{brand_id}:0")]
            for brand_id, name, count in brands]
    rows.append([InlineKeyboardButton(text="🔎 جستجو با نام یا شناسه", callback_data="panel:editsearch")])
    rows.append([InlineKeyboardButton(text="🏠 پنل من", callback_data="panel:home")])
    text = "✏️ برای ویرایش مشخصات، ابتدا برند محصول را انتخاب کنید:"
    if edit:
        await message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    else:
        await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data == "panel:editbrands")
async def edit_brands_back(callback, state: FSMContext, current_user: User) -> None:
    if not _can_edit(current_user):
        await callback.answer("دسترسی مجاز نیست.", show_alert=True)
        return
    await state.clear()
    await callback.answer()
    await show_edit_brands(callback.message, edit=True)


@router.callback_query(F.data == "panel:editsearch")
async def edit_search_start(callback, state: FSMContext, current_user: User) -> None:
    if not _can_edit(current_user):
        await callback.answer("دسترسی مجاز نیست.", show_alert=True)
        return
    await state.set_state(PanelState.product_lookup)
    await callback.answer()
    await callback.message.answer("نام مدل همان‌طور که در کاتالوگ دیده می‌شود یا شناسهٔ محصول را بفرستید.")


@router.callback_query(F.data.startswith("panel:editbrand:"))
async def edit_brand_products(callback, current_user: User) -> None:
    if not _can_edit(current_user):
        await callback.answer("دسترسی مجاز نیست.", show_alert=True)
        return
    try:
        _, _, brand_id, page = callback.data.split(":")
        brand_id, page = int(brand_id), int(page)
        if page < 0:
            raise ValueError
    except (ValueError, TypeError):
        await callback.answer("درخواست نامعتبر است.", show_alert=True)
        return
    page_size = 8
    async with AsyncSessionLocal() as session:
        brand = await session.get(LaptopBrand, brand_id)
        if brand is None:
            await callback.answer("برند پیدا نشد.", show_alert=True)
            return
        total = await session.scalar(select(func.count(func.distinct(Laptop.model))).where(Laptop.brand_id == brand_id, Laptop.status == "active")) or 0
        rows = (await session.execute(
            select(func.min(Laptop.id), Laptop.model, func.min(Laptop.ram), func.min(Laptop.price),
                   func.coalesce(func.sum(BranchInventory.quantity - BranchInventory.reserved_count), 0))
            .outerjoin(BranchInventory, BranchInventory.laptop_id == Laptop.id)
            .where(Laptop.brand_id == brand_id, Laptop.status == "active")
            .group_by(Laptop.model)
            .order_by(Laptop.model)
            .offset(page * page_size).limit(page_size)
        )).all()
    if not rows:
        await callback.answer("مدلی در این صفحه پیدا نشد.", show_alert=True)
        return
    buttons = [[InlineKeyboardButton(
        text=f"{model} | {max(0, stock)} موجود"[:60],
        callback_data=f"panel:editmodel:{laptop_id}:0",
    )] for laptop_id, model, ram, _price, stock in rows]
    nav = []
    if page:
        nav.append(InlineKeyboardButton(text="⬅️ قبلی", callback_data=f"panel:editbrand:{brand_id}:{page - 1}"))
    if (page + 1) * page_size < total:
        nav.append(InlineKeyboardButton(text="بعدی ➡️", callback_data=f"panel:editbrand:{brand_id}:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="🔙 برندها", callback_data="panel:editbrands")])
    await callback.answer()
    await callback.message.edit_text(
        f"✏️ {escape(brand.name)} — مدل موردنظر برای ویرایش را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.message(PanelState.product_lookup, ~F.text.in_({'👤 کاربران جدید', '📈 گزارش ورودی‌ها', '👤 مشتریان جدید من', '📈 گزارش ورودی‌های من', '👥 مدیران و کارشناسان', '🏢 عملکرد شعب', '💻 کاتالوگ و مشخصات', '📊 داشبورد CRM', '👥 دسترسی پرسنل', '🔐 دسترسی ویرایش پرسنل', '🔙 انتخاب پنل', 'بازگشت به منوی اصلی', '📊 عملکرد کارشناسان شعبه', '📦 موجودی شعبه', '💻 مدل‌های شعبه', '📋 رزروها و سفارش‌های شعبه', '📥 گزارش عملیات شعبه', '📦 مدل‌ها و موجودی شعبه', '🔙 بازگشت به پنل مدیریت', '💼 امور مالی'}))
async def product_lookup(message: Message, state: FSMContext, current_user: User) -> None:
    if not _can_edit(current_user):
        await state.clear()
        return
    query = (message.text or "").strip()
    if not query:
        await message.answer("نام یا شناسهٔ مدل را بفرستید.")
        return
    async with AsyncSessionLocal() as session:
        stmt = select(Laptop).join(LaptopBrand).where(Laptop.status == "active")
        if query.isdigit():
            stmt = stmt.where(Laptop.id == int(query))
        else:
            # The catalog shows "brand + model"; the stored model often omits the brand.
            # Match every distinct word against either field so copied catalog titles work.
            words = list(dict.fromkeys(word.casefold() for word in re.findall(r"[\w-]+", query[:120]) if len(word) > 1))[:8]
            if not words:
                await message.answer("نام یا شناسهٔ معتبر مدل را بفرستید.")
                return
            for word in words:
                stmt = stmt.where(or_(Laptop.model.ilike(f"%{word}%"), LaptopBrand.name.ilike(f"%{word}%")))
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
    if not _can_edit(current_user):
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
    ] + [[InlineKeyboardButton(text="دیدن در کاتالوگ", callback_data=f"tree:laptop:{laptop_id}")],
         [InlineKeyboardButton(text="🔙 کانفیگ‌ها", callback_data=f"panel:editmodel:{laptop_id}:0")],
         [InlineKeyboardButton(text="🏠 پنل من", callback_data="panel:home")]])
    await callback.answer()
    await callback.message.answer(f"✏️ {escape(laptop.model)}\nفیلد مورد نظر را انتخاب کنید:", reply_markup=keyboard)


@router.callback_query(F.data.startswith("panel:field:"))
async def choose_edit_field(callback, state: FSMContext, current_user: User) -> None:
    if not _can_edit(current_user):
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
    await state.clear()
    await state.set_state(PanelState.product_value)
    await state.update_data(laptop_id=laptop_id, edit_field=field)
    await callback.answer()
    await callback.message.answer("عکس واقعی لپ‌تاپ را ارسال کنید. برای افزودن عکس‌های بعدی، همین مسیر را دوباره انتخاب کنید." if field == "photo" else f"مقدار جدید {field} را بفرستید:")


@router.message(PanelState.product_value, ~F.text.in_({'👤 کاربران جدید', '📈 گزارش ورودی‌ها', '👤 مشتریان جدید من', '📈 گزارش ورودی‌های من', '👥 مدیران و کارشناسان', '🏢 عملکرد شعب', '💻 کاتالوگ و مشخصات', '📊 داشبورد CRM', '👥 دسترسی پرسنل', '🔐 دسترسی ویرایش پرسنل', '🔙 انتخاب پنل', 'بازگشت به منوی اصلی', '📊 عملکرد کارشناسان شعبه', '📦 موجودی شعبه', '💻 مدل‌های شعبه', '📋 رزروها و سفارش‌های شعبه', '📥 گزارش عملیات شعبه', '📦 مدل‌ها و موجودی شعبه', '🔙 بازگشت به پنل مدیریت', '💼 امور مالی'}))
async def save_product_edit(message: Message, state: FSMContext, current_user: User) -> None:
    if not _can_edit(current_user):
        await state.clear()
        return
    data = await state.get_data()
    field = data.get("edit_field")
    laptop_id = data.get("laptop_id")
    if field not in {"cpu", "ram", "storage", "gpu", "screen_size", "color", "condition", "price", "photo"}:
        await state.clear()
        await message.answer("درخواست ویرایش منقضی شده است؛ محصول را دوباره انتخاب کنید.")
        return
    if field == "photo":
        if not message.photo:
            await message.answer("عکس را با گزینهٔ عکس تلگرام بفرستید، نه به صورت فایل؛ تا در کاتالوگ قابل نمایش باشد.")
            return
        value = message.photo[-1].file_id
    else:
        value = (message.text or "").strip()
        if not value or len(value) > 120:
            await message.answer("مقدار معتبر و کوتاه‌تر از ۱۲۰ نویسه بفرستید.")
            return
        if field == "price":
            from bot.services.edit_safety import parse_price
            try:
                value = parse_price(value)
            except ValueError as exc:
                await message.answer(str(exc))
                return
    from bot.services.edit_safety import new_confirmation
    confirmation = new_confirmation()
    token = confirmation['edit_token']
    from bot.services.sheet_product_editor import prepare_product_edit
    from bot.services.apps_script_bridge import SheetBridgeError
    if field == 'photo':
        await state.update_data(local_photo=value, sheet_edits=None, **confirmation)
        await state.set_state(PanelState.product_confirm)
        await message.answer_photo(photo=value, caption='پیش‌نمایش عکس اصلی جدید؛ عکس‌های قبلی در گالری می‌مانند. این عکس فعلاً فقط در بات ذخیره می‌شود.', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text='✅ ذخیره عکس', callback_data=f'panel:sheet:confirm:{token}'),
            InlineKeyboardButton(text='❌ لغو', callback_data=f'panel:sheet:cancel:{token}'),
        ]]))
        return
    try:
        async with AsyncSessionLocal() as session:
            edits = await prepare_product_edit(session, laptop_id, field, value)
    except Exception as exc:
        await message.answer(str(exc) if isinstance(exc, SheetBridgeError) else "دریافت شیت ناموفق بود؛ هیچ تغییری ثبت نشد. دوباره تلاش کنید.")
        return
    await state.update_data(sheet_edits=edits, local_photo=None, **confirmation)
    await state.set_state(PanelState.product_confirm)
    await message.answer(
        f"تغییر {escape(field)} در {len(edits)} ردیف شیت تأیید می‌شود؟\n" + ("عکس ارسالی" if field == 'photo' else escape(str(value))),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ تأیید و ثبت در شیت", callback_data=f"panel:sheet:confirm:{token}"),
            InlineKeyboardButton(text="❌ لغو", callback_data=f"panel:sheet:cancel:{token}"),
        ]]),
    )


@router.callback_query(F.data.startswith('panel:sheet:'))
@serialized_sheet
async def confirm_sheet_edit(callback, state: FSMContext, current_user: User):
    if not _can_edit(current_user):
        await state.clear()
        await callback.answer("دسترسی مجاز نیست.", show_alert=True)
        return
    data = await state.get_data()
    from bot.services.edit_safety import valid_confirmation
    parts = (callback.data or '').split(':')
    if len(parts) != 4 or parts[2] not in {'confirm', 'cancel'} or not valid_confirmation(data, parts[3]):
        await callback.answer('این دکمه قدیمی یا منقضی شده است؛ از آخرین پیش‌نمایش استفاده کنید.', show_alert=True)
        return
    await callback.answer()
    # Consume confirmation before I/O: duplicate clicks cannot blindly resend a write.
    await state.clear()
    if parts[2] == 'cancel':
        await callback.message.answer("ویرایش لغو شد؛ تغییری ثبت نشد.")
        return
    if data.get('local_photo'):
        from bot.services.product_media import save_product_photo
        try:
            async with AsyncSessionLocal() as session:
                await save_product_photo(session, data.get('laptop_id'), data['local_photo'], current_user.telegram_id)
        except Exception:
            await callback.message.answer('ذخیرهٔ عکس ناموفق بود؛ محصول را دوباره انتخاب و عکس را ارسال کنید.')
            return
        await callback.message.answer('✅ عکس اصلی محصول در بات ذخیره شد. به گوگل‌شیت ارسال نشده است.', reply_markup=panel_menu(current_user))
        return
    edits = data.get('sheet_edits')
    if not edits:
        await callback.message.answer("تأیید منقضی شده است؛ محصول را دوباره انتخاب کنید.")
        return
    from bot.services.sheet_product_editor import apply_product_edit
    from bot.services.apps_script_bridge import SheetBridgeError
    try:
        async with AsyncSessionLocal() as session:
            await apply_product_edit(session, edits)
            session.add(StaffActivity(actor_telegram_id=current_user.telegram_id, action='product_edit', target_id=data.get('laptop_id'), detail=str(data.get('edit_field', 'sheet'))))
            await session.commit()
    except Exception as exc:
        await callback.message.answer((str(exc) if isinstance(exc, SheetBridgeError) else "همگام‌سازی کامل نشد.") + "\nقبل از تلاش مجدد شیت را بررسی و محصول را دوباره انتخاب کنید؛ نتیجهٔ نوشتن ممکن است نامشخص باشد.")
        return
    await callback.message.answer("✅ تغییر در گوگل‌شیت ثبت و کاتالوگ تازه‌سازی شد.", reply_markup=panel_menu(current_user))

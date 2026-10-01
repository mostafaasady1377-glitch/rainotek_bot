from __future__ import annotations

from html import escape
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import or_, select
from sqlalchemy.orm import joinedload

from bot.services.inventory_service import InventoryService
from database.models import Branch, Laptop, LaptopBrand, User
from database.session import AsyncSessionLocal

router = Router()
ALLOWED_ROLES = {"admin", "warehouse", "branch_manager"}


class StockEntryStates(StatesGroup):
    choosing_branch = State()
    choosing_laptop = State()
    entering_new_model = State()
    choosing_variant = State()
    entering_configuration = State()
    entering_quantity = State()


def _keyboard(buttons: list[tuple[str, str]], columns: int = 1) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=callback_data)
         for label, callback_data in buttons[index:index + columns]]
        for index in range(0, len(buttons), columns)
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _is_stock_manager(user: User) -> bool:
    return user.is_active and user.role in ALLOWED_ROLES


def _can_manage_branch(user: User, branch_id: int) -> bool:
    return user.role != "branch_manager" or user.managed_branch_id == branch_id


@router.message(F.text == "➕ ورود کالا")
@router.message(F.text == "/stock_wizard")
async def start_stock_wizard(message: Message, state: FSMContext, current_user: User) -> None:
    if not _is_stock_manager(current_user):
        await message.answer("ورود موجودی فقط برای مدیر، انباردار یا مدیر شعبه مجاز است.")
        return

    async with AsyncSessionLocal() as session:
        statement = select(Branch).where(Branch.is_active.is_(True)).order_by(Branch.name)
        if current_user.role == "branch_manager":
            statement = statement.where(Branch.id == current_user.managed_branch_id)
        branches = list((await session.scalars(statement)).all())

    if not branches:
        await message.answer("برای حساب شما شعبه‌ی فعالی تخصیص داده نشده است.")
        return
    await state.clear()
    await state.update_data(branch_ids=[branch.id for branch in branches])
    await state.set_state(StockEntryStates.choosing_branch)
    await message.answer(
        "گام ۱ از ۴: شعبه‌ی مقصد را انتخاب کنید.",
        reply_markup=_keyboard([(branch.name, f"se:branch:{index}") for index, branch in enumerate(branches)]),
    )


@router.callback_query(F.data.startswith("se:branch:"))
async def choose_entry_branch(callback: CallbackQuery, state: FSMContext, current_user: User) -> None:
    data = await state.get_data()
    try:
        branch_id = data["branch_ids"][int(callback.data.rsplit(":", 1)[1])]
    except (KeyError, IndexError, ValueError, TypeError):
        await callback.answer("این مرحله منقضی شده است؛ ویزارد را دوباره شروع کنید.", show_alert=True)
        return
    if not _is_stock_manager(current_user) or not _can_manage_branch(current_user, branch_id):
        await callback.answer("اجازه‌ی ثبت موجودی در این شعبه را ندارید.", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        branch = await session.get(Branch, branch_id)
        laptops = await InventoryService(session).list_active_laptops()
    if branch is None:
        await callback.answer("شعبه پیدا نشد.", show_alert=True)
        return

    await state.update_data(branch_id=branch_id, laptop_ids=[laptop.id for laptop in laptops])
    await state.set_state(StockEntryStates.choosing_laptop)
    buttons = [
        (f"{laptop.brand.name} {laptop.model} ({laptop.ram or '-'} / {laptop.gpu or '-'})", f"se:laptop:{index}")
        for index, laptop in enumerate(laptops)
    ]
    buttons.append(("➕ تعریف مدل جدید", "se:new_model"))
    await callback.message.edit_text(f"گام ۲ از ۴: کالا برای شعبه {branch.name} را انتخاب کنید:", reply_markup=_keyboard(buttons))
    await callback.answer()


@router.callback_query(F.data.startswith("se:laptop:"))
async def choose_entry_laptop(callback: CallbackQuery, state: FSMContext, current_user: User) -> None:
    data = await state.get_data()
    branch_id = data.get("branch_id")
    try:
        laptop_id = data["laptop_ids"][int(callback.data.rsplit(":", 1)[1])]
    except (KeyError, IndexError, ValueError, TypeError):
        await callback.answer("این مرحله منقضی شده است؛ ویزارد را دوباره شروع کنید.", show_alert=True)
        return
    if not _is_stock_manager(current_user) or not _can_manage_branch(current_user, branch_id):
        await callback.answer("اجازه‌ی ثبت موجودی در این شعبه را ندارید.", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        laptop = await session.get(Laptop, laptop_id)
        if laptop is None:
            await callback.answer("مدل پیدا نشد.", show_alert=True)
            return
        variants = await InventoryService(session).get_laptop_variants(laptop.brand.name, laptop.model)
        variant_buttons = [
            (f"{item.cpu or '-'} | {item.ram or '-'} | {item.gpu or '-'} | {item.storage or '-'}", f"se:variant:{index}")
            for index, item in enumerate(variants)
        ]

    await state.update_data(
        brand_name=laptop.brand.name,
        model=laptop.model,
        variant_ids=[item.id for item in variants],
    )
    await state.set_state(StockEntryStates.choosing_variant)
    variant_buttons.append(("➕ واردکردن کانفیگ جدید", "se:new_config"))
    await callback.message.edit_text("گام ۳ از ۴: کانفیگ را انتخاب کنید:", reply_markup=_keyboard(variant_buttons))
    await callback.answer()


@router.callback_query(F.data == "se:new_model")
async def request_new_model(callback: CallbackQuery, state: FSMContext, current_user: User) -> None:
    if not _is_stock_manager(current_user):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    await state.set_state(StockEntryStates.entering_new_model)
    await callback.message.edit_text("برند و نام مدل را به شکل «برند | مدل» بفرستید.")
    await callback.answer()


@router.message(StockEntryStates.entering_new_model)
async def receive_new_model(message: Message, state: FSMContext, current_user: User) -> None:
    if not _is_stock_manager(current_user):
        await state.clear()
        await message.answer("دسترسی ندارید.")
        return
    parts = [part.strip() for part in (message.text or "").split("|")]
    if len(parts) != 2 or not all(parts):
        await message.answer("ورودی نامعتبر است. قالب: برند | مدل")
        return
    await state.update_data(brand_name=parts[0], model=parts[1])
    await state.set_state(StockEntryStates.entering_configuration)
    await message.answer("گام ۳ از ۴: کانفیگ را به شکل CPU | RAM | GPU | Storage بفرستید.")


@router.callback_query(F.data.startswith("se:variant:"))
async def choose_entry_variant(callback: CallbackQuery, state: FSMContext, current_user: User) -> None:
    data = await state.get_data()
    try:
        branch_id = int(data["branch_id"])
        laptop_id = data["variant_ids"][int(callback.data.rsplit(":", 1)[1])]
    except (KeyError, IndexError, ValueError, TypeError):
        await callback.answer("این مرحله منقضی شده است؛ ویزارد را دوباره شروع کنید.", show_alert=True)
        return
    if not _is_stock_manager(current_user) or not _can_manage_branch(current_user, branch_id):
        await callback.answer("اجازه‌ی ثبت موجودی در این شعبه را ندارید.", show_alert=True)
        return
    await state.update_data(laptop_id=laptop_id)
    await state.set_state(StockEntryStates.entering_quantity)
    await callback.message.edit_text("گام ۴ از ۴: تعداد را بفرستید. «+5» یعنی افزایش ۵ عدد؛ عدد ساده یعنی موجودی نهایی.")
    await callback.answer()


@router.callback_query(F.data == "se:new_config")
async def request_new_configuration(callback: CallbackQuery, state: FSMContext, current_user: User) -> None:
    if not _is_stock_manager(current_user):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    await state.set_state(StockEntryStates.entering_configuration)
    await callback.message.edit_text("گام ۳ از ۴: کانفیگ جدید را به شکل CPU | RAM | GPU | Storage بفرستید.")
    await callback.answer()


@router.message(StockEntryStates.entering_configuration)
async def receive_configuration(message: Message, state: FSMContext, current_user: User) -> None:
    if not _is_stock_manager(current_user):
        await state.clear()
        await message.answer("دسترسی ندارید.")
        return
    parts = [part.strip() for part in (message.text or "").split("|")]
    if len(parts) != 4 or not all(parts):
        await message.answer("چهار مقدار لازم است: CPU | RAM | GPU | Storage")
        return

    data = await state.get_data()
    if not _can_manage_branch(current_user, int(data["branch_id"])):
        await state.clear()
        await message.answer("اجازه‌ی ثبت در این شعبه را ندارید.")
        return
    async with AsyncSessionLocal() as session:
        laptop = await InventoryService(session).get_or_create_laptop_variant(
            data["brand_name"], data["model"], *parts
        )
    await state.update_data(laptop_id=laptop.id)
    await state.set_state(StockEntryStates.entering_quantity)
    await message.answer("گام ۴ از ۴: تعداد را بفرستید. «+5» یعنی افزایش ۵ عدد؛ عدد ساده یعنی موجودی نهایی.")


@router.message(StockEntryStates.entering_quantity)
async def receive_quantity(message: Message, state: FSMContext, current_user: User) -> None:
    data = await state.get_data()
    branch_id = int(data["branch_id"])
    if not _is_stock_manager(current_user) or not _can_manage_branch(current_user, branch_id):
        await state.clear()
        await message.answer("اجازه‌ی ثبت موجودی در این شعبه را ندارید.")
        return

    raw = (message.text or "").strip()
    increment = raw.startswith("+")
    numeric = raw[1:] if increment else raw
    if not numeric.isdigit():
        await message.answer("تعداد باید عدد صحیح باشد؛ نمونه افزایش: +5، موجودی نهایی: 12")
        return
    quantity = int(numeric)
    if quantity <= 0 and increment:
        await message.answer("مقدار افزایش باید بزرگ‌تر از صفر باشد.")
        return

    async with AsyncSessionLocal() as session:
        service = InventoryService(session)
        laptop = await session.scalar(
            select(Laptop)
            .options(joinedload(Laptop.brand))
            .where(Laptop.id == int(data["laptop_id"]))
        )
        branch = await session.get(Branch, branch_id)
        if laptop is None or branch is None:
            await state.clear()
            await message.answer("کالا یا شعبه پیدا نشد؛ ویزارد را دوباره آغاز کنید.")
            return
        try:
            if increment:
                stock = await service.add_stock(
                    laptop.id, branch.id, quantity, current_user.telegram_id, "ثبت افزایش از ویزارد"
                )
            else:
                stock = await service.set_stock_quantity(
                    laptop.id, branch.id, quantity, current_user.telegram_id
                )
        except ValueError as exc:
            await message.answer(str(exc))
            return
    await state.clear()
    await message.answer(
        f"ثبت شد: {escape(laptop.brand.name)} {escape(laptop.model)} در "
        f"{escape(branch.name)} اکنون {stock.quantity} عدد است."
    )


def parse_quick_add(text: str) -> tuple[str, str, str, str, str, str, int] | None:
    normalized = text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    parts = [part.strip() for part in normalized.split("/")]
    if len(parts) != 8 or not parts[0].startswith("افزودن"):
        return None
    branch, model, cpu, ram, gpu, storage = parts[1:7]
    match = re.search(r"(?:تعداد\s*:\s*)?(\+?\d+)\s*$", parts[7])
    if not all((branch, model, cpu, ram, gpu, storage)) or not match:
        return None
    count = int(match.group(1).lstrip("+"))
    if count <= 0:
        return None
    return branch, model, cpu, ram, gpu, storage, count


@router.message(F.text.startswith("افزودن /"))
async def quick_add_stock(message: Message, current_user: User) -> None:
    if not _is_stock_manager(current_user):
        await message.answer("ورود موجودی فقط برای مدیر، انباردار یا مدیر شعبه مجاز است.")
        return
    parsed = parse_quick_add(message.text or "")
    if parsed is None:
        await message.answer("قالب سریع نامعتبر است؛ نمونه: افزودن / شعبه مرکزی / G16 / i7 / 16GB / 4060 / 512GB / تعداد: 4")
        return
    branch_name, model, cpu, ram, gpu, storage, quantity = parsed

    async with AsyncSessionLocal() as session:
        branches = list((await session.scalars(
            select(Branch).where(
                Branch.is_active.is_(True),
                or_(Branch.code.ilike(branch_name), Branch.name.ilike(f"%{branch_name}%")),
            )
        )).all())
        if len(branches) != 1:
            await message.answer("شعبه پیدا نشد یا نام آن یکتا نیست؛ از /stock_wizard استفاده کنید.")
            return
        branch = branches[0]
        if not _can_manage_branch(current_user, branch.id):
            await message.answer("شما فقط اجازه‌ی ثبت در شعبه‌ی تخصیص‌یافته را دارید.")
            return
        laptops = await InventoryService(session).search_laptops_by_specs({
            "model": model, "cpu": cpu, "ram": ram, "gpu": gpu, "storage": storage,
        })
        if len(laptops) != 1:
            await message.answer("کانفیگ موجود به‌صورت یکتا پیدا نشد؛ برای مدل جدید یا انتخاب دستی از /stock_wizard استفاده کنید.")
            return
        laptop = laptops[0]
        stock = await InventoryService(session).add_stock(
            laptop.id,
            branch.id,
            quantity,
            current_user.telegram_id,
            "ورود سریع موجودی",
        )
    await message.answer(
        f"ثبت سریع انجام شد: {escape(laptop.brand.name)} {escape(laptop.model)} در "
        f"{escape(branch.name)} اکنون {stock.quantity} عدد است."
    )

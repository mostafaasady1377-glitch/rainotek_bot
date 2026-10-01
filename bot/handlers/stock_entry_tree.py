from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from bot.keyboards.catalog_builder import PAGE_SIZE
from bot.services.inventory_service import InventoryService
from database.models import Branch, LaptopBrand, LaptopModel, LaptopSeries, LaptopVariant, User
from database.session import AsyncSessionLocal

router = Router()
ALLOWED_ROLES = {"admin", "warehouse", "branch_manager"}


class TreeStockStates(StatesGroup):
    navigating = State()
    entering_new_model = State()
    entering_configuration = State()
    entering_custom_quantity = State()
    confirming = State()


def _buttons(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _role_allowed(user: User) -> bool:
    return user.is_active and user.role in ALLOWED_ROLES


def _branch_allowed(user: User, branch_id: int) -> bool:
    return user.role != "branch_manager" or user.managed_branch_id == branch_id


async def _render_options(
    message: Message,
    state: FSMContext,
    title: str,
    options: list[tuple[str, str]],
    *,
    edit: bool,
    add_new: tuple[str, str] | None = None,
) -> None:
    data = await state.get_data()
    page = int(data.get("inv_page", 0))
    page_count = max((len(options) + PAGE_SIZE - 1) // PAGE_SIZE, 1)
    page = min(page, page_count - 1)
    start = page * PAGE_SIZE
    visible = options[start:start + PAGE_SIZE]
    await state.update_data(inv_options=options, inv_page=page, inv_title=title, inv_add_new=add_new)

    rows = [
        [InlineKeyboardButton(text=label[:60], callback_data=f"inv:pick:{start + index}")]
        for index, (label, _) in enumerate(visible)
    ]
    if add_new:
        rows.append([InlineKeyboardButton(text=add_new[0], callback_data=add_new[1])])
    navigation: list[InlineKeyboardButton] = []
    if page:
        navigation.append(InlineKeyboardButton(text="⬅️ قبلی", callback_data=f"inv:page:{page - 1}"))
    navigation.append(InlineKeyboardButton(text=f"{page + 1}/{page_count}", callback_data="inv:noop"))
    if page + 1 < page_count:
        navigation.append(InlineKeyboardButton(text="بعدی ➡️", callback_data=f"inv:page:{page + 1}"))
    if page_count > 1:
        rows.append(navigation)
    rows.append([InlineKeyboardButton(text="لغو", callback_data="inv:cancel")])
    text = f"{title}\n\nگزینه‌ها: {len(options)}"
    if edit:
        await message.edit_text(text, reply_markup=_buttons(rows))
    else:
        await message.answer(text, reply_markup=_buttons(rows))


async def _load_level(message: Message, state: FSMContext, *, edit: bool = True) -> None:
    data = await state.get_data()
    level = data["inv_level"]
    async with AsyncSessionLocal() as session:
        service = InventoryService(session)
        if level == "branch":
            statement = select(Branch).where(Branch.is_active.is_(True)).order_by(Branch.name)
            if data.get("inv_user_role") == "branch_manager":
                statement = statement.where(Branch.id == data.get("inv_manager_branch"))
            rows = list((await session.scalars(statement)).all())
            options = [(row.name, str(row.id)) for row in rows]
            title = "گام ۱: شعبه‌ی ورود موجودی را انتخاب کنید"
        elif level == "brand":
            rows, total = await service.catalog_brands(0, 10000)
            options = [(row.name, str(row.id)) for row in rows]
            title = "گام ۲: برند را انتخاب کنید"
        elif level == "series":
            rows, total = await service.catalog_series(int(data["inv_brand_id"]), 0, 10000)
            options = [(row.name, str(row.id)) for row in rows]
            title = "گام ۳: سری را انتخاب کنید"
        elif level == "model":
            rows, total = await service.catalog_models(int(data["inv_series_id"]), 0, 10000)
            options = [(row.name, str(row.id)) for row in rows]
            title = "گام ۴: مدل را انتخاب کنید"
        elif level == "generation":
            options = [(value, value) for value in await service.catalog_generations(int(data["inv_model_id"]))]
            title = "گام ۵: نسل پردازنده را انتخاب کنید"
        elif level == "cpu":
            options = [(value, value) for value in await service.catalog_cpus(int(data["inv_model_id"]), str(data["inv_generation"]))]
            title = "گام ۶: CPU را انتخاب کنید"
        elif level == "variant":
            rows, total = await service.catalog_variants(
                int(data["inv_model_id"]), str(data["inv_generation"]), str(data["inv_cpu"]), 0, 10000
            )
            options = [
                (f"{row.ram or '-'} | {row.gpu or '-'} | {row.storage or '-'}", str(row.id))
                for row in rows
            ]
            title = "گام ۷: کانفیگ RAM / GPU / Storage را انتخاب کنید"
        else:
            return
    add_new = None
    if level == "model":
        add_new = ("➕ تعریف مدل جدید", "inv:new_model")
    elif level == "variant":
        add_new = ("➕ تعریف کانفیگ جدید", "inv:new_config")
    await _render_options(message, state, title, options, edit=edit, add_new=add_new)


@router.message(F.text == "➕ ورود کالا")
@router.message(F.text == "/stock_wizard")
async def start_tree_stock(message: Message, state: FSMContext, current_user: User) -> None:
    if not _role_allowed(current_user):
        await message.answer("ورود موجودی فقط برای مدیر، انباردار یا مدیر شعبه مجاز است.")
        return
    await state.clear()
    await state.set_state(TreeStockStates.navigating)
    await state.update_data(
        inv_level="branch",
        inv_user_role=current_user.role,
        inv_manager_branch=current_user.managed_branch_id,
        inv_actor_id=current_user.telegram_id,
    )
    await _load_level(message, state, edit=False)


@router.callback_query(F.data.startswith("inv:page:"))
async def page_stock_options(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        page = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer("صفحه معتبر نیست.", show_alert=True)
        return
    if page < 0:
        await callback.answer("صفحه معتبر نیست.", show_alert=True)
        return
    await state.update_data(inv_page=page)
    await callback.answer()
    await _load_level(callback.message, state)


@router.callback_query(F.data == "inv:noop")
async def stock_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data == "inv:cancel")
async def cancel_tree_stock(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("ورود موجودی لغو شد.")
    await callback.answer()


@router.callback_query(F.data.startswith("inv:pick:"))
async def select_stock_option(callback: CallbackQuery, state: FSMContext, current_user: User) -> None:
    data = await state.get_data()
    try:
        index = int(callback.data.rsplit(":", 1)[1])
        label, value = data["inv_options"][index]
        level = data["inv_level"]
    except (ValueError, IndexError, KeyError, TypeError):
        await callback.answer("این فهرست منقضی شده است.", show_alert=True)
        return
    if not _role_allowed(current_user):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return

    if level == "branch":
        branch_id = int(value)
        if not _branch_allowed(current_user, branch_id):
            await callback.answer("به این شعبه دسترسی ندارید.", show_alert=True)
            return
        await state.update_data(inv_branch_id=branch_id, inv_branch_name=label, inv_level="brand", inv_page=0)
    elif level == "brand":
        await state.update_data(inv_brand_id=int(value), inv_brand_name=label, inv_level="series", inv_page=0)
    elif level == "series":
        await state.update_data(inv_series_id=int(value), inv_series_name=label, inv_level="model", inv_page=0)
    elif level == "model":
        await state.update_data(inv_model_id=int(value), inv_model_name=label, inv_level="generation", inv_page=0)
    elif level == "generation":
        await state.update_data(inv_generation=value, inv_level="cpu", inv_page=0)
    elif level == "cpu":
        await state.update_data(inv_cpu=value, inv_level="variant", inv_page=0)
    elif level == "variant":
        await _show_variant_stock(callback, state, int(value))
        return
    await callback.answer()
    await _load_level(callback.message, state)


async def _show_variant_stock(callback: CallbackQuery, state: FSMContext, variant_id: int) -> None:
    data = await state.get_data()
    async with AsyncSessionLocal() as session:
        service = InventoryService(session)
        details = await service.catalog_variant_details(variant_id)
        current = await session.scalar(
            select(__import__("database.models", fromlist=["BranchStock"]).BranchStock.quantity).where(
                __import__("database.models", fromlist=["BranchStock"]).BranchStock.variant_id == variant_id,
                __import__("database.models", fromlist=["BranchStock"]).BranchStock.branch_id == int(data["inv_branch_id"]),
            )
        )
    if details is None:
        await callback.answer("کانفیگ پیدا نشد.", show_alert=True)
        return
    variant = details["variant"]
    await state.update_data(inv_variant_id=variant_id, inv_current_quantity=int(current or 0))
    rows = [[
        InlineKeyboardButton(text="+1", callback_data="inv:increment:1"),
        InlineKeyboardButton(text="+5", callback_data="inv:increment:5"),
        InlineKeyboardButton(text="+10", callback_data="inv:increment:10"),
    ], [
        InlineKeyboardButton(text="ورود تعداد دلخواه", callback_data="inv:custom"),
        InlineKeyboardButton(text="لغو", callback_data="inv:cancel"),
    ]]
    await callback.message.edit_text(
        f"شعبه: {escape(data['inv_branch_name'])}\n"
        f"کالا: {escape(details['brand'])} {escape(details['series'])} {escape(details['model'])}\n"
        f"کانفیگ: {escape(variant.generation or 'نامشخص')} / {escape(variant.cpu or '-')} / "
        f"{escape(variant.ram or '-')} / {escape(variant.gpu or '-')} / {escape(variant.storage or '-')}\n"
        f"موجودی فعلی در شعبه: {int(current or 0)}\n\nمقدار افزایش را انتخاب کنید:",
        reply_markup=_buttons(rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("inv:increment:"))
async def propose_increment(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    delta = int(callback.data.rsplit(":", 1)[1])
    await _confirm_quantity(callback, state, int(data["inv_current_quantity"]) + delta)


@router.callback_query(F.data == "inv:custom")
async def ask_custom_quantity(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TreeStockStates.entering_custom_quantity)
    await callback.message.edit_text("تعداد افزایشی را به صورت عدد مثبت بفرستید:")
    await callback.answer()


@router.message(TreeStockStates.entering_custom_quantity)
async def receive_custom_quantity(message: Message, state: FSMContext) -> None:
    text = (message.text or "").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")).strip()
    if not text.isdigit() or int(text) <= 0:
        await message.answer("عدد صحیح مثبت وارد کنید.")
        return
    data = await state.get_data()
    proposed = int(data["inv_current_quantity"]) + int(text)
    await state.update_data(inv_proposed_quantity=proposed)
    await state.set_state(TreeStockStates.confirming)
    await message.answer(
        f"موجودی از {data['inv_current_quantity']} به {proposed} عدد افزایش می‌یابد. تأیید می‌کنید؟",
        reply_markup=_buttons([[
            InlineKeyboardButton(text="✅ ثبت نهایی", callback_data="inv:confirm"),
            InlineKeyboardButton(text="انصراف", callback_data="inv:cancel"),
        ]]),
    )


async def _confirm_quantity(callback: CallbackQuery, state: FSMContext, proposed: int) -> None:
    data = await state.get_data()
    await state.update_data(inv_proposed_quantity=proposed)
    await state.set_state(TreeStockStates.confirming)
    await callback.message.edit_text(
        f"موجودی از {data['inv_current_quantity']} به {proposed} عدد افزایش می‌یابد. تأیید می‌کنید؟",
        reply_markup=_buttons([[
            InlineKeyboardButton(text="✅ ثبت نهایی", callback_data="inv:confirm"),
            InlineKeyboardButton(text="انصراف", callback_data="inv:cancel"),
        ]]),
    )
    await callback.answer()


@router.callback_query(F.data == "inv:confirm")
async def commit_tree_stock(callback: CallbackQuery, state: FSMContext, current_user: User) -> None:
    data = await state.get_data()
    branch_id = int(data["inv_branch_id"])
    if not _role_allowed(current_user) or not _branch_allowed(current_user, branch_id):
        await callback.answer("دسترسی ثبت در این شعبه را ندارید.", show_alert=True)
        await state.clear()
        return
    async with AsyncSessionLocal() as session:
        try:
            stock = await InventoryService(session).set_tree_stock_quantity(
                int(data["inv_variant_id"]),
                branch_id,
                int(data["inv_proposed_quantity"]),
                current_user.telegram_id,
            )
        except ValueError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
    await callback.message.edit_text(
        f"موجودی ثبت شد. شعبه {escape(data['inv_branch_name'])}: {stock.quantity} عدد."
    )
    await callback.answer("ثبت شد")
    await state.clear()


@router.callback_query(F.data == "inv:new_model")
async def ask_new_tree_model(callback: CallbackQuery, state: FSMContext, current_user: User) -> None:
    if not _role_allowed(current_user):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    await state.set_state(TreeStockStates.entering_new_model)
    await callback.message.edit_text("مدل جدید را بفرستید: برند | سری | مدل | توضیح کاربردی")
    await callback.answer()


@router.message(TreeStockStates.entering_new_model)
async def receive_new_tree_model(message: Message, state: FSMContext) -> None:
    parts = [part.strip() for part in (message.text or "").split("|")]
    if len(parts) < 3 or not all(parts[:3]):
        await message.answer("قالب لازم: برند | سری | مدل | توضیح کاربردی (اختیاری)")
        return
    await state.update_data(
        inv_brand_name=parts[0], inv_series_name=parts[1], inv_model_name=parts[2],
        inv_use_case=parts[3] if len(parts) > 3 else None,
    )
    await state.set_state(TreeStockStates.entering_configuration)
    await message.answer("کانفیگ را بفرستید: نسل | CPU | RAM | GPU | Storage")


@router.callback_query(F.data == "inv:new_config")
async def ask_new_tree_config(callback: CallbackQuery, state: FSMContext, current_user: User) -> None:
    if not _role_allowed(current_user):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    data = await state.get_data()
    await state.update_data(
        inv_brand_name=data.get("inv_brand_name"),
        inv_series_name=data.get("inv_series_name"),
        inv_model_name=data.get("inv_model_name"),
    )
    await state.set_state(TreeStockStates.entering_configuration)
    await callback.message.edit_text("کانفیگ را بفرستید: نسل | CPU | RAM | GPU | Storage")
    await callback.answer()


@router.message(TreeStockStates.entering_configuration)
async def receive_tree_config(message: Message, state: FSMContext, current_user: User) -> None:
    if not _role_allowed(current_user):
        await state.clear()
        await message.answer("دسترسی ندارید.")
        return
    parts = [part.strip() for part in (message.text or "").split("|")]
    if len(parts) != 5 or not all(parts):
        await message.answer("پنج مقدار لازم است: نسل | CPU | RAM | GPU | Storage")
        return
    data = await state.get_data()
    if not _branch_allowed(current_user, int(data["inv_branch_id"])):
        await state.clear()
        await message.answer("به این شعبه دسترسی ندارید.")
        return
    async with AsyncSessionLocal() as session:
        variant = await InventoryService(session).get_or_create_tree_variant(
            data["inv_brand_name"], data["inv_series_name"], data["inv_model_name"],
            *parts, use_case=data.get("inv_use_case"),
        )
        details = await InventoryService(session).catalog_variant_details(variant.id)
        branch_stock = next(
            (row["quantity"] for row in details["branches"] if row.get("branch_id") == int(data["inv_branch_id"])),
            0,
        )
    await state.update_data(inv_variant_id=variant.id, inv_current_quantity=int(branch_stock))
    await state.set_state(TreeStockStates.entering_custom_quantity)
    await message.answer(f"کانفیگ ساخته شد. موجودی فعلی: {branch_stock}. تعداد ورودی را بفرستید.")

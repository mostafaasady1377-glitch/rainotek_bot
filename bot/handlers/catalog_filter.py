from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.keyboards.inline_catalog import (
    branch_location_keyboard,
    catalog_models_keyboard,
    facet_values_keyboard,
)
from bot.services.inventory_service import InventoryService
from bot.services.sales_contact import contact_phone
from database.models import Branch, Laptop, User
from database.session import AsyncSessionLocal

router = Router()
FACETS = ("ram", "cpu", "gpu", "storage")
FACET_TITLES = {"ram": "RAM", "cpu": "پردازنده", "gpu": "گرافیک", "storage": "حافظه"}


class CatalogFilterStates(StatesGroup):
    selecting_facet = State()


async def show_catalog_models(
    message: Message,
    state: FSMContext,
    query: str | None = None,
) -> None:
    async with AsyncSessionLocal() as session:
        service = InventoryService(session)
        if query:
            laptops = await service.search_laptops(query)
            models = sorted({(laptop.model, laptop.brand.name) for laptop in laptops if laptop.brand})
        else:
            models = await service.list_laptop_models()

    if not models:
        await message.answer("مدلی با این مشخصات در کاتالوگ پیدا نشد.")
        return
    await state.clear()
    await state.update_data(catalog_models=models)
    await message.answer("مدل پایه را انتخاب کنید:", reply_markup=catalog_models_keyboard(models))


@router.callback_query(F.data.startswith("cf:model:"))
async def choose_catalog_model(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    models = data.get("catalog_models", [])
    try:
        model, brand = models[int(callback.data.rsplit(":", 1)[1])]
    except (ValueError, IndexError, TypeError):
        await callback.answer("این فهرست منقضی شده؛ دوباره مدل را جست‌وجو کنید.", show_alert=True)
        return

    await state.update_data(filters={"model": model, "brand": brand}, facet_index=0)
    await state.set_state(CatalogFilterStates.selecting_facet)
    await callback.answer()
    await _show_facet(callback.message, state)


async def _show_facet(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    facet_index = int(data.get("facet_index", 0))
    filters: dict[str, str] = data.get("filters", {})
    async with AsyncSessionLocal() as session:
        service = InventoryService(session)
        while facet_index < len(FACETS):
            field = FACETS[facet_index]
            values = await service.get_laptop_facet_values(filters["model"], field, filters.get("brand"))
            if values:
                await state.update_data(facet_index=facet_index, facet_values=values)
                await message.edit_text(
                    f"{escape(filters['brand'])} {escape(filters['model'])}\n"
                    f"فیلتر {FACET_TITLES[field]} را انتخاب کنید:",
                    reply_markup=facet_values_keyboard(field, values),
                )
                return
            facet_index += 1
        await state.update_data(facet_index=facet_index)
        matches = await service.search_laptops_by_specs(filters)
        branch_details = await service.get_laptop_branch_details([laptop.id for laptop in matches])

    await state.clear()
    if not matches:
        await message.edit_text("برای این ترکیب مشخصات، کالایی پیدا نشد.")
        return

    sections: list[str] = ["کانفیگ‌های منطبق و موجودی شعب:"]
    for laptop in matches:
        specs = " / ".join((laptop.cpu or "-", laptop.ram or "-", laptop.gpu or "-", laptop.storage or "-"))
        sections.append(
            f"\nمدل: {escape(laptop.brand.name)} {escape(laptop.model)}\n"
            f"مشخصات: {escape(specs)}"
        )
        branches = branch_details.get(laptop.id, [])
        if not branches:
            sections.append("موجودی شعب ثبت نشده است.")
        for branch in branches:
            if int(branch["available"]) <= 0:
                continue
            sections.append(
                f"📍 {escape(str(branch['name']))} ({branch['available']} عدد)\n"
                f"تلفن: {escape(str(contact_phone(branch['phone']) or 'ثبت نشده'))}\n"
                f"آدرس: {escape(str(branch['address'] or 'ثبت نشده'))}"
            )
    markup = branch_location_keyboard(
        [branch for laptop in matches for branch in branch_details.get(laptop.id, []) if int(branch["available"]) > 0]
    )
    await message.edit_text("\n".join(sections)[:4000], reply_markup=markup)


@router.callback_query(F.data.startswith("cf:value:"))
async def choose_facet_value(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    try:
        _, _, field, index_text = callback.data.split(":", 3)
        facet_index = int(data.get("facet_index", -1))
        if facet_index >= len(FACETS) or FACETS[facet_index] != field:
            raise ValueError
        values = data.get("facet_values", [])
        chosen = values[int(index_text)]
    except (ValueError, IndexError, TypeError):
        await callback.answer("گزینه معتبر نیست؛ دوباره تلاش کنید.", show_alert=True)
        return
    filters = data.get("filters", {})
    filters[field] = chosen
    await state.update_data(filters=filters, facet_index=facet_index + 1)
    await callback.answer()
    await _show_facet(callback.message, state)


@router.callback_query(F.data.startswith("cf:skip:"))
async def skip_facet(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    field = callback.data.rsplit(":", 1)[-1]
    facet_index = int(data.get("facet_index", -1))
    if facet_index < 0 or facet_index >= len(FACETS) or FACETS[facet_index] != field:
        await callback.answer("این مرحله منقضی شده است.", show_alert=True)
        return
    await state.update_data(facet_index=facet_index + 1)
    await callback.answer()
    await _show_facet(callback.message, state)


@router.callback_query(F.data.startswith("cf:loc:"))
async def send_branch_location(callback: CallbackQuery, bot) -> None:
    try:
        branch_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer("درخواست موقعیت معتبر نیست.", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        branch = await session.get(Branch, branch_id)
    if branch is None:
        await callback.answer("اطلاعات شعبه پیدا نشد.", show_alert=True)
        return
    if branch.latitude is None or branch.longitude is None:
        await callback.answer("مختصات این شعبه ثبت نشده است.", show_alert=True)
        return
    await bot.send_location(
        chat_id=callback.message.chat.id,
        latitude=float(branch.latitude),
        longitude=float(branch.longitude),
    )
    await callback.answer()

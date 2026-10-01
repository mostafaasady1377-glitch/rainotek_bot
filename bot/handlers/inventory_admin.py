from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.filters.command import CommandObject
from aiogram.types import Message
from sqlalchemy import select

from bot.services.inventory_service import InventoryService
from database.models import Branch, Laptop, User
from database.session import AsyncSessionLocal

router = Router()


def _parse_args(args: str | None, count: int) -> list[str]:
    parts = (args or "").split()
    if len(parts) != count:
        raise ValueError("تعداد ورودی‌ها درست نیست.")
    return parts


async def _authorize(message: Message, current_user: User) -> bool:
    if current_user.role == "admin":
        return True
    await message.answer("این عملیات فقط برای مدیر سیستم مجاز است.")
    return False


@router.message(Command("stock_add"))
async def cmd_stock_add(message: Message, command: CommandObject, current_user: User) -> None:
    if not await _authorize(message, current_user):
        return
    try:
        laptop_id_text, branch_code, quantity_text = _parse_args(command.args, 3)
        laptop_id, quantity = int(laptop_id_text), int(quantity_text)
    except ValueError:
        await message.answer("روش استفاده: /stock_add شناسه_لپ‌تاپ کد_شعبه تعداد")
        return

    async with AsyncSessionLocal() as session:
        laptop = await session.get(Laptop, laptop_id)
        branch = await session.scalar(select(Branch).where(Branch.code == branch_code))
        if laptop is None or branch is None:
            await message.answer("لپ‌تاپ یا شعبه پیدا نشد.")
            return
        try:
            stock = await InventoryService(session).add_stock(
                laptop.id, branch.id, quantity, current_user.telegram_id, "ورود از فرمان مدیریتی"
            )
        except ValueError as exc:
            await message.answer(str(exc))
            return
        await message.answer(f"موجودی {laptop.model} در {branch.name} اکنون {stock.quantity} عدد است.")


@router.message(Command("stock_deduct"))
async def cmd_stock_deduct(message: Message, command: CommandObject, current_user: User) -> None:
    if not await _authorize(message, current_user):
        return
    try:
        laptop_id_text, branch_code, quantity_text = _parse_args(command.args, 3)
        laptop_id, quantity = int(laptop_id_text), int(quantity_text)
    except ValueError:
        await message.answer("روش استفاده: /stock_deduct شناسه_لپ‌تاپ کد_شعبه تعداد")
        return

    async with AsyncSessionLocal() as session:
        laptop = await session.get(Laptop, laptop_id)
        branch = await session.scalar(select(Branch).where(Branch.code == branch_code))
        if laptop is None or branch is None:
            await message.answer("لپ‌تاپ یا شعبه پیدا نشد.")
            return
        try:
            stock = await InventoryService(session).deduct_stock(
                laptop.id, branch.id, quantity, current_user.telegram_id, "خروج از فرمان مدیریتی"
            )
        except ValueError as exc:
            await message.answer(str(exc))
            return
        await message.answer(f"موجودی {laptop.model} در {branch.name} اکنون {stock.quantity} عدد است.")


@router.message(Command("stock_transfer"))
async def cmd_stock_transfer(message: Message, command: CommandObject, current_user: User) -> None:
    if not await _authorize(message, current_user):
        return
    try:
        laptop_id_text, source_code, destination_code, quantity_text = _parse_args(command.args, 4)
        laptop_id, quantity = int(laptop_id_text), int(quantity_text)
    except ValueError:
        await message.answer("روش استفاده: /stock_transfer شناسه_لپ‌تاپ کد_مبدا کد_مقصد تعداد")
        return

    async with AsyncSessionLocal() as session:
        laptop = await session.get(Laptop, laptop_id)
        source = await session.scalar(select(Branch).where(Branch.code == source_code))
        destination = await session.scalar(select(Branch).where(Branch.code == destination_code))
        if laptop is None or source is None or destination is None:
            await message.answer("لپ‌تاپ یا یکی از شعبه‌ها پیدا نشد.")
            return
        try:
            await InventoryService(session).transfer_stock(
                laptop.id,
                source.id,
                destination.id,
                quantity,
                current_user.telegram_id,
            )
        except ValueError as exc:
            await message.answer(str(exc))
            return
        await message.answer(
            f"انتقال انجام شد: {quantity} عدد {laptop.model} از {source.name} به {destination.name}."
        )
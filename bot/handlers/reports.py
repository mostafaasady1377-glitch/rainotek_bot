from __future__ import annotations

import csv
import io

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, Message
from sqlalchemy import select

from bot.services.inventory_service import InventoryService
from database.models import Branch, BranchInventory, Laptop, User
from database.session import AsyncSessionLocal

router = Router()


def build_stock_csv(rows: list[tuple[object, ...]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(["branch", "branch_code", "laptop", "part_number", "quantity", "reserved", "alert_threshold"])
    writer.writerows(rows)
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


@router.message(Command("low_stock"))
async def cmd_low_stock(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        rows = await InventoryService(session).get_low_stock_alerts()
    if not rows:
        await message.answer("موردی پایین‌تر از حد هشدار موجودی نیست.")
        return

    lines = ["هشدار موجودی پایین:"]
    lines.extend(
        f"- {row['branch']} | {row['model']}: {row['quantity']} عدد (حد هشدار: {row['threshold']})"
        for row in rows
    )
    await message.answer("\n".join(lines))


@router.message(Command("stock_export"))
async def cmd_stock_export(message: Message, current_user: User) -> None:
    if current_user.role != "admin":
        await message.answer("خروجی کامل موجودی فقط برای مدیر سیستم مجاز است.")
        return

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Branch.name, Branch.code, Laptop.model, Laptop.part_number,
                   BranchInventory.quantity, BranchInventory.reserved_count,
                   BranchInventory.min_stock_alert)
            .join(BranchInventory, BranchInventory.branch_id == Branch.id)
            .join(Laptop, Laptop.id == BranchInventory.laptop_id)
            .order_by(Branch.name, Laptop.model)
        )
        rows = result.all()

    data = build_stock_csv([tuple(row) for row in rows])
    await message.answer_document(
        BufferedInputFile(data, filename="rainotek-stock.csv"),
        caption=f"گزارش موجودی راینوتک ({len(rows)} ردیف)",
    )
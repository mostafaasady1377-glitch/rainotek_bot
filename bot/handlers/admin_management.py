from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.filters.command import CommandObject
from aiogram.types import Message
from sqlalchemy import select

from database.models import Branch, User
from database.session import AsyncSessionLocal

router = Router()
ALLOWED_ROLES = {"admin", "warehouse", "branch_manager", "seller"}


async def _require_admin(message: Message, current_user: User) -> bool:
    if current_user.role == "admin":
        return True
    await message.answer("این فرمان فقط برای مدیر سیستم مجاز است.")
    return False


@router.message(Command("user_role"))
async def cmd_user_role(message: Message, command: CommandObject, current_user: User) -> None:
    if not await _require_admin(message, current_user):
        return
    parts = (command.args or "").split()
    if len(parts) != 2 or parts[1] not in ALLOWED_ROLES:
        await message.answer("روش استفاده: /user_role شناسه_تلگرام admin|warehouse|branch_manager|seller")
        return
    try:
        telegram_id = int(parts[0])
    except ValueError:
        await message.answer("شناسه تلگرام باید عدد صحیح باشد.")
        return

    async with AsyncSessionLocal() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            await message.answer("کاربر پیدا نشد؛ ابتدا کاربر باید /start را اجرا کند.")
            return
        user.role = parts[1]
        await session.commit()
        await message.answer(f"نقش کاربر {telegram_id} به {parts[1]} تغییر کرد.")


@router.message(Command("user_branch"))
async def cmd_user_branch(message: Message, command: CommandObject, current_user: User) -> None:
    if not await _require_admin(message, current_user):
        return
    parts = (command.args or "").split()
    if len(parts) != 2:
        await message.answer("روش استفاده: /user_branch شناسه_تلگرام کد_شعبه")
        return
    try:
        telegram_id = int(parts[0])
    except ValueError:
        await message.answer("شناسه تلگرام باید عدد صحیح باشد.")
        return

    async with AsyncSessionLocal() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        branch = await session.scalar(
            select(Branch).where(Branch.code == parts[1], Branch.is_active.is_(True))
        )
        if user is None or branch is None:
            await message.answer("کاربر یا شعبه فعال پیدا نشد.")
            return
        user.role = "branch_manager"
        user.managed_branch_id = branch.id
        await session.commit()
        await message.answer(f"کاربر {telegram_id} مدیر شعبه {branch.name} شد.")


@router.message(Command("branch_location"))
async def cmd_branch_location(message: Message, command: CommandObject, current_user: User) -> None:
    if not await _require_admin(message, current_user):
        return
    parts = [part.strip() for part in (command.args or "").split("|")]
    if len(parts) != 4 or not parts[0] or not parts[1]:
        await message.answer(
            "روش استفاده: /branch_location کد_شعبه | آدرس کامل | latitude | longitude"
        )
        return
    try:
        latitude, longitude = float(parts[2]), float(parts[3])
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError
    except ValueError:
        await message.answer("مختصات جغرافیایی معتبر نیست.")
        return

    async with AsyncSessionLocal() as session:
        branch = await session.scalar(select(Branch).where(Branch.code == parts[0]))
        if branch is None:
            await message.answer("شعبه پیدا نشد.")
            return
        branch.address_full = parts[1]
        branch.latitude = latitude
        branch.longitude = longitude
        await session.commit()
        await message.answer(f"آدرس و مختصات شعبه {branch.name} به‌روز شد.")


@router.message(Command("user_status"))
async def cmd_user_status(message: Message, command: CommandObject, current_user: User) -> None:
    if not await _require_admin(message, current_user):
        return
    parts = (command.args or "").split()
    if len(parts) != 2 or parts[1] not in {"active", "inactive"}:
        await message.answer("روش استفاده: /user_status شناسه_تلگرام active|inactive")
        return
    try:
        telegram_id = int(parts[0])
    except ValueError:
        await message.answer("شناسه تلگرام باید عدد صحیح باشد.")
        return
    if telegram_id == current_user.telegram_id and parts[1] == "inactive":
        await message.answer("برای جلوگیری از قفل‌شدن پنل، نمی‌توانید حساب خودتان را غیرفعال کنید.")
        return

    async with AsyncSessionLocal() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            await message.answer("کاربر پیدا نشد.")
            return
        user.is_active = parts[1] == "active"
        await session.commit()
        state = "فعال" if user.is_active else "غیرفعال"
        await message.answer(f"حساب کاربر {telegram_id} اکنون {state} است.")
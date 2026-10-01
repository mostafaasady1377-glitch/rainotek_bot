from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command
from aiogram.filters.command import CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from bot.services.inventory_service import InventoryService
from database.models import Branch, User
from database.session import AsyncSessionLocal

router = Router()
AUDIT_ROLES = {"admin", "warehouse", "branch_manager"}


class AuditWizardStates(StatesGroup):
    choosing_branch = State()
    counting_items = State()
    entering_count = State()


def _audit_count_keyboard(audit_id: int, item_index: int) -> InlineKeyboardMarkup:
    values = [
        InlineKeyboardButton(text=str(number), callback_data=f"auditq:{audit_id}:{item_index}:{number}")
        for number in range(10)
    ]
    rows = [values[index:index + 5] for index in range(0, 10, 5)]
    rows.append([
        InlineKeyboardButton(text="ورود عدد بزرگ‌تر", callback_data=f"auditq:custom:{audit_id}:{item_index}"),
        InlineKeyboardButton(text="لغو", callback_data="auditq:cancel"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_audit_item(message: Message, state: FSMContext, index: int, *, edit: bool = True) -> None:
    data = await state.get_data()
    items = data.get("audit_items", [])
    audit_id = int(data["audit_id"])
    if index >= len(items):
        async with AsyncSessionLocal() as session:
            service = InventoryService(session)
            audit, discrepancies = await service.record_audit_pending_approval(audit_id)

        await state.clear()
        diff_items = [row for row in discrepancies if row["discrepancy"] != 0]

        lines = [
            f"📋 <b>گزارش انبارگردانی #{audit_id}</b>",
            f"🏢 شعبه شناسه: <code>{audit.branch_id}</code>",
            f"📦 کل اقلام شمارش‌شده: <b>{len(discrepancies)}</b>",
            f"⚠️ اقلام دارای مغایرت: <b>{len(diff_items)}</b>",
            "",
            "<b>جزئیات مغایرت‌های کشف‌شده:</b>",
        ]

        if not diff_items:
            lines.append("✅ هیچ مغایرتی بین موجودی سیستم و شمارش واقعی وجود ندارد (تطابق کامل).")
        else:
            for item in diff_items[:12]:
                sign = f"+{item['discrepancy']}" if item['discrepancy'] > 0 else str(item['discrepancy'])
                lines.append(
                    f"• {item['brand']} {item['model']}: "
                    f"سیستم: {item['system_quantity']} | واقعی: {item['counted_quantity']} (<b>اختلاف: {sign}</b>)"
                )

        lines.extend([
            "",
            "🔒 <b>تأییدیه مدیر:</b>",
            "طبق ضوابط، اصلاح موجودی انبار فقط با تأیید مدیر کل یا مسئول مجاز شعبه امکان‌پذیر است.",
        ])

        approval_kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ تأیید و اصلاح قطعی موجودی", callback_data=f"audit_approve:{audit_id}"),
                InlineKeyboardButton(text="❌ رد مغایرت‌ها (بدون تغییر)", callback_data=f"audit_reject:{audit_id}"),
            ]
        ])

        text = "\n".join(lines)[:4000]
        if edit:
            await message.edit_text(text, reply_markup=approval_kb)
        else:
            await message.answer(text, reply_markup=approval_kb)
        return

    item = items[index]
    await state.update_data(audit_item_index=index)
    await state.set_state(AuditWizardStates.counting_items)
    text = (
        f"انبارگردانی #{audit_id} | قلم {index + 1} از {len(items)}\n"
        f"کالا: {item['brand']} {item['model']}\n"
        f"مشخصات: {item['cpu'] or '-'} / {item['ram'] or '-'} / {item['gpu'] or '-'} / {item['storage'] or '-'}\n"
        f"تعداد سیستم: {item['system_quantity']}\n\nتعداد واقعی را انتخاب کنید:"
    )
    if edit:
        await message.edit_text(text, reply_markup=_audit_count_keyboard(audit_id, index))
    else:
        await message.answer(text, reply_markup=_audit_count_keyboard(audit_id, index))


@router.message(Command("audit_wizard"))
@router.message(F.text == "🧾 انبارگردانی")
async def start_audit_wizard(message: Message, state: FSMContext, current_user: User) -> None:
    if not current_user.is_active or current_user.role not in AUDIT_ROLES:
        await message.answer("انبارگردانی فقط برای مدیر، انباردار یا مدیر شعبه مجاز است.")
        return
    async with AsyncSessionLocal() as session:
        statement = select(Branch).where(Branch.is_active.is_(True)).order_by(Branch.name)
        if current_user.role == "branch_manager":
            statement = statement.where(Branch.id == current_user.managed_branch_id)
        branches = list((await session.scalars(statement)).all())
    if not branches:
        await message.answer("شعبه‌ی فعالی برای انبارگردانی پیدا نشد.")
        return
    await state.clear()
    await state.set_state(AuditWizardStates.choosing_branch)
    await state.update_data(
        audit_branch_ids=[branch.id for branch in branches],
        audit_branch_names=[branch.name for branch in branches],
        audit_actor_id=current_user.telegram_id,
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=branch.name, callback_data=f"auditq:branch:{index}")]
        for index, branch in enumerate(branches)
    ])
    await message.answer("ابتدا شعبه‌ی انبارگردانی را انتخاب کنید:", reply_markup=keyboard)


@router.callback_query(F.data.startswith("auditq:branch:"))
async def choose_audit_branch(callback: CallbackQuery, state: FSMContext, current_user: User) -> None:
    data = await state.get_data()
    try:
        index = int(callback.data.rsplit(":", 1)[1])
        branch_id = int(data["audit_branch_ids"][index])
        branch_name = data["audit_branch_names"][index]
    except (ValueError, IndexError, KeyError, TypeError):
        await callback.answer("فهرست شعبه‌ها منقضی شده است.", show_alert=True)
        return
    if current_user.role == "branch_manager" and current_user.managed_branch_id != branch_id:
        await callback.answer("به این شعبه دسترسی ندارید.", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        service = InventoryService(session)
        try:
            audit = await service.start_audit(branch_id, current_user.telegram_id)
            items = await service.get_audit_items(audit.id)
        except ValueError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
    if not items:
        async with AsyncSessionLocal() as session:
            await InventoryService(session).finish_audit(audit.id)
        await callback.message.edit_text(f"برای شعبه {branch_name} کالای فعالی در کاتالوگ ثبت نشده است.")
        await state.clear()
        await callback.answer()
        return
    await state.update_data(audit_id=audit.id, audit_branch_id=branch_id, audit_items=items)
    await callback.answer()
    await _show_audit_item(callback.message, state, 0)


@router.callback_query(F.data.startswith("auditq:") & ~F.data.startswith("auditq:branch:") & ~F.data.startswith("auditq:custom:") & (F.data != "auditq:cancel"))
async def record_audit_button_count(callback: CallbackQuery, state: FSMContext, current_user: User) -> None:
    try:
        _, audit_id_text, index_text, count_text = callback.data.split(":", 3)
        audit_id, index, quantity = int(audit_id_text), int(index_text), int(count_text)
    except ValueError:
        await callback.answer("تعداد معتبر نیست.", show_alert=True)
        return
    data = await state.get_data()
    if audit_id != data.get("audit_id") or index != data.get("audit_item_index"):
        await callback.answer("این دکمه منقضی شده است.", show_alert=True)
        return
    item = data["audit_items"][index]
    async with AsyncSessionLocal() as session:
        await InventoryService(session).submit_audit_item(audit_id, int(item["laptop_id"]), quantity)
    await callback.answer("شمارش ثبت شد")
    await _show_audit_item(callback.message, state, index + 1)


@router.callback_query(F.data.startswith("auditq:custom:"))
async def request_audit_custom_count(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        _, _, audit_id, index = callback.data.split(":", 3)
        if int(audit_id) != int((await state.get_data())["audit_id"]):
            raise ValueError
        if int(index) != int((await state.get_data())["audit_item_index"]):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        await callback.answer("این دکمه منقضی شده است.", show_alert=True)
        return
    await state.set_state(AuditWizardStates.entering_count)
    await callback.message.edit_text("تعداد واقعی را به‌صورت عدد صحیح نامنفی بفرستید:")
    await callback.answer()


@router.message(AuditWizardStates.entering_count)
async def record_custom_audit_count(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")).strip()
    if not raw.isdigit():
        await message.answer("عدد صحیح نامنفی وارد کنید.")
        return
    data = await state.get_data()
    index = int(data["audit_item_index"])
    item = data["audit_items"][index]
    async with AsyncSessionLocal() as session:
        await InventoryService(session).submit_audit_item(
            int(data["audit_id"]), int(item["laptop_id"]), int(raw)
        )
    next_message = await message.answer("شمارش ثبت شد.")
    await _show_audit_item(next_message, state, index + 1, edit=False)


@router.callback_query(F.data == "auditq:cancel")
async def cancel_audit_wizard(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("فرآیند انبارگردانی لغو شد؛ چک‌لیست باز برای تکمیل باقی ماند.")
    await callback.answer()


async def _require_admin(message: Message, current_user: User) -> bool:
    if current_user.role == "admin":
        return True
    await message.answer("مدیریت انبارگردانی فقط برای مدیر سیستم مجاز است.")
    return False


@router.message(Command("audit_start"))
async def cmd_audit_start(message: Message, command: CommandObject, current_user: User) -> None:
    if not await _require_admin(message, current_user):
        return
    branch_code = (command.args or "").strip()
    if not branch_code:
        await message.answer("روش استفاده: /audit_start کد_شعبه")
        return

    async with AsyncSessionLocal() as session:
        branch = await session.scalar(select(Branch).where(Branch.code == branch_code))
        if branch is None or not branch.is_active:
            await message.answer("شعبه فعال با این کد پیدا نشد.")
            return
        try:
            audit = await InventoryService(session).start_audit(branch.id, current_user.telegram_id)
        except ValueError as exc:
            await message.answer(str(exc))
            return
        await message.answer(
            f"انبارگردانی شعبه {branch.name} آغاز شد. شناسه: {audit.id}\n"
            f"برای ثبت شمارش: /audit_count {audit.id} شناسه_لپ‌تاپ تعداد"
        )


@router.message(Command("audit_count"))
async def cmd_audit_count(message: Message, command: CommandObject, current_user: User) -> None:
    if not await _require_admin(message, current_user):
        return
    parts = (command.args or "").split()
    if len(parts) != 3:
        await message.answer("روش استفاده: /audit_count شناسه_ممیزی شناسه_لپ‌تاپ تعداد")
        return
    try:
        audit_id, laptop_id, counted_quantity = (int(part) for part in parts)
    except ValueError:
        await message.answer("شناسه‌ها و تعداد باید عدد صحیح باشند.")
        return

    async with AsyncSessionLocal() as session:
        try:
            item = await InventoryService(session).submit_audit_item(
                audit_id=audit_id,
                laptop_id=laptop_id,
                counted_quantity=counted_quantity,
            )
        except ValueError as exc:
            await message.answer(str(exc))
            return
        await message.answer(
            f"شمارش ثبت شد. سیستم: {item.system_quantity}، واقعی: {item.counted_quantity}، "
            f"اختلاف: {item.discrepancy:+d}"
        )


@router.message(Command("audit_finish"))
async def cmd_audit_finish(message: Message, command: CommandObject, current_user: User) -> None:
    if not await _require_admin(message, current_user):
        return
    try:
        audit_id = int((command.args or "").strip())
    except ValueError:
        await message.answer("روش استفاده: /audit_finish شناسه_ممیزی")
        return

    async with AsyncSessionLocal() as session:
        service = InventoryService(session)
        try:
            audit = await service.finish_audit(audit_id)
        except ValueError as exc:
            await message.answer(str(exc))
            return
        items = await service.get_audit_summary(audit_id)
        discrepancies = sum(bool(item["discrepancy"]) for item in items)
        await message.answer(
            f"انبارگردانی #{audit.id} برای شعبه {audit.branch_id} تکمیل شد.\n"
            f"اقلام شمارش‌شده: {len(items)}\n"
            f"اقلام دارای اختلاف: {discrepancies}\n"
            "موجودی شعبه بر اساس شمارش واقعی اصلاح شد."
        )


@router.callback_query(F.data.startswith("audit_approve:"))
async def handle_audit_approve(callback: CallbackQuery, current_user: User) -> None:
    if current_user.role not in ("admin", "branch_manager"):
        await callback.answer(
            "شما دسترسی لازم برای تأیید و اصلاح موجودی انبارگردانی را ندارید. فقط مدیر سیستم یا مسئول شعبه مجاز است.",
            show_alert=True,
        )
        return

    audit_id = int(callback.data.rsplit(":", 1)[1])
    idempotency_key = f"audit_appr_{audit_id}"

    from bot.services.idempotency_service import IdempotencyService
    from database.models import StockAuditChecklist

    async with AsyncSessionLocal() as session:
        service = InventoryService(session)

        async def _do_approve():
            return await service.approve_and_apply_audit(audit_id, current_user.telegram_id)

        try:
            is_new, result = await IdempotencyService.execute_idempotent(
                session=session,
                idempotency_key=idempotency_key,
                actor_telegram_id=current_user.telegram_id,
                action_type="AUDIT_APPROVE",
                operation_coro=_do_approve,
            )
        except Exception as exc:
            await callback.answer(f"خطا در تأیید: {exc}", show_alert=True)
            return

    await callback.message.edit_text(
        callback.message.text + f"\n\n✅ <b>موجودی شعبه توسط مدیر ({current_user.full_name or current_user.username}) اصلاح و تأیید قطعی شد.</b>"
    )
    await callback.answer("اصلاح موجودی با موفقیت ثبت شد.")


@router.callback_query(F.data.startswith("audit_reject:"))
async def handle_audit_reject(callback: CallbackQuery, current_user: User) -> None:
    if current_user.role not in ("admin", "branch_manager"):
        await callback.answer("شما دسترسی لازم برای رد نتایج انبارگردانی را ندارید.", show_alert=True)
        return

    audit_id = int(callback.data.rsplit(":", 1)[1])
    from database.models import StockAuditChecklist

    async with AsyncSessionLocal() as session:
        audit = await session.get(StockAuditChecklist, audit_id)
        if audit:
            audit.status = "rejected"
            audit.notes = (audit.notes or "") + f" | رد شده توسط {current_user.telegram_id}"
            await session.commit()

    await callback.message.edit_text(
        callback.message.text + f"\n\n❌ <b>مغایرت‌ها توسط مدیر ({current_user.full_name or current_user.username}) رد شد و هیچ تغییری در موجودی اعمال نشد.</b>"
    )
    await callback.answer("مغایرت‌ها رد شدند.")
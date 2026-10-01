from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.keyboards.reply_menus import main_menu_kb
from bot.services.support_service import open_support_request
from database.session import AsyncSessionLocal

router = Router()


@router.message(F.text == "💬 کارشناس و پشتیبانی آنلاین")
async def open_online_support(message: Message, state: FSMContext, current_user=None):
    if not message.from_user:
        return
    async with AsyncSessionLocal() as session:
        request = await open_support_request(session, message.from_user.id)
    await state.clear()
    await message.answer(
        f"💬 <b>کارشناس و پشتیبانی آنلاین راینوتک</b>\n\n"
        f"درخواست شما با شمارهٔ <b>{request.id}</b> ثبت شد.\n"
        "ارتباط مستقیم با کارشناسان پس از فعال‌سازی پنل پشتیبانی در دسترس قرار می‌گیرد.",
        reply_markup=main_menu_kb(role=current_user.role if current_user else "customer"),
    )

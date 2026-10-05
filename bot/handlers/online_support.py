from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.keyboards.reply_menus import main_menu_kb
from bot.services.support_service import open_support_request
from bot.services.sales_contact import contact_text, expert_chat_keyboard
from database.session import AsyncSessionLocal

router = Router()


INSTALLMENT_SECTIONS = {
    "terms": "📋 شرایط خرید اقساطی\n\nمبلغ پیش‌پرداخت، تعداد ماه‌ها، نرخ و هزینه‌های قرارداد باید برای مدل انتخابی توسط کارشناس فروش تأیید شوند. شرایط قطعی هنوز در بات ثبت نشده است؛ این صفحه وعدهٔ فروش یا تأیید اعتبار نیست.",
    "documents": "📄 مدارک لازم\n\nفهرست قطعی مدارک و نوع ضمانت هنوز از طرف فروشگاه ثبت نشده است. پیش از ارسال هر مدرک، فهرست لازم و مسیر امن دریافت آن را از کارشناس خود بگیرید. مدارک هویتی یا تصویر چک را در این بات ارسال نکنید.",
    "calculation": "🧮 نحوهٔ محاسبهٔ اقساط\n\nماندهٔ خرید = قیمت نقدی − پیش‌پرداخت\nدر قرارداد بدون کارمزد: مبلغ هر قسط = ماندهٔ خرید ÷ تعداد اقساط\nدر قرارداد دارای کارمزد: محاسبه به نرخ، شیوهٔ محاسبه و هزینه‌های قرارداد بستگی دارد. نرخ و مدت هنوز مشخص نیست؛ مبلغ قطعی باید توسط کارشناس تأیید شود.",
}


def installment_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 شرایط خرید", callback_data="installment:terms")],
        [InlineKeyboardButton(text="📄 مدارک لازم", callback_data="installment:documents")],
        [InlineKeyboardButton(text="🧮 نحوه محاسبه اقساط", callback_data="installment:calculation")],
    ])


@router.message(F.text.in_({"💳 شرایط اقساط", "💳 شرایط خرید اقساطی"}))
async def installment_terms(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "💳 شرایط خرید اقساطی راینو\n\nبخش موردنظر را انتخاب کنید. شرایط نهایی هر مدل با تأیید کارشناس فروش مشخص می‌شود."
        + contact_text(),
        reply_markup=installment_menu(),
    )


@router.callback_query(F.data.startswith("installment:"))
async def installment_section(callback: CallbackQuery):
    section = callback.data.split(":", 1)[1]
    if section not in INSTALLMENT_SECTIONS:
        await callback.answer("گزینه نامعتبر است.", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(INSTALLMENT_SECTIONS[section] + contact_text(), reply_markup=installment_menu())


@router.message(F.text == "💬 کارشناس و پشتیبانی آنلاین")
async def open_online_support(message: Message, state: FSMContext, current_user=None):
    if not message.from_user:
        return
    assigned_contact = contact_text()
    if assigned_contact:
        await state.clear()
        await message.answer(assigned_contact.strip(), reply_markup=expert_chat_keyboard())
        return
    async with AsyncSessionLocal() as session:
        request = await open_support_request(session, message.from_user.id)
    await state.clear()
    await message.answer(
        f"💬 <b>کارشناس و پشتیبانی آنلاین راینوتک</b>\n\n"
        f"درخواست شما با شمارهٔ <b>{request.id}</b> ثبت شد.\n"
        "ارتباط مستقیم با کارشناسان پس از فعال‌سازی پنل پشتیبانی در دسترس قرار می‌گیرد.",
        reply_markup=main_menu_kb("customer"),
    )

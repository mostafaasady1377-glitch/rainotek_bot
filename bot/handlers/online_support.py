from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.keyboards.reply_menus import main_menu_kb
from bot.services.support_service import open_support_request
from bot.services.sales_contact import contact_text, expert_chat_keyboard
from database.session import AsyncSessionLocal
from database.models import Laptop, LaptopBrand
from sqlalchemy import select, func
from html import escape
from bot.services.installment_calculator import installment_quote, toman_amount

router = Router()


INSTALLMENT_SECTIONS = {
    "terms": "📋 شرایط خرید اقساطی\n\nبدون پیش‌پرداخت · ۱۲ ماه\nسود سادهٔ ماهانه: ۲٫۵٪ روی قیمت نقدی دستگاه.\nمدارک و روش پرداخت پس از اعلام فروشگاه تکمیل می‌شود؛ قرارداد نهایی با تأیید فروشگاه است.",
    "documents": "📄 مدارک لازم\n\nفهرست قطعی مدارک و نوع ضمانت هنوز از طرف فروشگاه ثبت نشده است. پیش از ارسال هر مدرک، فهرست لازم و مسیر امن دریافت آن را از کارشناس خود بگیرید. مدارک هویتی یا تصویر چک را در این بات ارسال نکنید.",
    "calculation": "🧮 نحوهٔ محاسبهٔ اقساط\n\nسود ماهانه = قیمت × ۲٫۵٪\nسود ۱۲ ماه = سود ماهانه × ۱۲\nقسط ماهانه = (قیمت + سود ۱۲ ماه) ÷ ۱۲\n\nبدون پیش‌پرداخت؛ سود ساده روی قیمت اولیه، نه سود مرکب یا ماندهٔ بدهی.",
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
    await show_installment_models(message)


async def show_installment_models(message, page=0):
    size = 8
    async with AsyncSessionLocal() as session:
        total = await session.scalar(select(func.count(Laptop.id)).where(Laptop.status == 'active'))
        pages = max(1, (total + size - 1) // size)
        page = min(max(page, 0), pages - 1)
        items = (await session.execute(select(Laptop, LaptopBrand.name).join(LaptopBrand).where(Laptop.status == 'active').order_by(LaptopBrand.name, Laptop.model, Laptop.id).offset(page * size).limit(size))).all()
    rows = [[InlineKeyboardButton(text=f'{brand} {lap.model} | {lap.ram or "-"} | {toman_amount(lap.price) if lap.price and lap.price > 0 else "قیمت نامشخص"}'[:64], callback_data=f'installment:model:{lap.id}')] for lap, brand in items]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text='⬅️ قبلی', callback_data=f'installment:page:{page-1}'))
    if page + 1 < pages:
        nav.append(InlineKeyboardButton(text='بعدی ➡️', callback_data=f'installment:page:{page+1}'))
    if nav:
        rows.append(nav)
    rows.extend(installment_menu().inline_keyboard)
    await message.answer('💳 خرید اقساطی · بدون پیش‌پرداخت · ۱۲ ماه\nسود سادهٔ ماهانه ۲٫۵٪\n\n' + ('مدل را برای محاسبه انتخاب کنید:' if items else 'فعلاً مدل فعالی ثبت نشده است.') + contact_text(), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith('installment:page:'))
async def installment_page(callback: CallbackQuery):
    try:
        page = int(callback.data.rsplit(':', 1)[1])
    except ValueError:
        await callback.answer('گزینه نامعتبر است.', show_alert=True)
        return
    await callback.answer()
    await show_installment_models(callback.message, page)


@router.callback_query(F.data.startswith('installment:model:'))
async def installment_model(callback: CallbackQuery):
    try:
        product_id = int(callback.data.rsplit(':', 1)[1])
    except ValueError:
        await callback.answer('گزینه نامعتبر است.', show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        row = (await session.execute(select(Laptop, LaptopBrand.name).join(LaptopBrand).where(Laptop.id == product_id, Laptop.status == 'active'))).first()
    if not row:
        await callback.answer('این مدل دیگر در فهرست فعال نیست.', show_alert=True)
        return
    lap, brand = row
    await callback.answer()
    text = f'💳 اقساط {escape(brand)} {escape(lap.model)}\n{escape(lap.cpu or "-")} · {escape(lap.ram or "-")} · {escape(lap.storage or "-")}\n\n'
    if not lap.price or lap.price <= 0:
        text += 'قیمت معتبر این مدل ثبت نشده؛ محاسبه پس از تعیین قیمت امکان‌پذیر است.'
    else:
        quote = installment_quote(lap.price)
        text += (f'قیمت نقدی: {toman_amount(quote["price"])}\nپیش‌پرداخت: صفر\nمدت: ۱۲ ماه\nسود سادهٔ ماهانه: ۲٫۵٪\n'
                 f'سود هر ماه: {toman_amount(quote["monthly_interest"])}\nسود ۱۲ ماه: {toman_amount(quote["yearly_interest"])}\n'
                 f'جمع بازپرداخت: {toman_amount(quote["total"])}\n\nقسط ماهانه: حدود {toman_amount(quote["monthly_payment"])}\n\n'
                 'بر اساس قیمت فعلی؛ مبالغ به تومان گرد شده‌اند. قرارداد نهایی با تأیید فروشگاه است.')
    await callback.message.answer(text + contact_text(), reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='💻 مشخصات مدل', callback_data=f'tree:laptop:{lap.id}')],
        [InlineKeyboardButton(text='🔄 محاسبه با قیمت فعلی', callback_data=f'installment:model:{lap.id}')],
        [InlineKeyboardButton(text='🔙 مدل‌های اقساطی', callback_data='installment:page:0')],
    ]))


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

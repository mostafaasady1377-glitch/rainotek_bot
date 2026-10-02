"""AI-specific menu, guided laptop advice, and API purchase inquiries."""

from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from bot.services.ai_feature_visits import record_ai_visit
from bot.services.ai_search_service import AISearchService
from bot.services.laptop_recommendations import recommend_laptops
from bot.keyboards.reply_menus import AI_MENU_LABEL, main_menu_kb
from database.models import AiApiInquiry, User
from database.session import AsyncSessionLocal

router = Router()
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")


class AdviceState(StatesGroup):
    purpose = State()
    detail = State()
    budget = State()


def keyboard(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=data) for label, data in row]
        for row in rows
    ])


AI_ASSISTANT_LABEL = "\u200f🧠 تحلیل سیستم با \u2066ai\u2069"

AI_MENU = keyboard([
    [(AI_ASSISTANT_LABEL, "ai:assistant")],
    [("🌐 اتصال امن راینوتک", "vpn:home")],
    [("✨ سرویس‌های هوش مصنوعی", "ai:apis")],
    [("🔙 بازگشت به منوی اصلی", "ai:main_menu")],
])

PURPOSES = {
    "study": ("درس و کارهای روزمره", "درس اداری"),
    "office": ("حسابداری و کار اداری", "حسابداری اداری"),
    "engineering": ("مهندسی و طراحی", "مهندسی"),
    "graphics": ("گرافیک و تدوین", "گرافیک"),
    "gaming": ("بازی", "گیمینگ"),
    "programming": ("برنامه‌نویسی", "برنامه نویسی"),
    "trading": ("ترید و بازار مالی", "ترید"),
    "content": ("تدوین و تولید محتوا", "تدوین تولید محتوا"),
}

PURPOSE_GUIDANCE = {
    "study": "برای درس و کار سبک روزمره، پردازندهٔ معمولی، ۴ گیگابایت رم و حافظهٔ HDD هم می‌تواند کافی باشد؛ گرافیک مجزا اولویت ندارد. برای چند برنامهٔ هم‌زمان، رم ۸ و SSD روان‌تر است.",
    "office": "برای حسابداری و کار اداری معمول، ۸ گیگابایت رم و پردازندهٔ میان‌رده کفایت می‌کند؛ گرافیک مجزا لازم نیست. سرعت حافظه در باز شدن برنامه‌ها اثر دارد.",
    "graphics": "برای تدوین و کار گرافیکی سنگین، گرافیک مجزا، پردازندهٔ مناسب و رم بیشتر را در اولویت گذاشتم.",
    "engineering": "برای نرم‌افزارهای مهندسی، پردازنده و رم مناسب مهم‌اند؛ در طراحی سه‌بعدی، گرافیک مجزا کمک می‌کند.",
    "programming": "برای برنامه‌نویسی، پردازندهٔ میان‌رده به بالا، SSD و رم کافی برای ابزارهای هم‌زمان را در نظر گرفتم.",
    "gaming": "برای بازی، فقط مدل‌های دارای گرافیک مجزا را بررسی کردم؛ کارایی نهایی به مدل گرافیک، بازی و تنظیمات بستگی دارد.",
    "trading": "برای ترید و چند پنجرهٔ هم‌زمان، رم و پردازندهٔ مناسب را در اولویت گذاشتم؛ تعداد خروجی تصویر را پیش از خرید بررسی کنید.",
    "content": "برای تدوین و تولید محتوا، گرافیک مجزا و پردازندهٔ مناسب را در اولویت گذاشتم. رم ۸ گیگابایت برای پروژهٔ سبک پذیرفته شده، ولی برای پروژهٔ سنگین‌تر ۱۶ گیگابایت یا بیشتر بهتر است.",
}

API_GROUPS = {
    "text": ("متن و گفت‌وگو", ["openai", "gemini", "claude", "grok", "mistral", "cohere", "deepseek"]),
    "image": ("تصویر", ["openai_image", "imagen", "stability"]),
    "video": ("ویدئو", ["veo", "runway", "luma"]),
    "audio": ("صدا و گفتار", ["openai_audio", "elevenlabs", "google_speech"]),
    "embedding": ("جستجو و امبدینگ", ["openai_embedding", "voyage", "cohere_embedding"]),
}
API_PRODUCTS = {
    "ai_development": ("توسعه هوش مصنوعی", "طراحی دستیار، اتوماسیون و اتصال سرویس‌های هوش مصنوعی"),
    "openai": ("OpenAI API", "متن، ابزار و گفت‌وگو"),
    "gemini": ("Google Gemini API", "متن و چندرسانه‌ای"),
    "claude": ("Anthropic Claude API", "متن و تحلیل"),
    "grok": ("xAI Grok API", "متن و گفت‌وگو"),
    "mistral": ("Mistral API", "مدل‌های زبانی"),
    "cohere": ("Cohere API", "متن و جستجو"),
    "deepseek": ("DeepSeek API", "مدل‌های زبانی"),
    "openai_image": ("OpenAI Images API", "ساخت و ویرایش تصویر"),
    "imagen": ("Google Imagen API", "ساخت تصویر"),
    "stability": ("Stability AI API", "ساخت تصویر"),
    "veo": ("Google Veo API", "ساخت ویدئو"),
    "runway": ("Runway API", "ساخت ویدئو"),
    "luma": ("Luma API", "ساخت ویدئو"),
    "openai_audio": ("OpenAI Audio API", "گفتار و رونویسی"),
    "elevenlabs": ("ElevenLabs API", "تولید صدا"),
    "google_speech": ("Google Speech API", "گفتار و رونویسی"),
    "openai_embedding": ("OpenAI Embeddings API", "بردارهای متنی"),
    "voyage": ("Voyage AI API", "بردارهای جستجو"),
    "cohere_embedding": ("Cohere Embed API", "بردارهای جستجو"),
}

# Public list prices from the providers' US monthly plans. These are reference
# prices, not RAINOTEK stock or checkout prices.
OFFICIAL_SUBSCRIPTIONS = {
    "chatgpt_plus": ("ChatGPT Plus", "$20 / month", "https://help.openai.com/en/articles/6950777-what-is-chatgpt-free-plan", "اشتراک ماهانهٔ ChatGPT"),
    "gemini_pro": ("Google AI Pro", "$19.99 / month", "https://gemini.google/subscriptions/", "شامل دسترسی بیشتر به Gemini و Google Flow"),
    "claude_pro": ("Claude Pro", "$20 / month", "https://claude.com/pricing", "اشتراک ماهانهٔ Claude"),
    "codex": ("Codex", "Included with ChatGPT Plus ($20 / month)", "https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan", "Codex پلن مستقلِ این فهرست نیست؛ در ChatGPT Plus گنجانده شده است."),
    "google_flow": ("Google Flow", "Included with Google AI Pro ($19.99 / month)", "https://gemini.google/subscriptions/", "Flow در پلن Google AI Pro اعتبار ماهانه دارد و پلن مستقلِ این فهرست نیست."),
    "perplexity_pro": ("Perplexity Pro", "$20 / month", "https://www.perplexity.ai/hub", "اشتراک ماهانهٔ جستجو و پژوهش"),
    "copilot_pro": ("Microsoft Copilot Pro", "$20 / month", "https://www.microsoft.com/en-us/store/b/copilotpro", "اشتراک ماهانهٔ Copilot Pro"),
    "supergrok": ("SuperGrok", "$30 / month", "https://x.ai/pricing", "اشتراک ماهانهٔ Grok"),
    "midjourney_basic": ("Midjourney Basic", "$10 / month", "https://docs.midjourney.com/hc/en-us/articles/27870484040333-Comparing-Midjourney-Plans", "ساخت تصویر با پلن پایهٔ Midjourney"),
    "runway_standard": ("Runway Standard", "$15 / month", "https://help.runwayml.com/hc/en-us/articles/21776691742867-What-do-I-do-if-I-accidentally-selected-a-yearly-plan-instead-of-monthly", "تولید و ویرایش ویدئو با پلن Standard"),
    "elevenlabs_starter": ("ElevenLabs Starter", "$6 / month", "https://elevenlabs.io/pricing", "تولید صدا و گفتار با پلن Starter"),
    "leonardo_essential": ("Leonardo AI Essential", "$12 / month", "https://www.leonardo.ai/pricing", "ساخت تصویر و ویدئو با پلن Essential"),
}


@router.message(F.text == AI_MENU_LABEL)
async def ai_home_message(message: Message, state: FSMContext) -> None:
    await state.clear()
    await record_ai_visit(message.from_user.id, "ai_menu")
    await message.answer("🤖 <b>هوش مصنوعی ai راینوتک</b>\nچه کمکی از دستم برمی‌آید؟", reply_markup=AI_MENU)


@router.callback_query(F.data == "ai:home")
async def ai_home_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    await callback.message.answer("🤖 <b>هوش مصنوعی ai راینوتک</b>\nبخش دلخواهتان را انتخاب کنید:", reply_markup=AI_MENU)


@router.callback_query(F.data == "ai:main_menu")
async def ai_main_menu(callback: CallbackQuery, state: FSMContext, current_user: User | None = None) -> None:
    await state.clear()
    await callback.answer()
    await callback.message.answer("🏠 منوی اصلی راینوتک", reply_markup=main_menu_kb(current_user.role if current_user else "customer"))


@router.callback_query(F.data == "ai:assistant")
async def assistant_home(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(AdviceState.purpose)
    await callback.answer()
    await callback.message.answer(
        f"<b>{AI_ASSISTANT_LABEL}</b>\n\nلپ‌تاپ را بیشتر برای چه کاری لازم دارید؟",
        reply_markup=keyboard([
            [("💼 حسابداری و اداری", "ai:purpose:office"), ("📚 درس و روزمره", "ai:purpose:study")],
            [("🎨 گرافیک و تدوین", "ai:purpose:graphics"), ("📐 مهندسی", "ai:purpose:engineering")],
            [("💻 برنامه‌نویسی", "ai:purpose:programming"), ("🎮 بازی", "ai:purpose:gaming")],
            [("📈 ترید و بازار مالی", "ai:purpose:trading"), ("🎬 تدوین و تولید محتوا", "ai:purpose:content")],
            [("🚀 توسعه هوش مصنوعی", "ai:development")],
            [("🔙 بازگشت به هوش مصنوعی ai", "ai:home")],
        ]),
    )


@router.callback_query(F.data == "ai:development")
async def ai_development(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await record_ai_visit(callback.from_user.id, "ai_development")
    await callback.answer()
    await callback.message.answer(
        "🚀 <b>توسعه هوش مصنوعی</b>\n\nبرای ساخت دستیار، اتوماسیون یا اتصال هوش مصنوعی به کسب‌وکار خودتان، "
        "می‌توانید درخواست بررسی ثبت کنید تا نیاز و هزینهٔ پروژه مشخص شود.",
        reply_markup=keyboard([
            [("📝 ثبت درخواست توسعه", "ai:api_request:ai_development")],
            [("🔙 بازگشت به تحلیل سیستم", "ai:assistant")],
        ]),
    )


@router.callback_query(F.data.startswith("ai:purpose:"))
async def assistant_purpose(callback: CallbackQuery, state: FSMContext) -> None:
    key = callback.data.rsplit(":", 1)[-1]
    if key not in PURPOSES:
        await callback.answer("گزینه نامعتبر است.", show_alert=True)
        return
    await state.clear()
    await callback.answer()
    await show_purpose_recommendations(callback, key, 0)


@router.callback_query(F.data.startswith("ai:purpose_page:"))
async def assistant_purpose_page(callback: CallbackQuery) -> None:
    try:
        _, _, key, page_text = callback.data.split(":", 3)
        page = int(page_text)
    except (ValueError, AttributeError):
        await callback.answer("صفحه نامعتبر است.", show_alert=True)
        return
    if key not in PURPOSES or page < 0:
        await callback.answer("صفحه نامعتبر است.", show_alert=True)
        return
    await callback.answer()
    await show_purpose_recommendations(callback, key, page)


async def show_purpose_recommendations(callback: CallbackQuery, key: str, page: int) -> None:
    async with AsyncSessionLocal() as session:
        results = await recommend_laptops(session, key, limit=20)
    page_size = 10
    total_pages = max(1, (len(results) + page_size - 1) // page_size)
    page = min(page, total_pages - 1)
    current = results[page * page_size:(page + 1) * page_size]
    lines = [f"🧠 <b>پیشنهاد برای {escape(PURPOSES[key][0])}</b>", "", escape(PURPOSE_GUIDANCE[key]), ""]
    rows = []
    if current:
        lines.append(f"<b>{len(results)} مدل موجود و مناسب‌تر · صفحهٔ {page + 1} از {total_pages}</b>")
        lines.append("مرتب‌شده از قیمت کمتر به بیشتر؛ امتیاز تناسب از مشخصات ثبت‌شده محاسبه شده است.\n")
        for index, item in enumerate(current, page * page_size + 1):
            name = f"{item['brand']} {item['model']}"
            lines.append(f"{index}. <b>{escape(name)}</b> — {item['price']:,} تومان · امتیاز تناسب {item['score']} از ۱۰۰")
            lines.append("   " + " | ".join(escape(reason) for reason in item["reasons"]))
            lines.append(f"   موجودی قابل فروش: {item['quantity']} دستگاه")
            rows.append([(f"💻 {name[:48]} · مشخصات و عکس", f"ai:laptop:{item['id']}")])
    else:
        lines.append("فعلاً مدلی با مشخصات کافی و موجودی قابل فروش برای این کاربرد ثبت نشده است.")
    lines.append("\nپیشنهادها بر اساس مشخصات و موجودی ثبت‌شدهٔ فعلی‌اند؛ برای انتخاب نهایی، جزئیات هر مدل را باز کنید.")
    navigation = []
    if page > 0:
        navigation.append(("⬅️ ارزان‌ترها", f"ai:purpose_page:{key}:{page - 1}"))
    if page + 1 < total_pages:
        navigation.append(("➡️ گزینه‌های بعدی", f"ai:purpose_page:{key}:{page + 1}"))
    if navigation:
        rows.append(navigation)
    rows.extend([[("🔄 انتخاب کاربرد دیگر", "ai:assistant")], [("🔙 بازگشت به هوش مصنوعی ai", "ai:home")]])
    await callback.message.answer("\n".join(lines), reply_markup=keyboard(rows))


@router.message(AdviceState.detail, F.text & ~F.text.startswith("/"))
async def assistant_detail(message: Message, state: FSMContext) -> None:
    await state.update_data(detail=message.text.strip()[:160])
    await state.set_state(AdviceState.budget)
    await message.answer("بودجه‌تان حدوداً چند میلیون تومان است؟ مثلاً «تا ۵۰ میلیون». اگر محدودیتی ندارید «فرقی ندارد» بنویسید.")


def advice_query(purpose: str, detail: str, budget: str) -> str:
    base = PURPOSES[purpose][1]
    if purpose == "gaming":
        base = "لپ‌تاپ گیمینگ"
    return f"{base} {detail} {budget}".strip()


@router.message(AdviceState.budget, F.text & ~F.text.startswith("/"))
async def assistant_recommend(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    purpose = data.get("purpose", "study")
    detail = data.get("detail", "")
    budget = message.text.strip()[:80]
    await state.clear()
    query = advice_query(purpose, detail, budget)
    async with AsyncSessionLocal() as session:
        results = await AISearchService.smart_search(session, query, limit=5)
    intro = {
        "study": PURPOSE_GUIDANCE["study"],
        "office": PURPOSE_GUIDANCE["office"],
        "engineering": "برای طراحی و رندر، نوع نرم‌افزار تعیین می‌کند که گرافیک مجزا و رم ۱۶ گیگابایت یا بیشتر لازم است یا نه.",
        "graphics": "برای تدوین و کار سه‌بعدی، رم ۱۶ گیگابایت و گرافیک مجزا را در اولویت بگذارید.",
        "gaming": "برای بازی، مدل گرافیک و توان آن از برچسب کلی «گیمینگ» مهم‌تر است؛ اجرای بازی به تنظیمات و نسخه آن بستگی دارد.",
        "programming": "برای برنامه‌نویسی، SSD و رم ۱۶ گیگابایت انتخاب منعطف‌تری است؛ برای کارهای سنگین‌تر نیاز را دقیق‌تر بررسی کنید.",
        "trading": PURPOSE_GUIDANCE["trading"],
        "content": PURPOSE_GUIDANCE["content"],
    }[purpose]
    rows = [[(f"{r['brand']} {r['model']} · {r['price']:,} تومان" if r.get("price") else f"{r['brand']} {r['model']} · استعلام قیمت", f"ai:laptop:{r['id']}")] for r in results]
    rows.extend([[("🔄 گفت‌وگوی تازه", "ai:assistant")], [("🔙 بازگشت به هوش مصنوعی ai", "ai:home")]])
    count = f"{len(results)} مدل از موجودی فعلی پیدا شد. برای مشخصات و عکس، مدل را باز کنید." if results else "مدل منطبقی در موجودی فعلی پیدا نشد؛ می‌توانید با بودجه یا نیاز دیگری دوباره جستجو کنید."
    await message.answer(f"🧠 <b>پیشنهاد بر اساس نیاز شما</b>\n\n{escape(intro)}\n\n{escape(count)}", reply_markup=keyboard(rows))


@router.callback_query(F.data == "ai:apis")
async def api_categories(callback: CallbackQuery) -> None:
    await record_ai_visit(callback.from_user.id, "ai_api_catalog")
    await callback.answer()
    rows = [[("🛍 اشتراک‌های پرمیوم هوش مصنوعی", "premium:home")]]
    rows += [[(f"✨ {details[0]}", f"ai:subscription:{key}")] for key, details in OFFICIAL_SUBSCRIPTIONS.items()]
    rows.append([("🔙 بازگشت به هوش مصنوعی ai", "ai:home")])
    await callback.message.answer("✨ <b>سرویس‌های هوش مصنوعی</b>\nاشتراک موردنظر را انتخاب کنید:", reply_markup=keyboard(rows))


@router.callback_query(F.data.startswith("ai:subscription:"))
async def official_subscription(callback: CallbackQuery) -> None:
    key = callback.data.rsplit(":", 1)[-1]
    details = OFFICIAL_SUBSCRIPTIONS.get(key)
    if details is None:
        await callback.answer("سرویس نامعتبر است.", show_alert=True)
        return
    name, price, source, description = details
    await callback.answer()
    await callback.message.answer(
        f"✨ <b>{escape(name)}</b>\n{escape(description)}\n\n"
        f"قیمت رسمی آمریکا: <b>{escape(price)}</b>\n"
        f'<a href="{escape(source, quote=True)}">منبع رسمی قیمت</a>\n\n'
        "این مبلغ برای اطلاع از قیمت رسمی است؛ قیمت و موجودی فروش راینوتک را در «اشتراک‌های پرمیوم هوش مصنوعی» ببینید.\n"
        "برای اشتراک‌های موجود، از راینوتک با پرداخت ریالی خرید کنید.",
        reply_markup=keyboard([[("🛍 اشتراک‌های پرمیوم هوش مصنوعی", "premium:home")],
                               [("🔙 سرویس‌های هوش مصنوعی", "ai:apis")]]),
    )


@router.callback_query(F.data.startswith("ai:api_group:"))
async def api_group(callback: CallbackQuery) -> None:
    key = callback.data.rsplit(":", 1)[-1]
    group = API_GROUPS.get(key)
    if group is None:
        await callback.answer("دسته نامعتبر است.", show_alert=True)
        return
    await callback.answer()
    rows = [[(API_PRODUCTS[p][0], f"ai:api:{p}")] for p in group[1]]
    rows.append([("🔙 دسته‌های API", "ai:apis")])
    await callback.message.answer(f"🧩 <b>{escape(group[0])}</b>\nسرویس دلخواه را انتخاب کنید:", reply_markup=keyboard(rows))


@router.callback_query(F.data.startswith("ai:api:"))
async def api_product(callback: CallbackQuery) -> None:
    key = callback.data.rsplit(":", 1)[-1]
    product = API_PRODUCTS.get(key)
    if product is None:
        await callback.answer("سرویس نامعتبر است.", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(
        f"🧩 <b>{escape(product[0])}</b>\n{escape(product[1])}\n\n"
        "قیمت و نوع دسترسی پس از بررسی نیاز و ظرفیت سرویس اعلام می‌شود. تا پیش از اعلام مبلغ و تأیید سفارش، پرداختی دریافت نمی‌شود.",
        reply_markup=keyboard([[("🛒 درخواست خرید و استعلام", f"ai:api_request:{key}")], [("🔙 دسته‌های API", "ai:apis")]]),
    )


@router.callback_query(F.data.startswith("ai:api_request:"))
async def api_request(callback: CallbackQuery) -> None:
    key = callback.data.rsplit(":", 1)[-1]
    if key not in API_PRODUCTS:
        await callback.answer("سرویس نامعتبر است.", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        existing = await session.scalar(select(AiApiInquiry).where(
            AiApiInquiry.customer_telegram_id == callback.from_user.id,
            AiApiInquiry.provider_key == key,
            AiApiInquiry.status == "awaiting_quote",
        ).order_by(AiApiInquiry.id.desc()).limit(1))
        if existing is None:
            existing = AiApiInquiry(customer_telegram_id=callback.from_user.id, provider_key=key)
            session.add(existing)
            await session.commit()
    await callback.answer()
    development = key == "ai_development"
    await callback.message.answer(
        f"✅ درخواست <b>{escape(API_PRODUCTS[key][0])}</b> با شناسه <code>#{existing.id}</code> ثبت شد. "
        + ("پس از بررسی نیاز، برای هماهنگی پروژه با شما تماس گرفته می‌شود." if development
           else "پس از تعیین نوع دسترسی و قیمت، ادامه پرداخت به شما اعلام می‌شود."),
        reply_markup=keyboard([[(("🔙 بازگشت به تحلیل سیستم" if development else "🔙 سرویس‌های هوش مصنوعی"),
                                ("ai:assistant" if development else "ai:apis"))]]),
    )

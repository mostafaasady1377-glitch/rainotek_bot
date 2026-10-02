from __future__ import annotations

import re
from html import escape
from typing import Any, Optional

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Message
from loguru import logger
from sqlalchemy import func, or_, select
from sqlalchemy.orm import joinedload

from bot.keyboards.catalog_builder import (
    PAGE_SIZE,
    branches_menu_keyboard,
    laptop_detail_keyboard,
    paginated_catalog_keyboard,
    smart_search_menu_keyboard,
)
from bot.config import get_settings
from bot.services.ai_search_service import AISearchService
from bot.services.ai_feature_visits import record_ai_visit
from bot.services.branch_service import BranchService, RHINOTECH_BRANCHES
from bot.services.branch_presentation import branch_details
from bot.services.telegram_entities import branch_message_entities
from bot.services.product_condition import normalize_condition, condition_info
from bot.services.laptop_assets import get_laptop_photo_input, get_brand_photo_input
from database.models import (
    Branch,
    BranchInventory,
    BranchStock,
    Laptop,
    LaptopBrand,
    LaptopModel,
    LaptopSeries,
    LaptopVariant,
    ProductImage,
)
from database.session import AsyncSessionLocal

router = Router()


def format_price(tomans: int | None) -> str:
    if not tomans or tomans <= 0:
        return "تماس بگیرید"
    return f"{int(tomans):,} تومان"


# =========================================================================
# 1. کاتالوگ درختی برندها و مدل‌ها
# =========================================================================

async def show_brands_menu(message: Message, *, edit: bool = False) -> None:
    async with AsyncSessionLocal() as session:
        # دریافت برندهایی که حداقل یک لپ‌تاپ فعال دارند
        stmt = (
            select(LaptopBrand.id, LaptopBrand.name, func.count(Laptop.id))
            .join(Laptop, Laptop.brand_id == LaptopBrand.id)
            .where(Laptop.status == "active")
            .group_by(LaptopBrand.id, LaptopBrand.name)
            .order_by(func.count(Laptop.id).desc())
        )
        results = (await session.execute(stmt)).all()

    if not results:
        text = "در حال حاضر کالایی در کاتالوگ ثبت نشده است."
        if edit:
            await message.edit_text(text)
        else:
            await message.answer(text)
        return

    brand_icons = {
        "apple": "🍏",
        "iphone": "📱",
        "microsoft": "💻",
        "asus": "💻",
        "lenovo": "💻",
        "hp": "💻",
        "dell": "💻",
        "acer": "💻",
        "msi": "💻",
        "toshiba": "💻",
        "sony": "💻",
        "nec": "💻",
        "fujitsu": "💻",
        "samsung": "💻",
        "huawei": "💻",
        "case": "🖥",
        "monitor": "🖥",
    }

    buttons = []
    for b_id, b_name, count in results:
        icon = "💻"
        b_lower = b_name.lower()
        for k, ic in brand_icons.items():
            if k in b_lower:
                icon = ic
                break
        buttons.append([
            InlineKeyboardButton(
                text=f"{icon} {b_name} ({count} مدل موجود)",
                callback_data=f"tree:brand:{b_id}",
            )
        ])


    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    text = (
        "💻 <b>کاتالوگ تخصصی فروشگاه راینوتک (RAINOTEK):</b>\n\n"
        "بزرگترین مرجع لپ‌تاپ‌های نو، اپن‌باکس و استوک اروپایی گرید A++ در تهران.\n"
        "لطفاً برند مورد نظر خود را جهت مرور مدل‌ها و مشخصات انتخاب کنید:"
    )

    if edit:
        if message.photo:
            try:
                await message.delete()
            except Exception:
                pass
            await message.answer(text, reply_markup=keyboard)
        else:
            await message.edit_text(text, reply_markup=keyboard)
    else:
        await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data == "tree:brands_root")
@router.callback_query(F.data == "tree:back")
async def callback_brands_root(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    await show_brands_menu(callback.message, edit=True)


async def render_brand_laptops(
    message: Message,
    brand_id: int,
    page: int = 0,
    *,
    edit: bool = True,
) -> None:
    async with AsyncSessionLocal() as session:
        brand = await session.get(LaptopBrand, brand_id)
        if not brand:
            await message.answer("برند مورد نظر پیدا نشد.")
            return

        stmt = (
            select(Laptop)
            .where(Laptop.brand_id == brand_id, Laptop.status == "active")
            .order_by(Laptop.price.asc())
        )
        laptops = list((await session.scalars(stmt)).all())

    total = len(laptops)
    safe_page = max(page, 0)
    page_laptops = laptops[safe_page * PAGE_SIZE : (safe_page + 1) * PAGE_SIZE]
    page_count = max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1)

    buttons = []
    for lap in page_laptops:
        p_str = format_price(lap.price)
        btn_text = f"{lap.model} | {lap.cpu or '-'} | {lap.ram or '-'} - {p_str}"
        buttons.append([
            InlineKeyboardButton(
                text=btn_text[:60],
                callback_data=f"tree:laptop:{lap.id}",
            )
        ])

    nav_row = []
    if safe_page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ قبلی", callback_data=f"tree:bpage:{brand_id}:{safe_page - 1}"))
    nav_row.append(InlineKeyboardButton(text=f"{safe_page + 1} از {page_count}", callback_data="tree:noop"))
    if safe_page + 1 < page_count:
        nav_row.append(InlineKeyboardButton(text="بعدی ➡️", callback_data=f"tree:bpage:{brand_id}:{safe_page + 1}"))

    if page_count > 1:
        buttons.append(nav_row)

    buttons.append([
        InlineKeyboardButton(text="🔙 بازگشت به لیست برندها", callback_data="tree:brands_root"),
    ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    text = (
        f"💻 <b>مدل‌های موجود برند {escape(brand.name)}:</b>\n"
        f"تعداد کل گزینه‌ها: <b>{total}</b> کالا\n\n"
        f"برای مشاهده مشخصات فنی کامل، تصاویر و موجودی شعب، روی مدل دلخواه کلیک کنید:"
    )

    if edit:
        if message.photo:
            try:
                await message.delete()
            except Exception:
                pass
            await message.answer(text, reply_markup=keyboard)
        else:
            await message.edit_text(text, reply_markup=keyboard)
    else:
        await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("tree:brand:"))
async def callback_choose_brand(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        brand_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("شناسه برند نامعتبر است.", show_alert=True)
        return

    await state.update_data(current_brand_id=brand_id)
    await callback.answer()
    await render_brand_laptops(callback.message, brand_id, page=0, edit=True)


@router.callback_query(F.data.startswith("tree:bpage:"))
async def callback_brand_page(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    brand_id = int(parts[2])
    page = int(parts[3])
    await callback.answer()
    await render_brand_laptops(callback.message, brand_id, page=page, edit=True)


@router.callback_query(F.data == "tree:noop")
async def callback_tree_noop(callback: CallbackQuery) -> None:
    await callback.answer()


# =========================================================================
# 2. نمایش کارت کامل محصول (عکس، مشخصات، قیمت و موجودی شعب)
# =========================================================================

def gallery_keyboard(laptop_id: int, index: int, total: int) -> InlineKeyboardMarkup | None:
    if total < 2:
        return None
    row = []
    if index > 0:
        row.append(InlineKeyboardButton(text="⬅️ قبلی", callback_data=f"gallery:{laptop_id}:{index - 1}"))
    if index + 1 < total:
        row.append(InlineKeyboardButton(text="بعدی ➡️", callback_data=f"gallery:{laptop_id}:{index + 1}"))
    return InlineKeyboardMarkup(inline_keyboard=[row])


async def product_gallery_sources(session, laptop: Laptop) -> list[str]:
    images = list((await session.scalars(
        select(ProductImage).where(ProductImage.laptop_id == laptop.id)
        .order_by(ProductImage.is_primary.desc(), ProductImage.display_order, ProductImage.id)
    )).all())
    real = [image.telegram_file_id or image.image_url.removeprefix("tgfile:") for image in images
            if image.image_url.startswith("tgfile:")]
    if real:
        return real
    catalog = [image.image_url for image in images if image.image_url.startswith(("http://", "https://"))]
    if catalog:
        return catalog
    return [laptop.image_url.removeprefix("tgfile:")] if laptop.image_url else []


async def show_laptop_card(message: Message, laptop_id: int, *, callback: CallbackQuery | None = None, from_ai: bool = False) -> None:
    async with AsyncSessionLocal() as session:
        statement = (
            select(Laptop)
            .options(
                joinedload(Laptop.brand),
                joinedload(Laptop.inventory_rows).joinedload(BranchInventory.branch),
            )
            .where(Laptop.id == laptop_id)
        )
        laptop = await session.scalar(statement)

        if not laptop:
            if callback:
                await callback.answer("کالای مورد نظر یافت نشد.", show_alert=True)
            return

        brand_name = laptop.brand.name if laptop.brand else "راینوتک"
        gallery_sources = await product_gallery_sources(session, laptop)

        # بررسی و لیست موجودی شعب
        branch_lines = []
        total_in_branches = 0
        for inv in laptop.inventory_rows:
            if inv.branch and inv.branch.is_active and inv.quantity > 0:
                total_in_branches += inv.quantity
                branch_lines.append(branch_details(inv.branch.name, inv.branch.address, inv.branch.phone, inv.branch.latitude, inv.branch.longitude, f"{inv.quantity - inv.reserved_count} عدد قابل فروش"))


    card_text = (
        f"💻 <b>{escape(brand_name)} {escape(laptop.model)}</b>\n\n"
        f"🏷 <b>وضعیت:</b> {escape(condition_info(laptop)[1])}\n"
        f"🛡 <b>گارانتی:</b> {escape(get_settings().STORE_WARRANTY)}\n\n"
        f"⚙️ <b>مشخصات فنی دستگاه:</b>\n"
        f"• پردازنده (CPU): <code>{escape(laptop.cpu or '-')}</code>\n"
        f"• حافظه رم (RAM): <code>{escape(laptop.ram or '-')}</code>\n"
        f"• حافظه ذخیره‌سازی: <code>{escape(laptop.storage or '-')}</code>\n"
        f"• گرافیک (GPU): <code>{escape(laptop.gpu or 'آنبورد')}</code>\n"
        f"• صفحه نمایش: <code>{escape(laptop.screen_size or '-')}</code>\n\n"
        f"💰 <b>قیمت نقدی:</b> <b>{format_price(laptop.price)}</b>\n\n"
        f"🏢 <b>وضعیت موجودی در شعب راینوتک:</b>\n"
    )

    if branch_lines:
        card_text += "\n".join(branch_lines) + "\n\n"
        card_text += "🚚 <i>امکان ارسال فوری به سراسر کشور یا تحویل حضوری در شعب فوق</i>"
    else:
        card_text += "⚠️ <i>در حال حاضر در شعب فیزیکی ناموجود است (قابل سفارش و رزرو شرکتی).</i>"

    keyboard = laptop_detail_keyboard(
        laptop_id=laptop.id,
        brand_id=laptop.brand_id,
        from_ai=from_ai,
    )

    photo_input = (gallery_sources[0] if gallery_sources else
                   get_laptop_photo_input(brand_name, laptop.model, custom_url=laptop.image_url, cpu=laptop.cpu or '', screen=laptop.screen_size or ''))

    brand_photo = photo_input is None
    if brand_photo:
        photo_input = get_brand_photo_input(brand_name)
    if callback:
        await callback.answer()
    if photo_input is not None:
        try:
            await message.answer_photo(photo=photo_input, caption=(f"📷 <b>تصویر نمونهٔ لپ‌تاپ / گروه {escape(brand_name)}</b>\nاین تصویر، عکس دستگاه انتخاب‌شده نیست." if brand_photo else f"📷 <b>{'تصویر واقعی' if gallery_sources and laptop.image_url and laptop.image_url.startswith('tgfile:') else 'تصویر کاتالوگی'} {escape(brand_name)} {escape(laptop.model)}</b> · ۱ از {max(len(gallery_sources), 1)}"), reply_markup=gallery_keyboard(laptop_id, 0, len(gallery_sources)))
        except Exception as exc:
            logger.warning('Product photo unavailable for laptop {}: {}', laptop_id, type(exc).__name__)
            card_text += '\n\n📷 تصویر فعلاً قابل ارسال نیست.'
    else:
        card_text += '\n\n📷 عکس دقیق این مدل هنوز ثبت نشده است.'
    card_text, card_entities = branch_message_entities(card_text)
    await message.answer(card_text, entities=card_entities, parse_mode=None, reply_markup=keyboard)


@router.callback_query(F.data.startswith("tree:laptop:"))
async def callback_show_laptop(callback: CallbackQuery) -> None:
    try:
        laptop_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("شناسه محصول نامعتبر است.", show_alert=True)
        return
    await show_laptop_card(callback.message, laptop_id, callback=callback)


@router.callback_query(F.data.startswith("ai:laptop:"))
async def callback_show_ai_laptop(callback: CallbackQuery) -> None:
    try:
        laptop_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer("شناسه محصول نامعتبر است.", show_alert=True)
        return
    await show_laptop_card(callback.message, laptop_id, callback=callback, from_ai=True)


@router.callback_query(F.data.startswith("gallery:"))
async def callback_gallery(callback: CallbackQuery) -> None:
    try:
        _, laptop_id_text, index_text = callback.data.split(":")
        laptop_id, index = int(laptop_id_text), int(index_text)
    except (ValueError, AttributeError):
        await callback.answer("تصویر نامعتبر است.", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        laptop = await session.get(Laptop, laptop_id)
        if laptop is None:
            await callback.answer("محصول یافت نشد.", show_alert=True)
            return
        sources = await product_gallery_sources(session, laptop)
    if not 0 <= index < len(sources):
        await callback.answer("تصویر یافت نشد.", show_alert=True)
        return
    await callback.message.edit_media(
        InputMediaPhoto(media=sources[index], caption=f"📷 تصویر {index + 1} از {len(sources)}"),
        reply_markup=gallery_keyboard(laptop_id, index, len(sources)),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tree:variant:"))
async def callback_show_variant(callback: CallbackQuery) -> None:
    try:
        variant_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("شناسه نامعتبر است.", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        variant = await session.get(LaptopVariant, variant_id)
        laptop_id = variant.legacy_laptop_id if variant else None

    if laptop_id:
        await show_laptop_card(callback.message, laptop_id, callback=callback)
    else:
        await callback.answer("محصول متناظر یافت نشد.", show_alert=True)


@router.callback_query(F.data.startswith("branches:laptop:"))
async def callback_show_laptop_branches(callback: CallbackQuery) -> None:
    laptop_id = int(callback.data.split(":")[2])
    async with AsyncSessionLocal() as session:
        laptop = await session.get(Laptop, laptop_id)
        if not laptop:
            await callback.answer("کالا یافت نشد.", show_alert=True)
            return

        inv_rows = (await session.execute(
            select(BranchInventory, Branch)
            .join(Branch, Branch.id == BranchInventory.branch_id)
            .where(BranchInventory.laptop_id == laptop_id, BranchInventory.quantity > 0, Branch.is_active == True)
        )).all()

    if not inv_rows:
        await callback.answer("این مدل در حال حاضر در شعب فیزیکی موجود نیست.", show_alert=True)
        return

    lines = [f"🏢 <b>شعب دارای موجودی لپ‌تاپ {escape(laptop.model)}:</b>\n"]
    for inv, branch in inv_rows:
        lines.append(branch_details(branch.name, branch.address, branch.phone, branch.latitude, branch.longitude, f"{inv.quantity - inv.reserved_count} عدد قابل فروش"))
        lines.append("\n────────────\n")

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛍 ثبت درخواست خرید", callback_data=f"order:laptop:{laptop_id}")],
        [InlineKeyboardButton(text="↩️ بازگشت به مشخصات کالا", callback_data=f"tree:laptop:{laptop_id}")],
    ])
    text, entities = branch_message_entities("\n".join(lines))
    await callback.message.answer(text, entities=entities, parse_mode=None, reply_markup=keyboard, disable_web_page_preview=True)
    await callback.answer()


@router.callback_query(F.data.startswith("photos:laptop:"))
async def callback_show_laptop_photos(callback: CallbackQuery) -> None:
    laptop_id = int(callback.data.split(":")[2])
    async with AsyncSessionLocal() as session:
        laptop = await session.get(Laptop, laptop_id)
        if not laptop:
            await callback.answer("کالا یافت نشد.", show_alert=True)
            return
        brand_name = laptop.brand.name if laptop.brand else "راینوتک"

    photo = get_laptop_photo_input(brand_name, laptop.model, custom_url=laptop.image_url)
    try:
        await callback.message.answer_photo(
            photo=photo,
            caption=f"📸 <b>تصویر کاتالوگ {escape(brand_name)} {escape(laptop.model)}</b>\nکلیه محصولات قبل از ارسال از نظر فنی تست و تأیید می‌شوند.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="↩️ بازگشت به مشخصات دستگاه", callback_data=f"tree:laptop:{laptop_id}")],
            ]),
        )
        await callback.answer()
    except Exception as exc:
        await callback.answer(f"تصویر در پیام اصلی نمایش داده شده است.", show_alert=True)


# =========================================================================
# 3. بخش شعب راینوتک و موجودی اختصاصی هر شعبه
# =========================================================================

async def render_branches_overview(message: Message, *, edit: bool = False) -> None:
    async with AsyncSessionLocal() as session:
        # اطمینان از وجود ۵ شعبه
        await BranchService.ensure_canonical_branches(session)
        branches = list((await session.scalars(
            select(Branch).where(Branch.is_active == True).order_by(Branch.id)
        )).all())

        branches_data = []
        for b in branches:
            cnt = await session.scalar(
                select(func.sum(BranchInventory.quantity)).where(BranchInventory.branch_id == b.id)
            ) or 0
            branches_data.append({
                "id": b.id,
                "name": b.name,
                "phone": b.phone,
                "address": b.address,
                "address_full": b.address_full,
                "stock_count": cnt,
                "latitude": b.latitude,
                "longitude": b.longitude,
            })

    lines = [
        "🏢 <b>شعب فعال فروشگاه تخصصی راینوتک (RAINOTEK):</b>\n",
        "مجموعه راینوتک با ۵ شعبه مجهز در تهران آماده تحویل حضوری، تست رایگان و مشاوره تخصصی می‌باشد:\n",
    ]

    for b in branches_data:
        lines.append(branch_details(b['name'], b['address'], b['phone'], b['latitude'], b['longitude'], f"{b['stock_count']} دستگاه موجود"))
        lines.append("\n────────────\n")

    lines.append("🕒 <b>ساعات کاری:</b>")
    lines.append("• <b>شعبه میرداماد:</b> همه‌روزه (حتی جمعه‌ها و ایام تعطیل) از ساعت ۱۰:۰۰ الی ۲۲:۰۰")
    lines.append("• <b>سایر شعب:</b> شنبه تا پنج‌شنبه از ساعت ۱۰:۰۰ الی ۲۱:۰۰\n")
    lines.append("👇 <i>جهت مشاهده لیست کامل لپ‌تاپ‌های موجود در هر شعبه، دکمه زیر را لمس کنید:</i>")

    keyboard = branches_menu_keyboard(branches_data)
    text, entities = branch_message_entities("\n".join(lines))

    if edit:
        if message.photo:
            try:
                await message.delete()
            except Exception:
                pass
            await message.answer(text, entities=entities, parse_mode=None, reply_markup=keyboard, disable_web_page_preview=True)
        else:
            await message.edit_text(text, entities=entities, parse_mode=None, reply_markup=keyboard, disable_web_page_preview=True)
    else:
        await message.answer(text, entities=entities, parse_mode=None, reply_markup=keyboard, disable_web_page_preview=True)


@router.message(F.text == "🏢 شعب راینوتک")
@router.callback_query(F.data == "branch:overview")
async def show_branches_info(event: Message | CallbackQuery) -> None:
    if isinstance(event, CallbackQuery):
        await event.answer()
        await render_branches_overview(event.message, edit=True)
    else:
        await render_branches_overview(event, edit=False)


@router.callback_query(F.data.startswith("branch_stock:"))
async def show_branch_inventory(callback: CallbackQuery) -> None:
    try:
        branch_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("شناسه شعبه نامعتبر است.", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        data = await BranchService.get_branch_inventory_summary(session, branch_id)

    branch = data["branch"]
    if not branch:
        await callback.answer("شعبه یافت نشد.", show_alert=True)
        return

    total_items = data["total_items"]
    brands = data["brands"]

    lines = [
        f"📦 <b>موجودی لپ‌تاپ‌های {escape(branch.name)}:</b>\n",
        branch_details(branch.name, branch.address, branch.phone, branch.latitude, branch.longitude),
        f"تعداد کل دستگاه‌های موجود در این شعبه: <b>{total_items} دستگاه</b>\n",
        "تفکیک برندهای موجود در این شعبه:",
    ]

    for b_name, b_count in sorted(brands.items(), key=lambda x: -x[1]):
        lines.append(f"  • {escape(b_name)}: <b>{b_count} دستگاه</b>")

    lines.append("\nجهت مشاهده اقلام یا رزرو حضوری از دکمه‌های زیر استفاده کنید:")

    buttons = []
    # دکمه‌های برندهای موجود در این شعبه
    brand_row = []
    for b_name, b_count in sorted(brands.items(), key=lambda x: -x[1])[:8]:
        brand_row.append(
            InlineKeyboardButton(
                text=f"{b_name} ({b_count})",
                callback_data=f"br_filter:{branch.id}:{b_name}",
            )
        )
        if len(brand_row) == 2:
            buttons.append(brand_row)
            brand_row = []
    if brand_row:
        buttons.append(brand_row)

    buttons.append([
        InlineKeyboardButton(text="📋 مشاهده ۱۰ محصول برگزیده این شعبه", callback_data=f"br_all:{branch.id}:0"),
    ])
    buttons.append([
        InlineKeyboardButton(text="🔙 بازگشت به لیست شعب", callback_data="branch:overview"),
    ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    text, entities = branch_message_entities("\n".join(lines))
    await callback.message.edit_text(text, entities=entities, parse_mode=None, reply_markup=keyboard, disable_web_page_preview=True)
    await callback.answer()


@router.callback_query(F.data.startswith("br_filter:"))
async def show_branch_brand_items(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    branch_id = int(parts[1])
    brand_name = parts[2]

    async with AsyncSessionLocal() as session:
        branch = await session.get(Branch, branch_id)
        stmt = (
            select(Laptop, BranchInventory.quantity)
            .join(BranchInventory, BranchInventory.laptop_id == Laptop.id)
            .join(LaptopBrand, LaptopBrand.id == Laptop.brand_id)
            .where(
                BranchInventory.branch_id == branch_id,
                BranchInventory.quantity > 0,
                LaptopBrand.name.ilike(f"%{brand_name}%"),
            )
            .order_by(Laptop.price.asc())
        )
        results = (await session.execute(stmt)).all()

    if not results:
        await callback.answer(f"کالایی از برند {brand_name} در این شعبه موجود نیست.", show_alert=True)
        return

    buttons = []
    for lap, qty in results:
        btn_title = f"{lap.model} | {lap.cpu or ''} - {format_price(lap.price)}"
        buttons.append([
            InlineKeyboardButton(text=btn_title[:60], callback_data=f"tree:laptop:{lap.id}")
        ])

    buttons.append([
        InlineKeyboardButton(text="🔙 بازگشت به شعبه", callback_data=f"branch_stock:{branch_id}"),
        InlineKeyboardButton(text="🏢 کل شعب", callback_data="branch:overview"),
    ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    text = (
        f"📦 <b>لپ‌تاپ‌های {escape(brand_name)} موجود در {escape(branch.name)}:</b>\n"
        f"تعداد: <b>{len(results)}</b> مدل\n\n"
        f"جهت مشاهده جزییات و رزرو حضوری در این شعبه کلیک فرمایید:"
    )
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("br_all:"))
async def show_branch_all_items(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    branch_id = int(parts[1])
    page = int(parts[2])

    async with AsyncSessionLocal() as session:
        branch = await session.get(Branch, branch_id)
        stmt = (
            select(Laptop, BranchInventory.quantity, LaptopBrand.name.label("bname"))
            .join(BranchInventory, BranchInventory.laptop_id == Laptop.id)
            .join(LaptopBrand, LaptopBrand.id == Laptop.brand_id)
            .where(BranchInventory.branch_id == branch_id, BranchInventory.quantity > 0)
            .order_by(Laptop.price.asc())
        )
        results = (await session.execute(stmt)).all()

    total = len(results)
    page_items = results[page * 8 : (page + 1) * 8]
    page_count = max((total + 7) // 8, 1)

    buttons = []
    for lap, qty, bname in page_items:
        btn_title = f"{bname} {lap.model} - {format_price(lap.price)}"
        buttons.append([
            InlineKeyboardButton(text=btn_title[:60], callback_data=f"tree:laptop:{lap.id}")
        ])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ قبلی", callback_data=f"br_all:{branch_id}:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{page_count}", callback_data="tree:noop"))
    if page + 1 < page_count:
        nav.append(InlineKeyboardButton(text="بعدی ➡️", callback_data=f"br_all:{branch_id}:{page + 1}"))
    if page_count > 1:
        buttons.append(nav)

    buttons.append([
        InlineKeyboardButton(text="🔙 بازگشت به شعبه", callback_data=f"branch_stock:{branch_id}"),
    ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    text = (
        f"📦 <b>کلیه اقلام موجود در {escape(branch.name)}:</b>\n"
        f"تعداد کل: <b>{total}</b> کالا\n\n"
        f"برای مشخصات فنی و رزرو کالا، مدل را لمس کنید:"
    )
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


# =========================================================================
# 4. تفکیک نو و استوک
# =========================================================================

@router.message(F.text == "🏷 لپ‌تاپ‌های نو و استوک")
async def show_condition_menu(message: Message) -> None:
    buttons = [
        [InlineKeyboardButton(text="✨ لپ‌تاپ‌های نو (آکبند شرکتی با گارانتی)", callback_data="cond_filter:نو")],
        [InlineKeyboardButton(text="🔄 لپ‌تاپ‌های استوک اروپایی (Grade A+ تمیز)", callback_data="cond_filter:استوک")],
        [InlineKeyboardButton(text="🔙 بازگشت به منوی اصلی", callback_data="tree:brands_root")],
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(
        "🏷 <b>انتخاب وضعیت دستگاه:</b>\n\n"
        "فروشگاه راینوتک هر دو ردهٔ لپ‌تاپ‌های آکبند روز بازار و دستگاه‌های استوک اروپایی گرید A++ را همراه با مهلت تست و گارانتی رسمی ارائه می‌دهد.\n"
        "دسته‌بندی مورد نظر خود را انتخاب کنید:",
        reply_markup=keyboard,
    )


@router.callback_query(F.data.startswith("cond_filter:"))
async def filter_by_condition(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    cond_query = parts[1]
    page = max(0, int(parts[2])) if len(parts) > 2 and parts[2].isdigit() else 0
    async with AsyncSessionLocal() as session:
        statement = (
            select(Laptop)
            .options(joinedload(Laptop.brand))
            .where(
                Laptop.status == "active",
            )
            .order_by(Laptop.price.asc())
        )
        laptops = [lap for lap in (await session.scalars(statement)).all() if condition_info(lap)[0] == cond_query]

    if not laptops:
        await callback.message.edit_text(f"🏷 کالایی با وضعیت {escape(cond_query)} در شیت ثبت نشده است. وضعیت نامشخص به نو یا استوک تبدیل نمی‌شود.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 بازگشت", callback_data="cond:back")]]))
        await callback.answer()
        return

    buttons = [
        [InlineKeyboardButton(
            text=f"{lap.brand.name if lap.brand else ''} {lap.model} ({format_price(lap.price)})"[:60],
            callback_data=f"tree:laptop:{lap.id}",
        )]
        for lap in laptops[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    ]
    navigation = []
    if page > 0:
        navigation.append(InlineKeyboardButton(text="⬅️ قبلی", callback_data=f"cond_filter:{cond_query}:{page - 1}"))
    if (page + 1) * PAGE_SIZE < len(laptops):
        navigation.append(InlineKeyboardButton(text="بعدی ➡️", callback_data=f"cond_filter:{cond_query}:{page + 1}"))
    if navigation:
        buttons.append(navigation)
    buttons.append([
        InlineKeyboardButton(text="🔙 بازگشت به انتخاب وضعیت", callback_data="cond:back"),
        InlineKeyboardButton(text="💻 کاتالوگ کامل", callback_data="tree:brands_root"),
    ])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text(
        f"🏷 <b>دستگاه‌های {escape(cond_query)} راینوتک:</b>\nبرای مشخصات کامل، تصاویر و موجودی شعب کلیک کنید:",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(F.data == "cond:back")
async def callback_cond_back(callback: CallbackQuery) -> None:
    buttons = [
        [InlineKeyboardButton(text="✨ لپ‌تاپ‌های نو (آکبند شرکتی با گارانتی)", callback_data="cond_filter:نو")],
        [InlineKeyboardButton(text="🔄 لپ‌تاپ‌های استوک اروپایی (Grade A+ تمیز)", callback_data="cond_filter:استوک")],
        [InlineKeyboardButton(text="🔙 بازگشت به کاتالوگ", callback_data="tree:brands_root")],
    ]
    await callback.message.edit_text("🏷 <b>انتخاب وضعیت دستگاه:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


# =========================================================================
# 5. جستجوی هوشمند و دستیار خرید
# =========================================================================

@router.message(F.text == "🔎 جستجو")
@router.message(F.text == "🔎 جستجوی هوشمند")
@router.callback_query(F.data == "smart_search:menu")
async def open_smart_search_menu(event: Message | CallbackQuery) -> None:
    if getattr(event, "from_user", None):
        await record_ai_visit(event.from_user.id, "smart_search")
    text = (
        "🤖 <b>به دستیار هوشمند و جستجوی تخصصی راینوتک خوش آمدید!</b>\n\n"
        "شما می‌توانید به روش‌های زیر لپ‌تاپ دلخواه خود را بیابید:\n\n"
        "1️⃣ <b>تایپ آزاد متن:</b> هر مدل، کانفیگ یا بودجه‌ای مد نظر دارید تایپ و ارسال کنید؛ مثلاً:\n"
        "   • <i>«لپ‌تاپ تا ۴۰ میلیون برای برنامه‌نویسی»</i>\n"
        "   • <i>«سرفیس i7 رم ۱۶»</i>\n"
        "   • <i>«مک‌بوک پرو استوک»</i>\n"
        "   • <i>«ایسوس گیمینگ rtx 4060»</i>\n"
        "   • <i>«لپ‌تاپ سبک لمسی»</i>\n\n"
        "2️⃣ <b>ارسال ویس (Voice):</b> صدای خود را بفرستید و بگویید چه سیستمی می‌خواهید!\n\n"
        "3️⃣ <b>فیلتر سریع:</b> از دکمه‌های پرکاربرد زیر انتخاب فرمایید:"
    )
    keyboard = smart_search_menu_keyboard()

    if isinstance(event, CallbackQuery):
        await event.answer()
        if event.message.photo:
            try:
                await event.message.delete()
            except Exception:
                pass
            await event.message.answer(text, reply_markup=keyboard)
        else:
            await event.message.edit_text(text, reply_markup=keyboard)
    else:
        await event.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("quick_search:"))
async def handle_quick_search(callback: CallbackQuery) -> None:
    preset = callback.data.split(":")[1]
    presets_map = {
        "under30": ("تا ۳۰ میلیون تومان", "تا ۳۰ میلیون"),
        "30to60": ("۳۰ تا ۶۰ میلیون تومان", "۳۰ تا ۶۰ میلیون"),
        "60to100": ("۶۰ تا ۱۰۰ میلیون تومان", "۶۰ تا ۱۰۰ میلیون"),
        "above100": ("بالای ۱۰۰ میلیون تومان", "بالای ۱۰۰ میلیون"),
        "gaming": ("لپ‌تاپ‌های گیمینگ با گرافیک مجزا", "لپ‌تاپ گیمینگ"),
        "surface": ("سرفیس و لپ‌تاپ‌های لمسی تبلت‌شو", "سرفیس لمسی"),
        "apple": ("محصولات اپل (مک‌بوک و آیفون)", "مک‌بوک اپل"),
        "thinkpad": ("لپ‌تاپ‌های لنوو تینک‌پد صنعتی", "لنوو thinkpad"),
    }

    title, query = presets_map.get(preset, ("جستجو", "لپ‌تاپ"))

    async with AsyncSessionLocal() as session:
        results = await AISearchService.smart_search(session, query, limit=8)

    if not results:
        await callback.answer("موردی با این مشخصات یافت نشد.", show_alert=True)
        return

    buttons = []
    for r in results:
        btn_text = f"{r['brand']} {r['model']} - {format_price(r['price'])}"
        buttons.append([
            InlineKeyboardButton(text=btn_text[:60], callback_data=f"tree:laptop:{r['id']}")
        ])

    buttons.append([
        InlineKeyboardButton(text="🔍 جستجوی دیگر", callback_data="smart_search:menu"),
        InlineKeyboardButton(text="🔙 بازگشت به کاتالوگ", callback_data="tree:brands_root"),
    ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text(
        f"🤖 <b>پیشنهادات هوشمند راینوتک ({escape(title)}):</b>\n"
        f"تعداد {len(results)} گزینه مناسب پیدا شد:\n\n"
        f"جهت بررسی مشخصات فنی کامل، عکس و موجودی در شعب، روی مدل کلیک کنید:",
        reply_markup=keyboard,
    )
    await callback.answer()


# رسیدگی به ورودی‌های متنی آزاد برای جستجوی هوشمند
@router.message(F.text & ~F.text.startswith("/"))
async def handle_free_text_query(message: Message, state: FSMContext) -> None:
    current_state = await state.get_state()
    if current_state:
        # اگر کاربر در وضعیت FSM فرم (مثل ثبت نام یا شماره) است مداخله نکن
        raise SkipHandler

    query = (message.text or "").strip()
    from bot.keyboards.reply_menus import main_menu_kb
    if any(query == button.text for role in ("customer", "admin", "branch_manager") for row in main_menu_kb(role).keyboard for button in row):
        raise SkipHandler
    # اگر پیام کوتاه یا دکمه‌های پیش‌فرض بود نادیده بگیر
    if len(query) < 2 or query in ["💻 کاتالوگ", "📦 موجودی شعب",
        "💻 کاتالوگ و مشخصات", "📦 موجودی", "🔎 جستجو", "🔎 جستجوی هوشمند",
        "🏷 لپ‌تاپ‌های نو و استوک", "🏢 شعب راینوتک", "🛒 پیگیری سفارش من",
        "ℹ️ راهنما", "ℹ️ راهنمای خرید و تماس", "🔄 همگام‌سازی شیت",
    ]:
        raise SkipHandler

    async with AsyncSessionLocal() as session:
        results = await AISearchService.smart_search(session, query, limit=8)

    if not results:
        await message.answer(
            f"🔍 موردی دقیقاً منطبق با <i>«{escape(query)}»</i> پیدا نشد.\n\n"
            f"💡 <b>پیشنهاد:</b> می‌توانید نام برند (مثلاً <code>ایسوس</code>)، نوع پردازنده (مثلاً <code>i7</code>) یا سقف بودجه (مثلاً <code>تا ۵۰ میلیون</code>) را جستجو کنید یا از کاتالوگ دیدن نمایید.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💻 مرور کاتالوگ دسته‌بندی‌شده", callback_data="tree:brands_root")],
                [InlineKeyboardButton(text="🏢 مشاهده شعب راینوتک", callback_data="branch:overview")],
            ]),
        )
        return

    buttons = []
    for r in results:
        btn_text = f"{r['brand']} {r['model']} - {format_price(r['price'])}"
        buttons.append([
            InlineKeyboardButton(text=btn_text[:60], callback_data=f"tree:laptop:{r['id']}")
        ])

    buttons.append([
        InlineKeyboardButton(text="🔍 جستجوی هوشمند جدید", callback_data="smart_search:menu"),
        InlineKeyboardButton(text="💻 مشاهده کل کاتالوگ", callback_data="tree:brands_root"),
    ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(
        f"🤖 <b>نتایج هوشمند برای جستجوی:</b> <i>«{escape(query)}»</i>\n"
        f"تعداد {len(results)} لپ‌تاپ مناسب یافت شد:\n\n"
        f"جهت مشاهده عکس، مشخصات فنی دقیق و موجودی شعب، مدل را انتخاب کنید:",
        reply_markup=keyboard,
    )


# =========================================================================
# 6. فرمان‌های کلی ورود به کاتالوگ
# =========================================================================

async def start_catalog(message: Message, state: FSMContext) -> None:
    await state.clear()
    await show_brands_menu(message, edit=False)


@router.message(F.text == "💻 کاتالوگ و مشخصات")
@router.message(F.text == "📦 موجودی")
@router.message(F.text == "/catalog")
@router.message(F.text == "/stock")
async def open_catalog(message: Message, state: FSMContext) -> None:
    await start_catalog(message, state)


@router.callback_query(F.data == "consultation:online")
async def show_online_consultation(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer("💬 مشاوره آنلاین راینوتک\nمدل یا مشخصات موردنظرتان را بنویسید یا ویس بفرستید؛ محصولات منطبق با موجودی شیت نمایش داده می‌شوند.")

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

PAGE_SIZE = 8


def paginated_catalog_keyboard(
    items: list[tuple[str, str]],
    page: int,
    total: int,
    *,
    columns: int = 1,
    back: bool = False,
    back_callback: str = "tree:back",
) -> InlineKeyboardMarkup:
    safe_page = max(page, 0)
    page_items = items[safe_page * PAGE_SIZE : (safe_page + 1) * PAGE_SIZE]
    rows = [
        [
            InlineKeyboardButton(text=label[:60], callback_data=callback_data)
            for label, callback_data in page_items[index : index + columns]
        ]
        for index in range(0, len(page_items), columns)
    ]
    page_count = max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1)
    navigation: list[InlineKeyboardButton] = []
    if page > 0:
        navigation.append(InlineKeyboardButton(text="⬅️ قبلی", callback_data=f"tree:page:{page - 1}"))
    navigation.append(InlineKeyboardButton(text=f"صفحه {page + 1} از {page_count}", callback_data="tree:noop"))
    if page + 1 < page_count:
        navigation.append(InlineKeyboardButton(text="بعدی ➡️", callback_data=f"tree:page:{page + 1}"))
    if page_count > 1:
        rows.append(navigation)
    if back:
        rows.append([InlineKeyboardButton(text="↩️ بازگشت", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def laptop_detail_keyboard(
    laptop_id: int,
    variant_id: int | None = None,
    brand_id: int | None = None,
) -> InlineKeyboardMarkup:
    v_id = variant_id or laptop_id
    rows = [
        [
            InlineKeyboardButton(text="🛍 ثبت درخواست خرید / رزرو حضوری", callback_data=f"order:laptop:{laptop_id}"),
        ],
        [
            InlineKeyboardButton(text="🏢 شعب دارای این کالا", callback_data=f"branches:laptop:{laptop_id}"),
            InlineKeyboardButton(text="📸 تصاویر بیشتر", callback_data=f"photos:laptop:{laptop_id}"),
        ],
        [
            InlineKeyboardButton(
                text="↩️ بازگشت به لیست مدل‌ها",
                callback_data=f"tree:brand:{brand_id}" if brand_id else "tree:back",
            ),
            InlineKeyboardButton(text="🏷 منوی اصلی کاتالوگ", callback_data="tree:brands_root"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def variant_detail_keyboard(variant_id: int = 0, has_photos: bool = True) -> InlineKeyboardMarkup:
    return laptop_detail_keyboard(laptop_id=variant_id, variant_id=variant_id)


def branches_menu_keyboard(branches: list[dict[str, Any]]) -> InlineKeyboardMarkup:
    rows = []
    for b in branches:
        rows.append([
            InlineKeyboardButton(
                text=f"📦 موجودی {b['name']} ({b.get('stock_count', 0)} دستگاه)",
                callback_data=f"branch_stock:{b['id']}",
            )
        ])

    rows.append([
        InlineKeyboardButton(text="🔙 بازگشت به منوی کاتالوگ", callback_data="tree:brands_root"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def smart_search_menu_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text="💰 زیر ۳۰ میلیون (اقتصادی)", callback_data="quick_search:under30"),
            InlineKeyboardButton(text="💰 ۳۰ تا ۶۰ میلیون (اداری/ترید)", callback_data="quick_search:30to60"),
        ],
        [
            InlineKeyboardButton(text="💰 ۶۰ تا ۱۰۰ میلیون (مهندسی)", callback_data="quick_search:60to100"),
            InlineKeyboardButton(text="💰 بالای ۱۰۰ میلیون (پرچم‌دار)", callback_data="quick_search:above100"),
        ],
        [
            InlineKeyboardButton(text="🎮 لپ‌تاپ‌های گیمینگ", callback_data="quick_search:gaming"),
            InlineKeyboardButton(text="🎨 سرفیس و لمسی", callback_data="quick_search:surface"),
        ],
        [
            InlineKeyboardButton(text="🍏 مک‌بوک‌های اپل", callback_data="quick_search:apple"),
            InlineKeyboardButton(text="💼 لپ‌تاپ‌های لنوو تینک‌پد", callback_data="quick_search:thinkpad"),
        ],
        [
            InlineKeyboardButton(text="💻 مشاهده کل کاتالوگ", callback_data="tree:brands_root"),
            InlineKeyboardButton(text="🏢 شعب و موجودی حضوری", callback_data="branch:overview"),
        ],
    ]
    buttons.append([InlineKeyboardButton(text="💬 مشاوره آنلاین", callback_data="consultation:online")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

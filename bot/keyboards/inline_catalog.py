from __future__ import annotations

from urllib.parse import quote

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def catalog_models_keyboard(models: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"{brand} {model}", callback_data=f"cf:model:{index}")]
            for index, (model, brand) in enumerate(models)
        ]
    )


def facet_values_keyboard(field: str, values: list[str]) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=value, callback_data=f"cf:value:{field}:{index}")
        for index, value in enumerate(values)
    ]
    rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(text="بدون فیلتر", callback_data=f"cf:skip:{field}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def branch_location_keyboard(branches: list[dict[str, object]]) -> InlineKeyboardMarkup | None:
    rows: list[list[InlineKeyboardButton]] = []
    for index, branch in enumerate(branches):
        address = str(branch.get("address") or "")
        latitude = branch.get("latitude")
        longitude = branch.get("longitude")
        if latitude is not None and longitude is not None:
            maps_url = f"https://www.google.com/maps/dir/?api=1&destination={latitude},{longitude}"
            rows.append([
                InlineKeyboardButton(
                    text=f"📍 موقعیت {branch['name']}",
                    callback_data=f"cf:loc:{branch['branch_id']}",
                ),
                InlineKeyboardButton(text="مسیریابی", url=maps_url),
            ])
        elif address:
            maps_url = f"https://www.google.com/maps/search/?api=1&query={quote(address)}"
            rows.append([InlineKeyboardButton(text=f"مسیریابی {branch['name']}", url=maps_url)])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
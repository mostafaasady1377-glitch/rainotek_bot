from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def main_menu_kb(role: str = "customer") -> ReplyKeyboardMarkup:
    if role == "admin":
        keyboard = [
            [KeyboardButton(text="💻 کاتالوگ و مشخصات"), KeyboardButton(text="📦 موجودی")],
            [KeyboardButton(text="➕ ورود کالا"), KeyboardButton(text="➖ خروج کالا")],
            [KeyboardButton(text="🔄 انتقال بین شعب"), KeyboardButton(text="🧾 انبارگردانی")],
            [KeyboardButton(text="📊 گزارش و خروجی CSV"), KeyboardButton(text="⚠️ کمبود موجودی")],
            [KeyboardButton(text="📋 درخواست‌های خرید"), KeyboardButton(text="🔄 همگام‌سازی شیت")],
            [KeyboardButton(text="👥 مدیریت کاربران"), KeyboardButton(text="ℹ️ راهنما")],
        ]
    elif role == "branch_manager":
        keyboard = [
            [KeyboardButton(text="💻 کاتالوگ و مشخصات"), KeyboardButton(text="📦 موجودی")],
            [KeyboardButton(text="➕ ورود کالا"), KeyboardButton(text="➖ خروج کالا")],
            [KeyboardButton(text="🔄 انتقال بین شعب"), KeyboardButton(text="🧾 انبارگردانی")],
            [KeyboardButton(text="📋 درخواست‌های خرید"), KeyboardButton(text="ℹ️ راهنما")],
        ]
    else:  # customer / default
        keyboard = [
            [KeyboardButton(text="💻 کاتالوگ و مشخصات"), KeyboardButton(text="🔎 جستجوی هوشمند")],
            [KeyboardButton(text="🏷 لپ‌تاپ‌های نو و استوک"), KeyboardButton(text="🏢 شعب راینوتک")],
        ]

    keyboard.append([KeyboardButton(text="💬 کارشناس و پشتیبانی آنلاین")])
    keyboard.append([KeyboardButton(text="🤖 هوش مصنوعی AI")])
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        one_time_keyboard=False,
    )


"""Per-request contact selection; never fall back to another seller for referrals."""
from contextvars import ContextVar
from html import escape
import re
from sqlalchemy import select
from database.models import User

sales_contact = ContextVar('sales_contact', default=None)
sales_expert = ContextVar('sales_expert', default=None)


def telegram_chat_url(user):
    username = (user.username or '').lstrip('@')
    if re.fullmatch(r'[A-Za-z0-9_]{5,32}', username):
        return 'https://t.me/' + username
    return f'tg://user?id={int(user.telegram_id)}'


def expert_chat_keyboard():
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    expert = sales_expert.get()
    if not expert or sales_contact.get() is None:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text='💬 چت با کارشناس من', url=telegram_chat_url(expert)),
    ]])


async def resolve_sales_expert(session, user):
    if not user or user.role != 'customer' or not user.referrer_telegram_id:
        return None
    return await session.scalar(select(User).where(
        User.telegram_id == user.referrer_telegram_id,
        User.is_active.is_(True), User.role.in_(('seller', 'branch_manager')),
    ))


async def resolve_sales_contact(session, user):
    if not user or user.role != 'customer' or not user.referrer_telegram_id:
        return None
    expert = await resolve_sales_expert(session, user)
    return expert.phone_number if expert and expert.phone_number else ''


def contact_phone(default=None):
    assigned = sales_contact.get()
    return default if assigned is None else assigned


def contact_text():
    assigned = sales_contact.get()
    if assigned is None:
        return ''
    expert = sales_expert.get()
    identity = ''
    if expert:
        identity = '\n👤 ' + escape(expert.full_name or 'کارشناس فروش')
        identity += '\nشناسه: <code>' + str(expert.telegram_id) + '</code>'
        if expert.username:
            identity += '\n@' + escape(expert.username.lstrip('@'))
        identity += '\n<a href="' + escape(telegram_chat_url(expert), quote=True) + '">💬 چت تلگرام با کارشناس شما</a>'
    return '\n\n📞 کارشناس فروش راینو:' + identity + '\n' + (escape(assigned) if assigned else 'شمارهٔ کارشناس شما در دسترس نیست؛ با کارشناس معرف خود پیگیری کنید.')

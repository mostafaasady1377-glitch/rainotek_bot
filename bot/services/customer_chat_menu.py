"""Inline customer navigation that edits its message instead of appending menus."""
from aiogram import BaseMiddleware
from contextvars import ContextVar
import hashlib, sqlite3
from pathlib import Path

panel_anchor=ContextVar("panel_anchor",default=None)

def panel_keyboard(keyboard):
    if any(b.request_contact or b.request_location or b.request_poll or b.web_app for row in keyboard.keyboard for b in row):
        return keyboard
    path=Path("scratch/panel-actions.sqlite3");path.parent.mkdir(exist_ok=True)
    rows=[]
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS actions (token TEXT PRIMARY KEY, label TEXT NOT NULL)")
        for row in keyboard.keyboard:
            converted=[]
            for button in row:
                token=hashlib.sha256(button.text.encode()).hexdigest()[:24]
                db.execute("INSERT OR IGNORE INTO actions VALUES (?,?)",(token,button.text))
                converted.append(Button(text=button.text,callback_data="panel:"+token))
            rows.append(converted)
    return InlineKeyboardMarkup(inline_keyboard=rows)

def panel_label(token):
    try:
        with sqlite3.connect("scratch/panel-actions.sqlite3") as db:
            row=db.execute("SELECT label FROM actions WHERE token=?",(token,)).fetchone()
            return row[0] if row else None
    except sqlite3.Error:
        return None
from aiogram.exceptions import TelegramBadRequest,TelegramAPIError
from aiogram.types import InlineKeyboardButton as Button, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from bot.keyboards.reply_menus import AI_MENU_LABEL


def home_keyboard(user=None):
    from bot.handlers.role_panels import _is_admin
    rows=[
        [Button(text='💻 کاتالوگ و مشخصات',callback_data='cm:catalog')],
        [Button(text='🔎 جستجوی هوشمند',callback_data='cm:search'),Button(text='🏷 نو و استوک',callback_data='cm:condition')],
        [Button(text='🏢 شعب راینوتک',callback_data='cm:branches'),Button(text='💳 خرید اقساطی',callback_data='cm:installment')],
        [Button(text=AI_MENU_LABEL.replace('🤖','🧠'),callback_data='cm:ai')],
        [Button(text='💬 پیام به کارشناس فروش',callback_data='cm:support')],
    ]
    if getattr(user,'accounting_access',False):
        rows.append([Button(text='💼 امور مالی',callback_data='cm:finance')])
    if _is_admin(user) and user.telegram_id==7495565146:
        rows.append([Button(text='🔙 بازگشت به پنل مدیریت',callback_data='cm:admin')])
    rows.append([Button(text='🔽 جمع‌کردن منو',callback_data='cm:collapse')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def navigation_home(user):
    from bot.handlers.role_panels import _is_staff
    if _is_staff(user) and getattr(user,'view_panel',None)!='customer':
        role=getattr(user,'view_panel',None) or getattr(user,'role',None)
        title={'seller':'پنل کارشناسان فروش','branch_manager':'پنل مدیر شعبه'}.get(role,'پنل مدیریت')
        return Button(text='🔙 بازگشت به '+title,callback_data='cm:panelhome')
    return Button(text='🏠 منوی اصلی',callback_data='cm:home')


async def open_home(message,user,*,edit=False,welcome=None):
    text='💻 راینوتک\nپیام‌های ارسالی به بات برای پاسخ‌گویی و پشتیبانی در اختیار کارشناس معرف و مدیریت فروشگاه قرار می‌گیرند.'
    if welcome:
        text=welcome+'\n\n'+text
    if edit:
        return await message.edit_text(text,reply_markup=home_keyboard(user))
    # Telegram cannot edit a message sent with ReplyKeyboardRemove. Remove the
    # old keyboard with a temporary message, then leave only the inline menu.
    sent=await message.answer('راینوتک',reply_markup=ReplyKeyboardRemove())
    result=await message.answer(text,reply_markup=home_keyboard(user))
    try:
        await sent.delete()
    except TelegramAPIError:
        pass
    return result


class MenuMessage:
    def __init__(self,message,user,editable=True):
        self.original=message
        self.user=user
        self.deleted=False
        self.editable=editable

    def __getattr__(self,name):
        return getattr(self.original,name)

    async def answer(self,text,**kwargs):
        return await self.edit_text(text,**kwargs)

    async def _append(self,text,**kwargs):
        previous=self.original
        result=await previous.answer(text,**kwargs)
        # Retire the old buttons only after the replacement was delivered.
        if getattr(previous,'reply_markup',None) is not None:
            try:
                await previous.edit_reply_markup(reply_markup=None)
            except TelegramAPIError:
                pass
        if getattr(result,'message_id',None) is not None:
            self.original=result
            self.editable=True
            self.deleted=False
        return result

    async def delete(self,**kwargs):
        result=await self.original.delete(**kwargs)
        self.deleted=True
        return result

    async def edit_text(self,text,**kwargs):
        keyboard=kwargs.get('reply_markup')
        root_panel=isinstance(keyboard,ReplyKeyboardMarkup) and any(
            b.text=='📣 آگهی‌های امروز راینوتک' for row in keyboard.keyboard for b in row)
        if isinstance(keyboard,ReplyKeyboardMarkup):
            if any(button.text==AI_MENU_LABEL for row in keyboard.keyboard for button in row):
                keyboard=home_keyboard(self.user)
            else:
                keyboard=panel_keyboard(keyboard)
                if isinstance(keyboard,ReplyKeyboardMarkup):
                    return await self.original.answer(text,**kwargs)
        if keyboard is None:
            keyboard=InlineKeyboardMarkup(inline_keyboard=[])
        if isinstance(keyboard,InlineKeyboardMarkup):
            rows=[list(row) for row in keyboard.inline_keyboard]
            if not root_panel and not any(b.callback_data=='cm:home' for row in rows for b in row) and keyboard!=home_keyboard(self.user):
                rows.append([navigation_home(self.user)])
            kwargs['reply_markup']=InlineKeyboardMarkup(inline_keyboard=rows)
        from bot.services.chat_position import position
        at_bottom=position(self.original)
        if root_panel:
            # Clear legacy reply keyboards on every entry/return to a role panel.
            cleanup=await self.original.answer('پنل راینوتک',reply_markup=ReplyKeyboardRemove())
            result=await self._append(text,**kwargs)
            try:
                await cleanup.delete()
            except TelegramAPIError:
                pass
            return result
        if not self.editable or self.deleted or self.original.photo or at_bottom is False or (kwargs.get("reply_markup") is not None and not isinstance(kwargs["reply_markup"],InlineKeyboardMarkup)):
            return await self._append(text,**kwargs)
        try:
            return await self.original.edit_text(text,**kwargs)
        except TelegramBadRequest as exc:
            if 'message is not modified' in str(exc):
                return self.original
            if any(reason in str(exc).lower() for reason in ("message can't be edited",'message to edit not found','there is no text in the message')):
                return await self._append(text,**kwargs)
            raise


class InlineNavigationMiddleware(BaseMiddleware):
    async def __call__(self,handler,event,data):
        if event.message:
            event=event.model_copy(update={'message':MenuMessage(event.message,data.get('current_user'))})
        return await handler(event,data)


class PanelMessageMiddleware(BaseMiddleware):
    async def __call__(self,handler,event,data):
        anchor=panel_anchor.get()
        if anchor is None:
            from bot.services.chat_position import position
            position(event,record=True)
        proxy=MenuMessage(anchor or event,data.get('current_user'),editable=anchor is not None)
        if anchor is not None:
            proxy.text=event.text
            proxy.from_user=event.from_user
        return await handler(proxy,data)

import asyncio
from datetime import datetime,timezone
from html import escape
from aiogram import F,Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State,StatesGroup
from aiogram.types import InlineKeyboardButton as Button,InlineKeyboardMarkup,ForceReply
from bot.handlers.role_panels import _is_admin,_is_staff
from bot.services.customer_inbox import InboxStore,inbox_targets

router=Router()
ENTRY='📨 پیام‌های مشتریان'
STAFF_ENTRY='📨 پیام‌های مشتریان من'


class ReplyState(StatesGroup):
    message=State()


def kb(rows):return InlineKeyboardMarkup(inline_keyboard=rows)


async def thread_list(message,page=0,current_user=None):
    targets=await inbox_targets(current_user)
    rows,total=await asyncio.to_thread(InboxStore().threads,page,targets)
    buttons=[[Button(text=(str(r['chat_id'])+' · '+r['name']+' · '+r['body'][:20])[:64],callback_data=f"inbox:thread:{r['chat_id']}:0")] for r in rows]
    nav=[]
    if page:nav.append(Button(text='قبلی',callback_data=f'inbox:list:{page-1}'))
    if (page+1)*8<total:nav.append(Button(text='بعدی',callback_data=f'inbox:list:{page+1}'))
    if nav:buttons.append(nav)
    buttons.append([Button(text='🔄 تازه‌سازی پیام‌ها',callback_data=f'inbox:list:{page}')])
    await message.answer(f'📨 صندوق پیام مشتریان\nگفت‌وگوها: {total}\nپیام‌های دریافت‌شده از زمان فعال‌شدن این بخش.',reply_markup=kb(buttons))


@router.message(F.text.in_({ENTRY,STAFF_ENTRY}))
async def inbox_entry(message,state:FSMContext,current_user=None):
    if not (_is_admin(current_user) or _is_staff(current_user)):
        await message.answer('دسترسی مجاز نیست.')
        return
    await state.clear()
    await thread_list(message,current_user=current_user)


@router.callback_query(F.data.startswith('inbox:'))
async def inbox_action(callback,state:FSMContext,current_user=None):
    if not (_is_admin(current_user) or _is_staff(current_user)):
        await callback.answer('دسترسی مجاز نیست.',show_alert=True)
        return
    await callback.answer()
    parts=callback.data.split(':')
    try:
        if parts[1]=='list':
            await state.clear()
            await thread_list(callback.message,int(parts[2]),current_user)
            return
        target=int(parts[2])
        allowed=await inbox_targets(current_user)
        if allowed is not None and target not in allowed:
            raise ValueError('این مشتری به کارشناس دیگری مربوط است.')
        rows,total=await asyncio.to_thread(InboxStore().messages,target,int(parts[3]) if parts[1]=='thread' and len(parts)>3 else 0)
        if not rows:raise ValueError('گفت‌وگو یافت نشد.')
        if parts[1]=='reply':
            await state.set_state(ReplyState.message)
            prompt=await callback.message.answer('پاسخ برای '+escape(rows[0]['name'])+' را در پاسخ به همین پیام بفرستید؛ متن یا فایل شما به همین مشتری ارسال می‌شود. برای انصراف /cancel را بفرستید.',reply_markup=ForceReply(selective=True,input_field_placeholder='پاسخ به مشتری'))
            await state.update_data(inbox_target=target,inbox_prompt=prompt.message_id)
            return
        page=int(parts[3]) if len(parts)>3 else 0
        text='📨 '+escape(rows[0]['name'])+'\nشناسه: '+str(target)+'\n'+('@'+escape(rows[0]['username']) if rows[0]['username'] else '')+'\n\n'
        for r in reversed(rows):
            from bot.services.local_time import format_local
            text+=('↗️ پاسخ پشتیبانی\n' if r.get('direction')=='outgoing' else '↙️ پیام کاربر\n')+escape(format_local(datetime.fromtimestamp(r['created_at'],timezone.utc)))+'\n'+escape(r['body'][:550])+'\n────────\n'
        buttons=[[Button(text='✉️ پاسخ به این مشتری',callback_data=f'inbox:reply:{target}')]]
        for r in rows:
            if r.get('direction')=='outgoing':
                continue
            buttons.append([Button(text='مشاهده کامل پیام '+str(r['id']),callback_data=f"inbox:media:{target}:{r['message_id']}")])
        if parts[1]=='media':
            message_id=int(parts[3])
            with InboxStore().connect() as c:
                allowed=c.execute('SELECT 1 FROM customer_inbox WHERE chat_id=? AND message_id=?',(target,message_id)).fetchone()
            if not allowed:raise ValueError('پیام معتبر نیست.')
            await callback.bot.copy_message(callback.message.chat.id,target,message_id)
            return
        nav=[]
        if page:nav.append(Button(text='جدیدتر',callback_data=f'inbox:thread:{target}:{page-1}'))
        if (page+1)*5<total:nav.append(Button(text='قدیمی‌تر',callback_data=f'inbox:thread:{target}:{page+1}'))
        if nav:buttons.append(nav)
        buttons.append([Button(text='🔄 تازه‌سازی گفت‌وگو',callback_data=f'inbox:thread:{target}:0')])
        buttons.append([Button(text='فهرست مشتریان',callback_data='inbox:list:0')])
        await callback.message.answer(text[:3900],reply_markup=kb(buttons))
    except (ValueError,IndexError):
        await callback.message.answer('گفت‌وگو یا درخواست معتبر نیست.')
    except TelegramAPIError:
        await callback.message.answer('دریافت فایل یا ارسال پیام فعلاً ممکن نیست؛ دوباره بررسی کنید.')


@router.message(ReplyState.message)
async def send_admin_reply(message,state:FSMContext,current_user=None):
    if not (_is_admin(current_user) or _is_staff(current_user)):
        await state.clear()
        await message.answer('دسترسی مجاز نیست.')
        return
    if message.text in ('/cancel','/menu','/start',ENTRY,STAFF_ENTRY,'🔙 بازگشت به پنل مدیریت'):
        await state.clear()
        await message.answer('ارسال پاسخ لغو شد.')
        return
    context=await state.get_data()
    target=context.get('inbox_target')
    allowed=await inbox_targets(current_user)
    if allowed is not None and target not in allowed:
        await state.clear()
        await message.answer('دسترسی به این مشتری ثبت نشده یا تغییر کرده است.')
        return
    if not message.reply_to_message or message.reply_to_message.message_id!=context.get('inbox_prompt'):
        await message.answer('برای ارسال به مشتری، پیام خود را در پاسخ به پیام درخواست پاسخ بفرستید؛ برای انصراف /cancel.')
        return
    store=InboxStore()
    if not target or not await asyncio.to_thread(store.claim_reply,current_user.telegram_id,message.message_id,target):
        await message.answer('این پاسخ قبلاً پردازش شده یا مقصد معتبر نیست.')
        return
    try:
        result=await message.bot.copy_message(target,message.chat.id,message.message_id)
    except Exception:
        await asyncio.to_thread(store.reply_result,current_user.telegram_id,message.message_id,'delivery_unconfirmed')
        await state.clear()
        await message.answer('ارسال تأیید نشد؛ برای جلوگیری از ارسال تکراری، خودکار دوباره ارسال نمی‌شود.')
        return
    await asyncio.to_thread(store.reply_result,current_user.telegram_id,message.message_id,'sent',result.message_id,(message.text or message.caption or '[فایل یا رسانه]')[:12000])
    await message.answer('✅ پاسخ در چت کاربر با همین بات ارسال شد. برای ادامه، دوباره به پیام درخواست پاسخ ریپلای کنید.',reply_markup=kb([[Button(text='بازگشت به گفت‌وگو',callback_data=f'inbox:thread:{target}:0')]]))

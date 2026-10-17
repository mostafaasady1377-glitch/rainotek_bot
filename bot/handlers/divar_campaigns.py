import asyncio
import subprocess
import sys
import json
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import FSInputFile, InputMediaPhoto, InlineKeyboardButton, InlineKeyboardMarkup

from bot.handlers.role_panels import _is_admin, _is_staff
from bot.services.divar_campaigns import CampaignStore

router = Router()
ENTRY = '📣 آگهی‌های امروز راینوتک'
LEGACY_ENTRY = '📣 آگهی‌های دیوار راینوتک'
SHOP = '🏪 صف فروشگاه دیوار'
_autofill_process = None


class DivarLinkState(StatesGroup):
    url = State()

class DivarEditState(StatesGroup):
    text = State()


def gallery_keyboard(assignment_id,index,count):
    return markup([[InlineKeyboardButton(text='⬅️ قبلی',callback_data=f'dr:gallery:{assignment_id}:{(index-1)%count}'),
                    InlineKeyboardButton(text=f'{index+1} / {count} • بعدی ➡️',callback_data=f'dr:gallery:{assignment_id}:{(index+1)%count}')]])


def can_view(user, ad=None):
    if not _is_staff(user):
        return False
    return bool(_is_admin(user) or (getattr(user, 'managed_branch_id', None) and
                (ad is None or ad['branch_id'] == user.managed_branch_id)))


def markup(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def load_store():
    store = CampaignStore()
    await asyncio.to_thread(store.sync)
    return store


async def show_list(message, user, page=0, shop=False):
    if not can_view(user):
        await message.answer('دسترسی فروشندگان یا شعبهٔ شما ثبت نشده است.')
        return
    try:
        store = await load_store()
        rows = await asyncio.to_thread(store.list, None if _is_admin(user) else user.managed_branch_id, shop)
    except (OSError, ValueError):
        await message.answer('بستهٔ آگهی‌ها در دسترس نیست؛ مدیر باید بسته را بازسازی کند.')
        return
    page = max(0, min(page, max(0, (len(rows)-1)//8)))
    buttons = [[InlineKeyboardButton(text=f"{'✅' if r['state']=='editorial_approved' else '📝'} {r['number']}. {r['ad']['title'][:45]}",
                                    callback_data=f"dc:view:{r['number']}")] for r in rows[page*8:page*8+8]]
    nav = []
    if page:
        nav.append(InlineKeyboardButton(text='قبلی', callback_data=f'dc:list:{page-1}:{int(shop)}'))
    if (page+1)*8 < len(rows):
        nav.append(InlineKeyboardButton(text='بعدی', callback_data=f'dc:list:{page+1}:{int(shop)}'))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text='صف فروشگاه' if not shop else 'همه آگهی‌ها', callback_data=f'dc:list:0:{int(not shop)}')])
    buttons.insert(0, [InlineKeyboardButton(text='🎯 ۱۰ آگهی پیشنهادی امروز', callback_data='dr:current')])
    title = 'صف داخلی آماده‌سازی فروشگاه' if shop else 'آگهی‌های دیوار راینوتک'
    await message.answer(f'<b>{title}</b>\nتعداد قابل مشاهده: {len(rows)}؛ صفحه {page+1}\n'
                         'آگهی را برای دیدن عنوان، متن و تصاویر انتخاب کنید. انتشار و پرداخت پس از بازبینی شما در دیوار انجام می‌شود.',
                         reply_markup=markup(buttons))


@router.message(F.text.in_({ENTRY,LEGACY_ENTRY, SHOP}))
async def entry(message, state: FSMContext, current_user=None):
    if not can_view(current_user):
        await message.answer('دسترسی مجاز نیست.')
        return
    await state.clear()
    if message.text in (ENTRY,LEGACY_ENTRY):
        await show_personal(message, current_user)
        return
    await show_list(message, current_user, shop=message.text == SHOP)


async def show_personal(message, user, index=None,batch=0,show_gallery=True):
    from bot.services.divar_recommendations import RecommendationStore, DAY, day_start
    from datetime import datetime, timezone, timedelta
    batch=max(0,min(int(batch),9))
    store = RecommendationStore()
    await asyncio.to_thread(store.allocate_daily,batch=batch)
    rows = await asyncio.to_thread(store.daily_rows, user.telegram_id,batch=batch)
    rows = [r for r in rows if r['state']!='unavailable']
    if index is None and rows:
        buttons=[[InlineKeyboardButton(text=f"{i+1}. {r['ad']['title']}",callback_data=f'dr:view:{i}:{batch}')] for i,r in enumerate(rows)]
        buttons.append([InlineKeyboardButton(text='🔄 تازه‌سازی پیشنهادهای من',callback_data=f'dr:batch:{(batch+1)%10}')])
        buttons.append([InlineKeyboardButton(text='🏪 فروشگاه دیوار من',callback_data='dr:link')])
        await message.answer(f'🎯 بستهٔ {batch+1} از ۱۰؛ آگهی‌های امروز شما: {len(rows)} از ۱۰\nعنوان فروشگاهی همراه گارانتی؛ قیمت و کانفیگ از موجودی راینوتک. اگر موجودی یکتا کافی نباشد، تعداد کمتری نمایش داده می‌شود.',reply_markup=markup(buttons))
        return
    row = rows[max(0,min(index or 0,len(rows)-1))] if rows else None
    if not row or row['state']=='unavailable':
        await message.answer('در این بازه آگهی یکتای قابل تخصیص برای شعبهٔ شما وجود ندارد؛ نقش، شعبه و موجودی بررسی شود.',
            reply_markup=markup([[InlineKeyboardButton(text='🔗 ثبت لینک فروشگاه دیوار من',callback_data='dr:link')],[InlineKeyboardButton(text='🔄 بسته بعدی پیشنهادها',callback_data=f'dr:batch:{(batch+1)%10}')]]))
        return
    import time
    end = datetime.fromtimestamp(day_start(time.time())+DAY, timezone(timedelta(hours=3,minutes=30)))
    ad = row['ad']
    text = (f"🎯 <b>پیشنهاد اختصاصی شما تا {end:%H:%M} به وقت تهران</b>\n\n"
            f"<b>{escape(ad['title'])}</b>\n\n{escape(ad['description'])}\n\n"
            'پیش از انتقال: ' + '؛ '.join(escape(b) for b in ad['blockers']))
    buttons = [
        [InlineKeyboardButton(text='انتشار آگهی',callback_data=f"dr:transfer:{row['id']}:{row['digest'][:16]}")],
        [InlineKeyboardButton(text='🏪 فروشگاه دیوار من',callback_data='dr:link')],
        [InlineKeyboardButton(text='✏️ ویرایش آگهی',callback_data=f"dr:edit:{row['id']}:{row['digest'][:16]}:{batch}")],
    ]
    from bot.services.divar_gallery import gallery_files
    from bot.services.customer_chat_menu import MenuMessage
    photos=await asyncio.to_thread(gallery_files,ad['product_id'])
    original=getattr(message,'original',message)
    if show_gallery and photos:
        await original.answer_photo(FSInputFile(photos[0]['path']),caption=f'گالری آگهی • ۱ از {len(photos)}',
                                    reply_markup=gallery_keyboard(row['id'],0,len(photos)))
    await MenuMessage(original,user,editable=False).answer(text[:4000],reply_markup=markup(buttons))


@router.message(DivarEditState.text)
async def save_personal_text(message,state:FSMContext,current_user=None):
    if not can_view(current_user):
        await state.clear()
        return
    data=await state.get_data()
    if message.text=='/cancel':
        await state.clear()
        await show_personal(message,current_user,batch=data.get('ad_batch',0))
        return
    parts=(message.text or '').split('\n',1)
    if len(parts)!=2:
        await message.answer('خط اول عنوان و از خط دوم توضیحات آگهی را بفرستید. برای انصراف /cancel')
        return
    from bot.services.divar_recommendations import RecommendationStore
    store=RecommendationStore()
    try:
        await asyncio.to_thread(store.edit_text,data['ad_id'],current_user.telegram_id,data['ad_digest'],*parts)
    except ValueError as exc:
        await message.answer(escape(str(exc)))
        return
    await state.clear()
    rows=await asyncio.to_thread(store.daily_rows,current_user.telegram_id,batch=data['ad_batch'])
    rows=[r for r in rows if r['state']!='unavailable']
    index=next((i for i,r in enumerate(rows) if r['id']==data['ad_id']),None)
    await show_personal(message,current_user,index=index,batch=data['ad_batch'],show_gallery=False)



@router.message(F.text == '🔗 اتصال فروشگاه دیوار من')
async def connection_entry(message,state:FSMContext,current_user=None):
    if not can_view(current_user):
        await message.answer('دسترسی مجاز نیست.')
        return
    from bot.services.divar_recommendations import RecommendationStore
    account=await asyncio.to_thread(RecommendationStore().account_link,current_user.telegram_id)
    await state.set_state(DivarLinkState.url)
    text='لینک فروشگاه خود را از نوار آدرس Chrome کپی و همین‌جا ارسال کنید. نمونه:\nhttps://divar.ir/my-divar/my-posts/business/mWYasTCg\nبرای انصراف /cancel'
    buttons=[]
    if account:
        text='مسیر فروشگاه شما ذخیره شده است. برای تغییر، لینک جدید را بفرستید.\n\n'+text
        buttons=[[InlineKeyboardButton(text='ورود به فروشگاه من با حساب خودم',url=account['url'])],[InlineKeyboardButton(text='آگهی‌های امروز من',callback_data='dr:current')]]
    text+='\n\nورود با شماره و کد خودتان در دیوار انجام می‌شود؛ بات رمز یا کد ورود نمی‌گیرد. برای انتقال، لینک آگهی را با Chrome دارای افزونه باز کنید.'
    await message.answer(text,reply_markup=markup(buttons))


@router.message(DivarLinkState.url)
async def register_personal_link(message, state: FSMContext, current_user=None):
    if not can_view(current_user):
        await state.clear()
        await message.answer('دسترسی مجاز نیست.')
        return
    if message.text in ('/cancel','لغو'):
        await state.clear()
        await message.answer('ثبت لینک لغو شد.')
        return
    try:
        from bot.services.divar_recommendations import RecommendationStore
        url = await asyncio.to_thread(RecommendationStore().register_link,current_user.telegram_id,message.text or '')
        await state.clear()
        await message.answer('✅ مسیر فروشگاه شما دائمی ذخیره شد؛ برای آگهی‌های بعدی نیازی به ثبت دوباره نیست. تا زمانی که خودتان آن را تغییر دهید، همهٔ انتقال‌ها به همین فروشگاه هدایت می‌شوند. ورود دیوار در مرورگر خودتان انجام می‌شود.',
            reply_markup=markup([[InlineKeyboardButton(text='فروشگاه من در دیوار',url=url)],
                                [InlineKeyboardButton(text='پیشنهاد اختصاصی من',callback_data='dr:current')]]))
    except ValueError as exc:
        await message.answer(escape(str(exc))+'\nبرای انصراف /cancel را بفرستید.')


@router.callback_query(F.data.startswith('dr:'))
async def personal_action(callback, state: FSMContext, current_user=None):
    if not can_view(current_user):
        await callback.answer('دسترسی مجاز نیست.', show_alert=True)
        return
    await callback.answer()
    try:
        if callback.data.startswith('dr:gallery:'):
            from bot.services.divar_recommendations import RecommendationStore
            from bot.services.divar_gallery import gallery_files
            parts=callback.data.split(':')
            rows=await asyncio.to_thread(RecommendationStore().daily_rows,current_user.telegram_id,batch=None)
            row=next((r for r in rows if r['id']==int(parts[2]) and r['state']!='unavailable'),None)
            if not row:
                raise ValueError('آگهی در دسترس نیست؛ پیشنهادهای امروز را باز کنید.')
            photos=await asyncio.to_thread(gallery_files,row['ad']['product_id'])
            index=int(parts[3])%len(photos)
            original=getattr(callback.message,'original',callback.message)
            await original.edit_media(InputMediaPhoto(media=FSInputFile(photos[index]['path']),caption=f'گالری آگهی • {index+1} از {len(photos)}'),
                                      reply_markup=gallery_keyboard(row['id'],index,len(photos)))
        elif callback.data.startswith('dr:edit:'):
            from bot.services.divar_recommendations import RecommendationStore
            parts=callback.data.split(':')
            await asyncio.to_thread(RecommendationStore().review_transfer,int(parts[2]),current_user.telegram_id,parts[3])
            await state.set_state(DivarEditState.text)
            await state.update_data(ad_id=int(parts[2]),ad_digest=parts[3],ad_batch=int(parts[4]))
            await callback.message.answer('✏️ متن جدید را در یک پیام بفرستید:\nخط اول: عنوان آگهی\nخط دوم به بعد: توضیحات\n\nبرای انصراف /cancel\nقیمت و مشخصات را مطابق موجودی بنویسید.')
        elif callback.data == 'dr:link':
            await state.set_state(DivarLinkState.url)
            await callback.message.answer('لینک مستقیم فروشگاه خودتان در دیوار را بفرستید. برای انصراف /cancel را بفرستید.')
        elif callback.data.startswith('dr:batch:'):
            await state.clear()
            await show_personal(callback.message,current_user,batch=int(callback.data.split(':')[2]))
        elif callback.data.startswith('dr:photos:'):
            from bot.services.divar_recommendations import RecommendationStore
            from bot.services.divar_gallery import gallery_files
            rows=await asyncio.to_thread(RecommendationStore().daily_rows,current_user.telegram_id)
            rows=[r for r in rows if r['state']!='unavailable']
            index=int(callback.data.split(':')[2])
            if index<0 or index>=len(rows):
                raise ValueError('آگهی تازه را دوباره باز کنید.')
            photos=await asyncio.to_thread(gallery_files,rows[index]['ad']['product_id'])
            await callback.message.answer_photo(FSInputFile(photos[0]['path']),caption=f'گالری آگهی • ۱ از {len(photos)}',
                reply_markup=gallery_keyboard(rows[index]['id'],0,len(photos)))
        elif callback.data.startswith('dr:view:'):
            await state.clear()
            await show_personal(callback.message,current_user,int(callback.data.split(':')[2]),int(callback.data.split(':')[3]) if len(callback.data.split(':'))>3 else 0)
        elif callback.data == 'dr:current':
            await state.clear()
            await show_personal(callback.message,current_user)
        else:
            parts = callback.data.split(':')
            if len(parts)!=4 or parts[1]!='transfer':
                raise ValueError('درخواست نامعتبر است.')
            from bot.services.divar_recommendations import RecommendationStore
            store = RecommendationStore()
            account = await asyncio.to_thread(store.account_link,current_user.telegram_id)
            if not account:
                await callback.message.answer('ابتدا لینک فروشگاه خودتان را ثبت کنید.',reply_markup=markup([[InlineKeyboardButton(text='ثبت لینک فروشگاه من',callback_data='dr:link')]]))
                return
            from bot.services.divar_handoff import HandoffStore
            from bot.config import get_settings
            if not get_settings().DIVAR_HANDOFF_BASE_URL:
                await asyncio.to_thread(store.review_transfer,int(parts[2]),current_user.telegram_id,parts[3])
                await callback.message.answer('✅ بازبینی آگهی ثبت شد. دکمه زیر پنل کسب‌وکار ذخیره‌شدهٔ خودتان را باز می‌کند. انتقال خودکار متن و عکس هنوز فعال نیست؛ ثبت نهایی و پرداخت در دیوار با شماست.',reply_markup=markup([[InlineKeyboardButton(text='🏪 بازکردن پنل کسب‌وکار من در دیوار',url=account['url'])],[InlineKeyboardButton(text='بازگشت به آگهی‌های آماده',callback_data='dr:current')]]))
                return
            url = await asyncio.to_thread(HandoffStore().issue,int(parts[2]),current_user.telegram_id,parts[3])
            await callback.message.answer('آگهی برای مسیر ذخیره‌شدهٔ فروشگاه شما آماده شد. فروشگاه فقط یک‌بار ثبت می‌شود و برای همهٔ آگهی‌های بعدی باقی می‌ماند. دکمه را در مرورگر دارای افزونه باز کنید. مجوز دریافت همین آگهی موقت است؛ مسیر فروشگاه شما منقضی نمی‌شود.',
                reply_markup=markup([[InlineKeyboardButton(text='انتقال آگهی به مرورگر و فروشگاه من',url=url)]]))
    except TelegramBadRequest as exc:
        if 'message is not modified' not in str(exc).lower():
            raise
    except (ValueError,OSError,IndexError) as exc:
        await callback.message.answer('انجام نشد: '+escape(str(exc)))


@router.callback_query(F.data.startswith('dc:'))
async def action(callback, current_user=None):
    if not can_view(current_user):
        await callback.answer('دسترسی مجاز نیست.', show_alert=True)
        return
    parts = callback.data.split(':')
    await callback.answer()
    try:
        if len(parts)==4 and parts[1]=='list':
            await show_list(callback.message, current_user, int(parts[2]), parts[3]=='1')
            return
        store = await load_store()
        number = int(parts[2])
        row = await asyncio.to_thread(store.get, number)
        if not row or not can_view(current_user, row['ad']):
            raise ValueError('این آگهی در دسترس شعبهٔ شما نیست')
        ad = row['ad']
        if parts[1]=='status':
            if not _is_admin(current_user):
                raise ValueError('این عملیات فقط برای مالک مجاز است')
            from divar_campaign.engine import RUNTIME
            path=RUNTIME/f'autofill-{number}.json'
            result=json.loads(path.read_text(encoding='utf8')) if path.exists() else {}
            labels={'opening':'در حال بازکردن Chrome',
                'waiting_for_form_or_user_login':'منتظر فرم دیوار یا ورود شما',
                'text_fields_filled_human_review_required':'عنوان و توضیحات شامل مشخصات و قیمت پر شد؛ مراحل بعدی با شماست',
                'blocked':'آماده‌سازی کامل نشد؛ اتصال، ورود یا پیش‌نویس موجود را بررسی کنید'}
            await callback.message.answer(labels.get(result.get('state'),'نتیجه‌ای ثبت نشده است'))
            return
        if parts[1] in ('approve','revoke'):
            if not _is_admin(current_user):
                raise ValueError('تأیید نهایی فقط توسط مالک/ادمین مجاز است')
            if parts[1]=='approve' and len(parts)==4:
                if row['digest'][:16]!=parts[3]:
                    await callback.message.answer('🔄 اطلاعات خودکار تازه شد؛ نسخه تازه را بازبینی کنید.',reply_markup=markup([[InlineKeyboardButton(text='مشاهده نسخه تازه',callback_data=f'dc:view:{number}')]]))
                    return
                await asyncio.to_thread(store.approve, number, parts[3], current_user.telegram_id)
                await callback.message.answer('✅ تأیید متن ثبت شد و آگهی به صف داخلی فروشگاه رفت.\n'
                    'با دکمه زیر Chrome روی سیستم میزبان باز می‌شود و عنوان و توضیحات شامل مشخصات و قیمت پر می‌شود. در صورت نیاز وارد دیوار شوید؛ عکس واقعی، مراحل بعدی و پرداخت با شماست.',
                    reply_markup=markup([[InlineKeyboardButton(text='📝 بازکردن و پرکردن خودکار فرم دیوار',callback_data=f'dc:autofill:{number}')],
                        [InlineKeyboardButton(text='مشاهده صف فروشگاه',callback_data='dc:list:0:1')]]))
            elif parts[1]=='revoke':
                await asyncio.to_thread(store.revoke, number, current_user.telegram_id)
                await callback.message.answer('تأیید لغو و آگهی از صف فروشگاه خارج شد.')
            return
        if parts[1]=='autofill':
            if not _is_admin(current_user) or row['state']!='editorial_approved':
                raise ValueError('تأیید تازه مالک لازم است')
            global _autofill_process
            if _autofill_process and _autofill_process.poll() is None:
                await callback.message.answer('پنجره آماده‌سازی قبلی باز است؛ ابتدا آن را تکمیل یا ببندید.')
                return
            from bot.services.divar_campaigns import ROOT
            _autofill_process=subprocess.Popen([sys.executable,'-m','divar_campaign.autofill',str(number)],cwd=str(ROOT),
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            await callback.message.answer('درخواست بازکردن فرم روی سیستم میزبان اجرا شد. پس از ورود به دیوار، عنوان و توضیحات خودکار پر می‌شوند. این پیام تأیید پرشدن فرم یا انتشار نیست.',
                reply_markup=markup([[InlineKeyboardButton(text='وضعیت پرکردن فرم',callback_data=f'dc:status:{number}')]]))
            return
        if parts[1]=='photos':
            from bot.services.divar_gallery import gallery_files
            photos=await asyncio.to_thread(gallery_files,ad['number']-1)
            for start in (0,10):
                await callback.message.answer_media_group([InputMediaPhoto(media=FSInputFile(p['path'])) for p in photos[start:start+10]])
            return
        if parts[1]!='view':
            return
        from bot.services.divar_gallery import gallery_files
        photos=await asyncio.to_thread(gallery_files,ad['number']-1)
        await callback.message.answer_photo(FSInputFile(photos[0]['path']),caption=ad['title'])
        buttons=[[InlineKeyboardButton(text='🖼 مشاهده ۱۵ عکس آگهی',callback_data=f'dc:photos:{number}')]]
        if current_user.telegram_id==7495565146:
            buttons.append([InlineKeyboardButton(text='🏪 پنل کسب‌وکار من در دیوار',url='https://divar.ir/my-divar/my-posts/business/mWYasTCg')])
        if _is_admin(current_user):
            buttons.append([InlineKeyboardButton(text='لغو تأیید' if row['state']=='editorial_approved' else '✅ تأیید متن و انتقال به صف فروشگاه',
                callback_data=f'dc:revoke:{number}' if row['state']=='editorial_approved' else f"dc:approve:{number}:{row['digest'][:16]}")])
        buttons.append([InlineKeyboardButton(text='فهرست آگهی‌ها',callback_data='dc:list:0:0')])
        text=f"<b>آگهی {number}: {escape(ad['title'])}</b>\n\n{escape(ad['description'])}\n\n<b>نیازمند تکمیل پیش از انتشار:</b>\n"+'\n'.join('• '+escape(b) for b in ad['blockers'])
        await callback.message.answer(text[:4000],reply_markup=markup(buttons))
    except (ValueError, IndexError, OSError) as exc:
        await callback.message.answer('انجام نشد: '+escape(str(exc)))

import io
import zipfile
from html import escape
from aiogram import Router, F
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup as K, BufferedInputFile
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from database.models import FinancialDocument as Document, User, StaffActivity
from database.session import AsyncSessionLocal
from bot.handlers.role_panels import _is_admin
from bot.services.local_time import format_local

router = Router()
CATEGORIES = {'invoice':'فاکتورها', 'cheque':'چک‌ها', 'other':'سایر مدارک'}


class FinanceState(StatesGroup):
    upload = State()


def authorized(user):
    return bool(user and user.is_active is not False and (_is_admin(user) or user.accounting_access))


def home_keyboard():
    return K(inline_keyboard=[[B(text=label, callback_data=f'fin:list:{key}:0')] for key, label in CATEGORIES.items()])


@router.message(F.text == '💼 امور مالی')
async def finance_home(message, state, current_user):
    if message.chat.type != 'private':
        await message.answer('مدارک مالی فقط در گفت‌وگوی خصوصی با بات قابل دسترسی هستند.')
        return
    if not authorized(current_user):
        await message.answer('دسترسی امور مالی برای شما ثبت نشده است.')
        return
    await state.clear()
    await message.answer('💼 بایگانی امور مالی\nمدارک را دسته‌بندی، بارگذاری و دریافت کنید. ثبت تصویر چک یا رسید، تأیید وصول یا پرداخت نیست.', reply_markup=home_keyboard())


async def list_documents(session, category, page):
    if category not in CATEGORIES or page < 0:
        raise ValueError
    total = await session.scalar(select(func.count(Document.id)).where(Document.category == category)) or 0
    page = min(page, max(0, (total-1)//5))
    documents = list((await session.scalars(select(Document).where(Document.category == category).order_by(Document.id.desc()).offset(page*5).limit(5))).all())
    return documents, total, page


@router.callback_query(F.data.startswith('fin:'))
async def finance_action(callback, state, current_user):
    if callback.message.chat.type != 'private' or not authorized(current_user):
        await callback.answer('دسترسی مجاز نیست.', show_alert=True)
        return
    try:
        parts = callback.data.split(':')
        action = parts[1]
        if action == 'home' and len(parts) == 2:
            await state.clear()
            await callback.answer()
            await callback.message.answer('بایگانی امور مالی', reply_markup=home_keyboard())
            return
        if action == 'upload' and len(parts) == 3 and parts[2] in CATEGORIES:
            await state.clear()
            await state.set_state(FinanceState.upload)
            await state.update_data(finance_category=parts[2])
            await callback.answer()
            await callback.message.answer('عکس یا PDF مدرک را بفرستید؛ توضیح را در کپشن بنویسید. سقف هر فایل ۱۰ مگابایت است. برای چند مدرک، آن‌ها را یکی‌یکی ارسال کنید.', reply_markup=K(inline_keyboard=[[B(text='پایان آپلود', callback_data='fin:home')]]))
            return
        if action == 'file' and len(parts) == 3:
            async with AsyncSessionLocal() as session:
                document = await session.get(Document, int(parts[2]))
            if not document:
                raise ValueError
            await callback.answer()
            caption = f'مدرک #{document.id} · {CATEGORIES[document.category]}\n{format_local(document.created_at)}\n{escape((document.caption or "")[:150])}'
            if document.media_type == 'photo':
                await callback.message.answer_photo(document.file_id, caption=caption)
            else:
                await callback.message.answer_document(document.file_id, caption=caption)
            return
        if action not in {'list','zip'} or len(parts) != 4:
            raise ValueError
        category, page = parts[2], int(parts[3])
        async with AsyncSessionLocal() as session:
            documents, total, page = await list_documents(session, category, page)
        await callback.answer()
        if action == 'zip':
            if not documents:
                raise ValueError
            buffer = io.BytesIO()
            size = 0
            with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
                for document in documents:
                    source = io.BytesIO()
                    await callback.bot.download(document.file_id, destination=source)
                    size += source.getbuffer().nbytes
                    if size > 30*1024*1024:
                        await callback.message.answer('حجم این صفحه بیش از سقف دانلود گروهی است؛ مدارک را جداگانه دریافت کنید.')
                        return
                    archive.writestr(f'{document.id}.jpg' if document.media_type == 'photo' else f'{document.id}.pdf', source.getvalue())
                    archive.writestr(f'{document.id}-info.txt', f'{document.file_name or ""}\n{document.caption or ""}\n{format_local(document.created_at)}\nuploader:{document.uploader_telegram_id}\nbranch:{document.branch_id or "unassigned"}')
            await callback.message.answer_document(BufferedInputFile(buffer.getvalue(), filename=f'{category}-page-{page+1}.zip'))
            return
        rows = [[B(text=f'#{d.id} · {format_local(d.created_at)}', callback_data=f'fin:file:{d.id}')] for d in documents]
        rows.append([B(text='➕ بارگذاری مدرک', callback_data=f'fin:upload:{category}')])
        if documents:
            rows.append([B(text='📥 دانلود گروهی این صفحه (ZIP)', callback_data=f'fin:zip:{category}:{page}')])
        navigation = []
        if page:
            navigation.append(B(text='قبلی', callback_data=f'fin:list:{category}:{page-1}'))
        if (page+1)*5 < total:
            navigation.append(B(text='بعدی', callback_data=f'fin:list:{category}:{page+1}'))
        if navigation:
            rows.append(navigation)
        rows.append([B(text='بازگشت به امور مالی', callback_data='fin:home')])
        await callback.message.answer(f'{CATEGORIES[category]} · {total} مدرک\nصفحهٔ {page+1}', reply_markup=K(inline_keyboard=rows))
    except (ValueError, IndexError):
        await callback.message.answer('مدرک یا درخواست معتبر نیست.')
    except Exception:
        await callback.message.answer('دریافت فایل ناموفق بود؛ مدارک حذف نشده‌اند. دوباره تلاش کنید.')


@router.message(FinanceState.upload)
async def upload_document(message, state, current_user):
    if message.chat.type != 'private' or not authorized(current_user):
        await state.clear()
        await message.answer('دسترسی امور مالی مجاز نیست.')
        return
    category = (await state.get_data()).get('finance_category')
    if category not in CATEGORIES:
        await state.clear()
        return
    photo = message.photo[-1] if message.photo else None
    file = photo or message.document
    if not file or (not photo and message.document.mime_type != 'application/pdf') or (file.file_size or 0) > 10*1024*1024:
        await message.answer('عکس یا فایل PDF معتبر با حجم حداکثر ۱۰ مگابایت بفرستید.')
        return
    async with AsyncSessionLocal() as session:
        session.add(Document(category=category, file_id=file.file_id, media_type='photo' if photo else 'document', file_name=None if photo else message.document.file_name, caption=(message.caption or '')[:2000], uploader_telegram_id=current_user.telegram_id, branch_id=current_user.managed_branch_id, chat_id=message.chat.id, message_id=message.message_id))
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
        except Exception:
            await session.rollback()
            await message.answer('ذخیرهٔ مدرک ناموفق بود؛ لطفاً دوباره ارسال کنید.')
            return
    await message.answer('مدرک ذخیره شد؛ می‌توانید مدرک بعدی را ارسال کنید.', reply_markup=K(inline_keyboard=[[B(text='فهرست مدارک', callback_data=f'fin:list:{category}:0')], [B(text='پایان آپلود', callback_data='fin:home')]]))


@router.callback_query(F.data.startswith('finaccess:'))
async def change_finance_access(callback, current_user):
    if not _is_admin(current_user):
        await callback.answer('دسترسی مجاز نیست.', show_alert=True)
        return
    try:
        _, identity, value = callback.data.split(':')
        if value not in {'0','1'}:
            raise ValueError
        async with AsyncSessionLocal() as session:
            user = await session.get(User, int(identity))
            if not user or _is_admin(user):
                raise ValueError
            user.accounting_access = value == '1'
            session.add(StaffActivity(actor_telegram_id=current_user.telegram_id, action='finance_permission', target_id=user.id, detail=value))
            await session.commit()
        await callback.answer('مجوز امور مالی ذخیره شد.')
        await callback.message.edit_reply_markup(reply_markup=None)
    except ValueError:
        await callback.answer('کاربر یا درخواست معتبر نیست.', show_alert=True)

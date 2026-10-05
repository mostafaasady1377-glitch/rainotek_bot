from html import escape
from sqlalchemy import select, func
from database.models import User, BotInteraction, PurchaseRequest
from bot.services.local_time import period_bounds, format_local

LABELS = {'daily': 'روزانه', 'weekly': 'هفتگی', 'monthly': 'ماهانه'}


async def entry_report(session, period='daily', page=0):
    start, end = period_bounds(period)
    visits = select(BotInteraction).where(BotInteraction.created_at >= start, BotInteraction.created_at < end)
    events = list((await session.scalars(visits)).all())
    new_filter = (User.first_seen_at >= start, User.first_seen_at < end)
    total = await session.scalar(select(func.count(User.id)).where(*new_filter)) or 0
    customers = await session.scalar(select(func.count(User.id)).where(*new_filter, User.role == 'customer')) or 0
    crm = await session.scalar(select(func.count(User.id)).where(User.joined_at >= start, User.joined_at < end)) or 0
    users = list((await session.scalars(select(User).where(*new_filter).order_by(User.first_seen_at.desc(), User.id).offset(page * 5).limit(5))).all())
    lines = [f'📈 <b>گزارش ورودی {LABELS[period]}</b>',
             f'🗓 {format_local(start)} تا {format_local(end)} (پایان بازه غیرشامل)',
             '',
             f'👥 کاربران جدید: <b>{total}</b>',
             f'🛍 مشتری جدید: <b>{customers}</b> | سایر کاربران: <b>{total-customers}</b>',
             f'📱 ثبت شماره در بازه: <b>{crm}</b>',
             f'🔄 مراجعه‌کنندگان یکتا: <b>{len({e.telegram_id for e in events})}</b>',
             f'💬 تعامل‌های ثبت‌شده: <b>{len(events)}</b>', '',
             'ثبت مراجعه از زمان فعال‌شدن این پنل با جزئیات آغاز شده است؛ جزئیات تعامل‌های قدیمی در دسترس نیست.', '',
             '<b>جزئیات کاربران جدید</b>']
    for user in users:
        actions = [event for event in events if event.telegram_id == user.telegram_id]
        orders = list((await session.scalars(select(PurchaseRequest).where(PurchaseRequest.customer_telegram_id == user.telegram_id, PurchaseRequest.created_at >= start, PurchaseRequest.created_at < end))).all())
        expert = await session.scalar(select(User).where(User.telegram_id == user.referrer_telegram_id)) if user.referrer_telegram_id else None
        lines += ['', f'👤 <b>{escape(user.first_name or user.full_name or "بدون نام")}</b>',
                  f'شناسه: <code>{user.telegram_id}</code> | نقش: {escape({"customer": "مشتری", "seller": "کارشناس فروش", "branch_manager": "مدیر فروش", "admin": "ادمین"}.get(user.role, user.role))}',
                  f'📱 {escape(user.phone_number or "شماره ثبت نشده")}',
                  f'ورود: {format_local(user.first_seen_at)}',
                  f'معرف: {escape((expert.full_name or expert.username or str(expert.telegram_id)) if expert else "ورود مستقیم / معرف در دسترس نیست")}',
                  f'تعامل: {len(actions)} | سفارش: {len(orders)}',
                  f'مدل و مشخصات: {sum(e.action == "product" for e in actions)} | شعب: {sum(e.action == "branch" for e in actions)} | جستجو: {sum(e.action == "search" for e in actions)}',
                  f'آخرین فعالیت: {format_local(max((e.created_at for e in actions), default=None))}']
        if orders:
            lines.append('وضعیت سفارش‌ها: ' + '، '.join(f'#{o.id}: {escape(o.status)}' for o in orders))
    if not users:
        lines += ['', 'کاربر جدیدی در این بازه ثبت نشده است.']
    return '\n'.join(lines), total

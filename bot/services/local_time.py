"""Database timestamps remain UTC; presentation and calendar bounds use Tehran."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import jdatetime

TEHRAN = ZoneInfo('Asia/Tehran')


def tehran_now():
    return datetime.now(timezone.utc).astimezone(TEHRAN)


def format_local(value):
    if not value:
        return 'ثبت نشده'
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    local = value.astimezone(TEHRAN)
    return jdatetime.datetime.fromgregorian(datetime=local).strftime('%Y/%m/%d ـ %H:%M')


def period_bounds(period='daily', now=None):
    local = (now or tehran_now()).astimezone(TEHRAN)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == 'weekly':
        start -= timedelta(days=(local.weekday() - 5) % 7)
        end = start + timedelta(days=7)
    elif period == 'monthly':
        jalali = jdatetime.date.fromgregorian(date=local.date())
        first = jdatetime.date(jalali.year, jalali.month, 1)
        next_month = jdatetime.date(jalali.year + (jalali.month == 12), 1 if jalali.month == 12 else jalali.month + 1, 1)
        start = datetime.combine(first.togregorian(), datetime.min.time(), TEHRAN)
        end = datetime.combine(next_month.togregorian(), datetime.min.time(), TEHRAN)
    else:
        end = start + timedelta(days=1)
    return tuple(value.astimezone(timezone.utc).replace(tzinfo=None) for value in (start, end))

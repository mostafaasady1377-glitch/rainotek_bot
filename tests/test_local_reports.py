import unittest
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from database.models import Base, User, BotInteraction
from bot.services.local_time import format_local, period_bounds
from bot.services.entry_reports import entry_report
from types import SimpleNamespace
from unittest.mock import AsyncMock
from bot.handlers.management_dashboard import entry_period


class LocalReportTests(unittest.IsolatedAsyncioTestCase):
    def test_new_year_and_tehran_midnight(self):
        self.assertEqual(format_local(datetime(2026, 3, 20, 20, 30)), '1405/01/01 ـ 00:00')
        start, end = period_bounds('daily', datetime(2026, 3, 20, 21, tzinfo=timezone.utc))
        self.assertEqual(start, datetime(2026, 3, 20, 20, 30))
        self.assertEqual((end-start).days, 1)

    def test_jalali_month_and_saturday_week(self):
        now = datetime(2026, 3, 22, 12, tzinfo=timezone.utc)
        start, end = period_bounds('monthly', now)
        self.assertTrue(format_local(start).startswith('1405/01/01'))
        self.assertTrue(format_local(end).startswith('1405/02/01'))
        week, _ = period_bounds('weekly', now)
        self.assertEqual(format_local(week), '1405/01/01 ـ 00:00')

    async def test_customer_cannot_open_admin_report_callback(self):
        callback = SimpleNamespace(data='mgmt:entries:monthly:0', answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()))
        await entry_period(callback, User(telegram_id=1, role='customer'))
        callback.message.answer.assert_not_awaited()
        self.assertTrue(callback.answer.await_args.kwargs['show_alert'])

    async def test_report_contains_customer_and_recorded_actions(self):
        engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                session.add(User(telegram_id=12, full_name='مشتری آزمایشی', first_seen_at=datetime.utcnow()))
                session.add_all([BotInteraction(telegram_id=12, action='product'), BotInteraction(telegram_id=12, action='branch')])
                await session.commit()
                report, total = await entry_report(session)
                self.assertEqual(total, 1)
                self.assertIn('مشتری آزمایشی', report)
                self.assertIn('مدل و مشخصات: 1 | شعب: 1', report)
                self.assertIn('تعامل: 2', report)
        finally:
            await engine.dispose()

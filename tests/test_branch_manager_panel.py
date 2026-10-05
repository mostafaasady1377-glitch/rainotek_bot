from tests.test_dashboard_cards import DashboardCardTests
from bot.handlers.branch_manager_panel import manager_data
from bot.handlers.role_panels import _manager_menu, _staff_menu, manager_entry
from database.models import User
from types import SimpleNamespace as N
from unittest.mock import AsyncMock


class BranchManagerTests(DashboardCardTests):
    async def test_admin_bound_branch_report_has_no_branch_picker(self):
        from bot.handlers.branch_manager_panel import manager_section
        from unittest.mock import patch
        owner = User(telegram_id=9, role='admin', view_panel='branch_manager', managed_branch_id=1)
        message = N(text='📦 مدل‌ها و موجودی شعبه', answer=AsyncMock())
        with patch('bot.handlers.branch_manager_panel.AsyncSessionLocal', self.factory):
            await manager_section(message, N(clear=AsyncMock()), owner)
        self.assertIn('موجودی: 5', message.answer.await_args.args[0])
        self.assertNotIn('شعبه را انتخاب', message.answer.await_args.args[0])

    def test_menus_are_different(self):
        manager = [b.text for row in _manager_menu().keyboard for b in row]
        seller = [b.text for row in _staff_menu(User(role='seller')).keyboard for b in row]
        self.assertIn('📊 عملکرد کارشناسان شعبه', manager)
        self.assertNotIn('📊 عملکرد کارشناسان شعبه', seller)
        for label in ('🔗 لینک معرفی من', '📋 رزروهای ارجاعی من', '👤 مشتریان جدید من', '📈 گزارش ورودی‌های من'):
            self.assertNotIn(label, manager)
        self.assertIn('📥 گزارش عملیات شعبه', manager)

    async def test_branch_orders_do_not_leak_other_branch(self):
        from database.models import PurchaseRequest
        async with self.factory() as session:
            session.add_all([
                PurchaseRequest(customer_telegram_id=1, customer_name='OwnCustomer', customer_phone='09120000000', laptop_id=1, branch_id=1),
                PurchaseRequest(customer_telegram_id=2, customer_name='OtherCustomer', customer_phone='09120000001', laptop_id=1, branch_id=2),
            ])
            await session.commit()
            for kind in ('orders', 'operations'):
                text, keyboard = await manager_data(session, User(role='branch_manager', managed_branch_id=1, telegram_id=30), 1, kind)
                self.assertNotIn('OtherCustomer', text)
                if kind == 'orders':
                    self.assertIn('OwnCustomer', text)

    async def test_stock_is_scoped_and_has_product_links(self):
        manager = User(role='branch_manager', managed_branch_id=1, telegram_id=20)
        async with self.factory() as session:
            text, keyboard = await manager_data(session, manager, 1, 'stock')
            self.assertIn('موجودی: 5', text)
            self.assertIn('قابل فروش: 3', text)
            with self.assertRaises(PermissionError):
                await manager_data(session, manager, 2, 'stock')
            with self.assertRaises(PermissionError):
                await manager_data(session, User(role='seller', telegram_id=1), 1, 'staff')

    async def test_performance_includes_only_branch_sellers(self):
        async with self.factory() as session:
            session.add_all([User(role='seller', managed_branch_id=1, telegram_id=20, full_name='OwnSeller'), User(role='seller', managed_branch_id=2, telegram_id=21, full_name='OtherSeller')])
            await session.commit()
            for period in ('daily','weekly','monthly'):
                text, keyboard = await manager_data(session, User(role='branch_manager', managed_branch_id=1, telegram_id=30), 1, 'staff', period)
                self.assertIn('OwnSeller', text)
                self.assertNotIn('OtherSeller', text)

    async def test_seller_cannot_enter_manager_panel(self):
        message = N(answer=AsyncMock())
        state = N(clear=AsyncMock())
        await manager_entry(message, state, User(role='seller', telegram_id=1))
        self.assertIn('ثبت نشده', message.answer.await_args.args[0])

    async def test_referral_orders_are_personal_to_seller(self):
        from database.models import PurchaseRequest
        from bot.handlers.purchase_request import referred_orders
        from unittest.mock import patch
        async with self.factory() as session:
            session.add_all([
                PurchaseRequest(customer_telegram_id=1, customer_name='MyReferral', customer_phone='09120000000', laptop_id=1, branch_id=1, referrer_telegram_id=20),
                PurchaseRequest(customer_telegram_id=2, customer_name='NotMyReferral', customer_phone='09120000001', laptop_id=1, branch_id=1, referrer_telegram_id=21),
            ])
            await session.commit()
        message = N(answer=AsyncMock())
        with patch('bot.handlers.purchase_request.AsyncSessionLocal', self.factory):
            await referred_orders(message, User(role='seller', telegram_id=20, is_active=True))
        text = message.answer.await_args.args[0]
        self.assertIn('MyReferral', text)
        self.assertNotIn('NotMyReferral', text)
        await referred_orders(message, User(role='branch_manager', telegram_id=20, is_active=True))
        self.assertIn('مجاز نیست', message.answer.await_args.args[0])

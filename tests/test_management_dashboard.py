import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.handlers import management_dashboard as dashboard
from bot.handlers import role_panels
from bot.middlewares.user_context import UserContextMiddleware
from database.models import Base, BotDailyVisit, Branch, BranchInventory, Laptop, LaptopBrand, StaffActivity, User


class ManagementDashboardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from bot.config import Settings
        settings_patch = patch.object(role_panels, 'get_settings', return_value=Settings(ADMIN_TELEGRAM_IDS=[901]))
        settings_patch.start()
        self.addCleanup(settings_patch.stop)
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_daily_visit_counts_unique_users_and_interactions(self):
        actor = SimpleNamespace(id=501, username='customer', full_name='Test Customer')
        handler = AsyncMock()
        with patch('bot.middlewares.user_context.AsyncSessionLocal', self.factory):
            middleware = UserContextMiddleware()
            await middleware(handler, SimpleNamespace(), {'event_from_user': actor})
            await middleware(handler, SimpleNamespace(), {'event_from_user': actor})
        async with self.factory() as session:
            visits = (await session.scalars(select(BotDailyVisit))).all()
            user = await session.scalar(select(User).where(User.telegram_id == actor.id))
        self.assertEqual(len(visits), 1)
        self.assertEqual(visits[0].interaction_count, 2)
        self.assertIsNotNone(user.first_seen_at)

    async def test_admin_can_view_staff_activity_without_exposing_it_to_customers(self):
        async with self.factory() as session:
            branch = Branch(name='Central', code='C')
            session.add(branch)
            await session.flush()
            staff = User(telegram_id=502, full_name='Staff', role='branch_manager', is_active=True, managed_branch_id=branch.id)
            session.add(staff)
            await session.flush()
            session.add(StaffActivity(actor_telegram_id=502, action='product_edit', target_id=1, detail='price', created_at=datetime.utcnow()))
            staff_id = staff.id
            await session.commit()
        callback = SimpleNamespace(data=f'mgmt:person:{staff_id}', answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()))
        with patch.object(dashboard, 'AsyncSessionLocal', self.factory):
            await dashboard.staff_detail(callback, User(telegram_id=900, role='customer'))
            callback.message.answer.assert_not_awaited()
            await dashboard.staff_detail(callback, User(telegram_id=901, role='admin'))
        self.assertIn('Staff', callback.message.answer.await_args.args[0])
        self.assertIn('Central', callback.message.answer.await_args.args[0])
        self.assertIn('ویرایش محصول: 1', callback.message.answer.await_args.args[0])
        callbacks = [b.callback_data for row in callback.message.answer.await_args.kwargs['reply_markup'].inline_keyboard for b in row if b.callback_data]
        self.assertIn(f'panel:role:{staff_id}:seller', callbacks)
        self.assertIn(f'panel:role:{staff_id}:branch_manager', callbacks)
        self.assertIn(f'panel:editaccess:{staff_id}:0', callbacks)

    async def test_daily_report_uses_only_recorded_visits(self):
        message = SimpleNamespace(answer=AsyncMock())
        with patch.object(dashboard, 'AsyncSessionLocal', self.factory):
            await dashboard.daily_entries(message, User(telegram_id=901, role='admin'))
        report = message.answer.await_args.args[0]
        self.assertIn('ثبت مراجعه از زمان فعال‌شدن این پنل', report)
        self.assertIn('۰', report.replace('0', '۰'))

    async def test_manager_promotion_requires_and_saves_a_branch(self):
        async with self.factory() as session:
            branch = Branch(name='Central', code='C2')
            target = User(telegram_id=605, role='customer', is_active=True)
            session.add_all([branch, target])
            await session.commit()
            branch_id, user_id = branch.id, target.id
        message = SimpleNamespace(answer=AsyncMock())
        callback = SimpleNamespace(data=f'panel:role:{user_id}:branch_manager', answer=AsyncMock(), message=message)
        admin = User(telegram_id=901, role='admin')
        with patch.object(role_panels, 'AsyncSessionLocal', self.factory):
            await role_panels.assign_staff_role(callback, admin)
            async with self.factory() as session:
                self.assertEqual((await session.get(User, user_id)).role, 'customer')
            callback.data = f'panel:branch:{user_id}:{branch_id}'
            await role_panels.assign_manager_branch(callback, admin)
        async with self.factory() as session:
            updated = await session.get(User, user_id)
            self.assertEqual((updated.role, updated.managed_branch_id), ('branch_manager', branch_id))

    async def test_product_lookup_accepts_catalog_brand_and_model_title(self):
        async with self.factory() as session:
            brand = LaptopBrand(name='Sony Vaio')
            session.add(brand)
            await session.flush()
            laptop = Laptop(brand_id=brand.id, model='Sony vaio', status='active')
            session.add(laptop)
            await session.commit()
            laptop_id = laptop.id
        message = SimpleNamespace(text='Sony Vaio Sony vaio', answer=AsyncMock())
        state = SimpleNamespace(clear=AsyncMock())
        with patch.object(role_panels, 'AsyncSessionLocal', self.factory):
            await role_panels.product_lookup(message, state, User(telegram_id=901, role='admin'))
        button = message.answer.await_args.kwargs['reply_markup'].inline_keyboard[0][0]
        self.assertEqual(button.callback_data, f'panel:edit:{laptop_id}')
        state.clear.assert_awaited_once()

    async def test_product_edit_lists_catalog_inventory_before_selection(self):
        async with self.factory() as session:
            branch = Branch(name='Central', code='EDIT')
            brand = LaptopBrand(name='Sony Vaio')
            session.add_all([branch, brand])
            await session.flush()
            laptop = Laptop(brand_id=brand.id, model='Sony vaio', status='active')
            session.add(laptop)
            await session.flush()
            session.add(BranchInventory(laptop_id=laptop.id, branch_id=branch.id, quantity=2, reserved_count=1))
            await session.commit()
            brand_id, laptop_id = brand.id, laptop.id
        message = SimpleNamespace(answer=AsyncMock())
        state = SimpleNamespace(clear=AsyncMock())
        with patch.object(role_panels, 'AsyncSessionLocal', self.factory):
            await role_panels.product_edit_start(message, state, User(telegram_id=901, role='admin'))
            brand_button = message.answer.await_args.kwargs['reply_markup'].inline_keyboard[0][0]
            self.assertEqual(brand_button.callback_data, f'panel:editbrand:{brand_id}:0')
            callback = SimpleNamespace(data=brand_button.callback_data, answer=AsyncMock(), message=SimpleNamespace(edit_text=AsyncMock()))
            await role_panels.edit_brand_products(callback, User(telegram_id=901, role='admin'))
        product_button = callback.message.edit_text.await_args.kwargs['reply_markup'].inline_keyboard[0][0]
        self.assertEqual(product_button.callback_data, f'panel:editmodel:{laptop_id}:0')
        self.assertIn('1 موجود', product_button.text)

    async def test_admin_panel_exit_returns_to_role_selection(self):
        message = SimpleNamespace(answer=AsyncMock())
        state = SimpleNamespace(clear=AsyncMock())
        await role_panels.panel_exit(message, state, User(telegram_id=901, role='admin', phone_number='09120000000', view_panel='admin', is_active=True))
        labels = [button.text for row in message.answer.await_args.kwargs['reply_markup'].keyboard for button in row]
        self.assertIn('📊 داشبورد CRM', labels)
        self.assertIn('🔙 انتخاب پنل', labels)
        self.assertNotIn('🔎 جستجوی هوشمند', labels)

    async def test_admin_menu_has_separate_role_selection_exit(self):
        labels = [button.text for row in role_panels._admin_menu().keyboard for button in row]
        self.assertIn('🔙 انتخاب پنل', labels)
        self.assertNotIn('بازگشت به منوی اصلی', labels)

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.online_support import installment_terms, installment_section, INSTALLMENT_SECTIONS
from bot.keyboards.reply_menus import main_menu_kb
from bot.services.sales_contact import sales_contact


class InstallmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_main_menu_and_submenu(self):
        self.assertIn('💳 شرایط خرید اقساطی', [b.text for row in main_menu_kb('customer').keyboard for b in row])
        message = SimpleNamespace(answer=AsyncMock())
        state = SimpleNamespace(clear=AsyncMock())
        with patch('bot.handlers.online_support.show_installment_models', AsyncMock()) as models:
            await installment_terms(message, state)
            models.assert_awaited_once_with(message)
        state.clear.assert_awaited_once()

    async def test_sections_keep_assigned_expert(self):
        token = sales_contact.set('09120000020')
        try:
            for section in INSTALLMENT_SECTIONS:
                callback = SimpleNamespace(data='installment:' + section, answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()))
                await installment_section(callback)
                self.assertIn('09120000020', callback.message.answer.await_args.args[0])
        finally:
            sales_contact.reset(token)

    async def test_unknown_section_is_rejected(self):
        callback = SimpleNamespace(data='installment:bad', answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()))
        await installment_section(callback)
        callback.message.answer.assert_not_awaited()


class InstallmentCalculationTests(unittest.IsolatedAsyncioTestCase):
    async def test_formula_and_live_price_changes(self):
        from bot.services.installment_calculator import installment_quote
        from bot.handlers.online_support import installment_model, show_installment_models
        from bot.keyboards.catalog_builder import laptop_detail_keyboard
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from database.models import Base, Laptop, LaptopBrand
        quote = installment_quote(30_000_000)
        self.assertEqual(quote['monthly_interest'], 750_000)
        self.assertEqual(quote['yearly_interest'], 9_000_000)
        self.assertEqual(quote['monthly_payment'], 3_250_000)
        for invalid in (None, 0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                installment_quote(invalid)
        engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                brand = LaptopBrand(name='Test'); session.add(brand); await session.flush()
                lap = Laptop(brand_id=brand.id, model='<model>', status='active', price=30_000_000)
                session.add(lap); await session.commit(); product_id = lap.id
            callback = SimpleNamespace(data=f'installment:model:{product_id}', answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()))
            token = sales_contact.set('09120000020')
            try:
                with patch('bot.handlers.online_support.AsyncSessionLocal', factory):
                    await show_installment_models(callback.message, 999)
                    self.assertIn(f'installment:model:{product_id}', str(callback.message.answer.call_args.kwargs['reply_markup']))
                    await installment_model(callback)
                    text = callback.message.answer.call_args.args[0]
                    self.assertIn('3,250,000', text)
                    self.assertIn('09120000020', text)
                    self.assertIn('&lt;model&gt;', text)
                    async with factory() as session:
                        lap = await session.get(Laptop, product_id); lap.price = 60_000_000; await session.commit()
                    await installment_model(callback)
                    self.assertIn('6,500,000', callback.message.answer.call_args.args[0])
                    async with factory() as session:
                        lap = await session.get(Laptop, product_id); lap.price = None; await session.commit()
                    await installment_model(callback)
                    self.assertIn('قیمت معتبر', callback.message.answer.call_args.args[0])
                    async with factory() as session:
                        lap = await session.get(Laptop, product_id); lap.status = 'sold'; await session.commit()
                    callback.message.answer.reset_mock()
                    await installment_model(callback)
                    callback.message.answer.assert_not_awaited()
                self.assertIn(f'installment:model:{product_id}', str(laptop_detail_keyboard(product_id)))
            finally:
                sales_contact.reset(token)
        finally:
            await engine.dispose()

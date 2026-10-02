import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.services.rhinotech_sheet_reader import RhinotechSheetReader as Reader
from bot.services.inventory_service import InventoryService
from database.models import Base, Laptop, BranchInventory, LaptopVariant, LaptopStaffOverride


def sheet(rows):
    return 'شعبه,مدل,پردازنده,رم,هارد,صفحه,گرافیک,قیمت\n,DELL,,,,,,\n' + '\n'.join(rows)


ROW = 'میرداماد,Latitude 5400,i5,8GB,256 SSD,14,Intel,50000'


class LiveSheetTests(unittest.IsolatedAsyncioTestCase):
    async def test_price_after_blank_column_matches_catalog(self):
        content = 'شعبه,مدل,پردازنده,رم,هارد,صفحه,گرافیک,,قیمت,\n,DELL,,,,,,,,\nمیرداماد,Latitude 5400,i5,8GB,256 SSD,14,Intel,,50000,'
        async with self.factory() as session:
            await Reader.sync_sheet_to_database(session, content)
        async with self.factory() as session:
            laptop = await session.scalar(select(Laptop))
            self.assertEqual(laptop.price, 50000000)
            self.assertEqual(laptop.ram, '8GB')

    async def test_staff_edits_survive_sheet_refresh(self):
        await self.sync([ROW])
        async with self.factory() as session:
            laptop = await session.scalar(select(Laptop))
            session.add(LaptopStaffOverride(laptop_id=laptop.id, ram='16GB', price=57000000, image_file_id='telegram-photo-id', edited_by=123))
            await session.commit()
        await self.sync([ROW.replace('50000', '60000')])
        async with self.factory() as session:
            laptop = await session.scalar(select(Laptop))
            variant = await session.scalar(select(LaptopVariant).where(LaptopVariant.legacy_laptop_id == laptop.id))
            self.assertEqual((laptop.ram, laptop.price, laptop.image_url), ('16GB', 57000000, 'tgfile:telegram-photo-id'))
            self.assertEqual((variant.ram, variant.price), ('16GB', 57000000))

    def test_staff_photo_takes_precedence(self):
        from bot.services.laptop_assets import get_laptop_photo_input
        self.assertEqual(get_laptop_photo_input('DELL', 'Latitude 5400', 'tgfile:telegram-photo-id'), 'telegram-photo-id')

    async def asyncSetUp(self):
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def sync(self, rows):
        async with self.factory() as session:
            return await Reader.sync_sheet_to_database(session, sheet(rows))

    async def test_duplicate_rows_and_distinct_configurations(self):
        result = await self.sync([ROW, ROW, ROW.replace('8GB', '16GB')])
        self.assertEqual(result['products_synced'], 2)
        async with self.factory() as session:
            laptops = (await session.scalars(select(Laptop))).all()
            self.assertEqual(len(laptops), 2)
            self.assertEqual(sum((await session.scalars(select(BranchInventory.quantity))).all()), 3)
            self.assertTrue(all(l.image_url is None for l in laptops))
            self.assertTrue(all(l.warranty == '۲ ماه ضمانت تست و تعویض راینوتک' for l in laptops))

    async def test_local_sale_survives_price_update_and_restart(self):
        await self.sync([ROW, ROW])
        async with self.factory() as session:
            stock = await session.scalar(select(BranchInventory).where(BranchInventory.quantity > 0))
            await InventoryService(session).deduct_stock(stock.laptop_id, stock.branch_id, 1, 99, 'sale')
        Reader._last_content_hash = None
        await self.sync([ROW.replace('50000', '60000')] * 2)
        async with self.factory() as session:
            laptop = await session.scalar(select(Laptop))
            self.assertEqual(laptop.price, 60000000)
            self.assertEqual(sum((await session.scalars(select(BranchInventory.quantity))).all()), 1)

    async def test_deletion_retires_history_and_edit_preserves_id(self):
        other = ROW.replace('5400', '5500')
        await self.sync([ROW, other])
        async with self.factory() as session:
            original = await session.scalar(select(Laptop.id).where(Laptop.model == 'Latitude 5400'))
        await self.sync([ROW.replace('8GB', '16GB')])
        async with self.factory() as session:
            edited = await session.scalar(select(Laptop).where(Laptop.model == 'Latitude 5400'))
            deleted = await session.scalar(select(Laptop).where(Laptop.model == 'Latitude 5500'))
            self.assertEqual(edited.id, original)
            self.assertEqual(edited.ram, '16GB')
            self.assertEqual(deleted.status, 'inactive')
            variant = await session.scalar(select(LaptopVariant).where(LaptopVariant.legacy_laptop_id == deleted.id))
            self.assertFalse(variant.is_active)

    async def test_invalid_response_does_not_commit_hash_or_stock(self):
        await self.sync([ROW])
        previous = Reader._last_content_hash
        async with self.factory() as session:
            with patch.object(Reader, 'fetch_sheet_csv', AsyncMock(return_value='<html>Login</html>')):
                result = await Reader.check_and_sync_changes(session)
            self.assertIn('error', result)
            self.assertEqual(Reader._last_content_hash, previous)
            self.assertFalse(Reader._last_sync_status)
            self.assertEqual(sum((await session.scalars(select(BranchInventory.quantity))).all()), 1)

    def test_missing_price_remains_unknown_and_source_specs_preserved(self):
        row = Reader.parse_csv(sheet([ROW.rsplit(',', 1)[0] + ',']))[0]
        self.assertEqual(row['price_tomans'], 0)
        self.assertEqual(row['condition'], 'ثبت نشده')
        self.assertEqual(row['ram'], '8GB')

    async def test_stock_conflict_rolls_back_catalog_and_baseline(self):
        await self.sync([ROW, ROW])
        async with self.factory() as session:
            stock = await session.scalar(select(BranchInventory).where(BranchInventory.quantity > 0))
            await InventoryService(session).deduct_stock(stock.laptop_id, stock.branch_id, 2, 99)
        with self.assertRaises(ValueError):
            await self.sync([ROW.replace('50000', '70000')])
        async with self.factory() as session:
            self.assertEqual((await session.scalar(select(Laptop))).price, 50000000)
            self.assertEqual(sum((await session.scalars(select(BranchInventory.quantity))).all()), 0)


    def test_unknown_branch_and_iphone_are_not_fabricated(self):
        from bot.services.branch_service import BranchService
        self.assertEqual(BranchService.normalize_branch_string('Health 80'), [])
        content = 'شعبه,مدل,پردازنده,رم,هارد,صفحه,گرافیک,قیمت\n,,,,IPHONE,,,\nHealth 80,13,Green,128,Health 80,Za,با رجیستری,161000'
        item = Reader.parse_csv(content)[0]
        self.assertEqual(item['storage'], '128GB')
        self.assertEqual(item['color'], 'Green')
        self.assertEqual(item['cpu'], '')
        self.assertEqual(item['branch_raw'], '')




    async def test_search_does_not_swallow_menu_or_fsm_input(self):
        from types import SimpleNamespace
        from aiogram.dispatcher.event.bases import SkipHandler
        from bot.handlers.catalog_browser import handle_free_text_query
        for text, active in [('💻 کاتالوگ و مشخصات', None), ('➖ خروج کالا', None), ('3', 'quantity')]:
            message=SimpleNamespace(text=text)
            state=SimpleNamespace(get_state=AsyncMock(return_value=active))
            with self.assertRaises(SkipHandler):
                await handle_free_text_query(message,state)


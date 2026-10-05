import unittest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from database.models import Base, LaptopBrand, Laptop, ProductImage, LaptopStaffOverride
from bot.services.product_media import save_product_photo


class ProductMediaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.factory() as session:
            session.add(LaptopBrand(id=1, name='test'))
            await session.flush()
            session.add(Laptop(id=1, brand_id=1, model='test'))
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_photo_without_sheet_credentials_and_replacement(self):
        async with self.factory() as session:
            await save_product_photo(session, 1, 'first-file-id', 20)
            await save_product_photo(session, 1, 'second-file-id', 20)
            await save_product_photo(session, 1, 'second-file-id', 20)
            laptop = await session.get(Laptop, 1)
            self.assertEqual(laptop.image_url, 'tgfile:second-file-id')
            override = await session.get(LaptopStaffOverride, 1)
            self.assertEqual(override.image_file_id, 'second-file-id')
            photos = list((await session.scalars(select(ProductImage))).all())
            self.assertEqual(len(photos), 2)
            self.assertEqual([p.telegram_file_id for p in photos if p.is_primary], ['second-file-id'])

    async def test_missing_product_or_invalid_photo_rejected(self):
        async with self.factory() as session:
            with self.assertRaises(ValueError):
                await save_product_photo(session, 99, 'file-id', 20)
            with self.assertRaises(ValueError):
                await save_product_photo(session, 1, '', 20)

    async def test_sheet_sync_preserves_corrected_branch_address(self):
        from bot.services.branch_service import BranchService
        async with self.factory() as session:
            branches = await BranchService.ensure_canonical_branches(session)
            branch = branches['mirdamad']
            branch.address = 'corrected address'
            branch.address_full = 'corrected full address'
            branch.latitude = 35.7
            branch.longitude = 51.4
            await session.commit()
            branches = await BranchService.ensure_canonical_branches(session)
            self.assertEqual(branches['mirdamad'].address, 'corrected address')
            self.assertEqual(branches['mirdamad'].address_full, 'corrected full address')
            self.assertEqual(branches['mirdamad'].latitude, 35.7)

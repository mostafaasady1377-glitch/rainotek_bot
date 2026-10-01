from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import hashlib
import unittest
from unittest.mock import AsyncMock, patch

from aiogram.types import FSInputFile
from PIL import Image
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from bot.services.model_photos import load_manifest, resolve_model_photo
from bot.services.laptop_assets import get_laptop_photo_input
from database.models import Base, LaptopBrand, Laptop


class ModelPhotoTests(unittest.TestCase):
    def test_registered_downloads_are_valid_and_unchanged(self):
        from bot.services.model_photos import ASSETS_DIR
        entries=load_manifest()
        self.assertGreaterEqual(len(entries),100)
        for entry in entries:
            with self.subTest(model=entry['query']):
                path=ASSETS_DIR/entry['file']
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),entry['sha256'])
                with Image.open(path) as image:
                    self.assertEqual(image.format,'JPEG')
                    self.assertGreaterEqual(min(image.size),240)
                self.assertTrue(entry['source']['page_url'].startswith('https://'))

    def test_exact_model_generation_and_screen_select_local_photo(self):
        photo=get_laptop_photo_input('Dell','5430',cpu='i5 3320M',screen='14')
        self.assertIsInstance(photo,FSInputFile)
        self.assertIsNone(get_laptop_photo_input('Dell','5430',cpu='i5-1235U',screen='14'))
        small=resolve_model_photo('Apple','macbook pro 2019',cpu='i9',screen='15.4 Retina')
        large=resolve_model_photo('Apple','macbook pro 2019',cpu='i9',screen='16.1 Retina')
        self.assertIsNotNone(small); self.assertIsNotNone(large)
        self.assertNotEqual(small,large)
        self.assertIsNone(resolve_model_photo('Apple','macbook pro 2019'))

    def test_unknown_model_and_unsafe_or_missing_manifest_file_are_not_replaced(self):
        self.assertIsNone(get_laptop_photo_input('NEC Japan','nec'))
        self.assertIsNone(get_laptop_photo_input('Asus','unregistered model'))
        for filename in ['../outside.jpg','missing.jpg']:
            with patch('bot.services.model_photos.load_manifest',return_value=[dict(status='reviewed',file=filename,identities=[dict(brand='A',model='B')])]):
                self.assertIsNone(resolve_model_photo('A','B'))


class PhotoCardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine=create_async_engine('sqlite+aiosqlite:///:memory:')
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory=async_sessionmaker(self.engine,expire_on_commit=False)
        async with self.factory() as session:
            brand=LaptopBrand(name='Dell'); session.add(brand); await session.flush()
            laptop=Laptop(brand_id=brand.id,model='5430',cpu='i5 3320M',screen_size='14',ram='8GB',storage='256GB SSD',status='active',price=10000000)
            session.add(laptop); await session.commit(); self.product_id=laptop.id

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_photo_is_sent_before_full_specifications(self):
        from bot.handlers.catalog_browser import show_laptop_card
        calls=[]
        async def photo(**kwargs): calls.append(('photo',kwargs))
        async def answer(text,**kwargs): calls.append(('text',text))
        message=SimpleNamespace(answer_photo=photo,answer=answer)
        with patch('bot.handlers.catalog_browser.AsyncSessionLocal',self.factory):
            await show_laptop_card(message,self.product_id)
        self.assertEqual([kind for kind,_ in calls],['photo','text'])
        self.assertIsInstance(calls[0][1]['photo'],FSInputFile)
        self.assertIn('تصویر کاتالوگی',calls[0][1]['caption'])
        self.assertIn('i5 3320M',calls[1][1]); self.assertIn('8GB',calls[1][1])
        self.assertIn('۲ ماه',calls[1][1])

    async def test_photo_failure_keeps_the_product_information_accessible(self):
        from bot.handlers.catalog_browser import show_laptop_card
        message=SimpleNamespace(answer_photo=AsyncMock(side_effect=OSError('network')),answer=AsyncMock())
        with patch('bot.handlers.catalog_browser.AsyncSessionLocal',self.factory):
            await show_laptop_card(message,self.product_id)
        self.assertIn('i5 3320M',message.answer.call_args.args[0])
        self.assertIn('قابل ارسال نیست',message.answer.call_args.args[0])

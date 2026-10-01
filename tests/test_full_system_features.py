from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import (
    Base,
    Branch,
    BranchInventory,
    BranchStock,
    Laptop,
    LaptopBrand,
    LaptopModel,
    LaptopSeries,
    LaptopVariant,
    PurchaseRequest,
    User,
)
from bot.services.branch_service import BranchService, RHINOTECH_BRANCHES
from bot.services.ai_search_service import AISearchService
from bot.services.laptop_assets import get_laptop_photo_input, get_laptop_image_source
from bot.keyboards.catalog_builder import (
    paginated_catalog_keyboard,
    laptop_detail_keyboard,
    branches_menu_keyboard,
    smart_search_menu_keyboard,
)


class SystemFullFeaturesTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_canonical_branches_initialization(self):
        """تست راه‌اندازی و جامعیت ۵ شعبه اصلی راینوتک"""
        async with self.session_factory() as session:
            branch_map = await BranchService.ensure_canonical_branches(session)
            self.assertEqual(len(branch_map), 5)
            self.assertIn("mirdamad", branch_map)
            self.assertIn("sadeghiyeh", branch_map)
            self.assertIn("heravi", branch_map)
            self.assertIn("shahrak", branch_map)
            self.assertIn("fallah", branch_map)

            mirdamad = branch_map["mirdamad"]
            self.assertIn("رز", mirdamad.address)
            self.assertIn("021-26401850", mirdamad.phone)
            self.assertIsNotNone(mirdamad.latitude)
            self.assertIsNotNone(mirdamad.longitude)
            self.assertTrue(mirdamad.is_active)

            sadeghiyeh = branch_map["sadeghiyeh"]
            self.assertIn("گلدیس", sadeghiyeh.address)
            self.assertIn("021-44287050", sadeghiyeh.phone)

    async def test_branch_string_normalization(self):
        """تست نرمال‌سازی اسامی شعب و نام‌های مرکب"""
        self.assertEqual(BranchService.normalize_branch_string("صادقيه"), ["sadeghiyeh"])
        self.assertEqual(BranchService.normalize_branch_string("lمیرداماد"), ["mirdamad"])
        self.assertEqual(BranchService.normalize_branch_string("هروي"), ["heravi"])
        self.assertEqual(BranchService.normalize_branch_string("شهرک"), ["shahrak"])
        self.assertEqual(BranchService.normalize_branch_string("فلاح"), ["fallah"])

        multi_1 = BranchService.normalize_branch_string("شهرک/صادقیه")
        self.assertTrue("shahrak" in multi_1 and "sadeghiyeh" in multi_1)

        multi_2 = BranchService.normalize_branch_string("هروی/میرداماد")
        self.assertTrue("heravi" in multi_2 and "mirdamad" in multi_2)

        multi_3 = BranchService.normalize_branch_string("فلاح-هروی")
        self.assertTrue("fallah" in multi_3 and "heravi" in multi_3)

    async def test_ai_search_service_parsing(self):
        """تست استخراج معنایی و نیات کاربر در دستیار هوشمند"""
        # ۱. جستجوی بودجه و برند
        res1 = AISearchService.parse_query_intent("لپ تاپ ایسوس تا ۵۰ میلیون برای برنامه نویسی")
        self.assertEqual(res1["brand"], "asus")
        self.assertEqual(res1["max_price"], 50_000_000)

        # ۲. جستجوی رم و پردازنده
        res2 = AISearchService.parse_query_intent("سرفیس i7 با رم 16 گیگ")
        self.assertEqual(res2["brand"], "microsoft")
        self.assertEqual(res2["cpu_family"], "i7")
        self.assertEqual(res2["min_ram"], 16)
        self.assertTrue(res2["is_touch"])

        # ۳. جستجوی گیمینگ
        res3 = AISearchService.parse_query_intent("لپ‌تاپ گیمینگ لنوو با rtx 4060")
        self.assertEqual(res3["brand"], "lenovo")
        self.assertTrue(res3["is_gaming"])

        # ۴. جستجوی مک‌بوک و بازه قیمت
        res4 = AISearchService.parse_query_intent("مک بوک پرو بین ۶۰ تا ۱۰۰ تومن")
        self.assertEqual(res4["brand"], "apple")
        self.assertEqual(res4["min_price"], 60_000_000)
        self.assertEqual(res4["max_price"], 100_000_000)

        # ۵. جستجوی شعبه
        res5 = AISearchService.parse_query_intent("لپتاپ موجود در شعبه میرداماد")
        self.assertEqual(res5["branch_key"], "میرداماد")

    async def test_laptop_assets_resolution(self):
        """تست نگاشت و اختصاص صحیح تصاویر برای همه برندها"""
        k_mac, url_mac, local_mac = get_laptop_image_source("Apple", "MacBook Pro 16 2019")
        self.assertIn("apple", k_mac)
        self.assertTrue(url_mac.startswith("http"))

        k_tuf, url_tuf, _ = get_laptop_image_source("Asus", "TUF Gaming A15")
        self.assertIn("tuf", k_tuf)

        k_surf, url_surf, _ = get_laptop_image_source("Microsoft", "Surface Pro 7")
        self.assertIn("surface", k_surf)

        k_iphone, url_iphone, _ = get_laptop_image_source("Apple iPhone", "iPhone 14 Max")
        self.assertIn("iphone", k_iphone)

        input_file = get_laptop_photo_input("Asus", "ROG Strix G16")
        self.assertIsNone(input_file)

    async def test_keyboards_structure(self):
        """تست ساختار و کلیدهای ناوبری کاتالوگ و شعب"""
        items = [(f"Item {i}", f"cb:{i}") for i in range(15)]
        kb = paginated_catalog_keyboard(items, page=0, total=15, back=True)
        self.assertGreater(len(kb.inline_keyboard), 0)

        detail_kb = laptop_detail_keyboard(laptop_id=10, brand_id=2)
        has_order = any("order:laptop:10" in btn.callback_data for row in detail_kb.inline_keyboard for btn in row)
        has_branches = any("branches:laptop:10" in btn.callback_data for row in detail_kb.inline_keyboard for btn in row)
        self.assertTrue(has_order)
        self.assertTrue(has_branches)

        search_kb = smart_search_menu_keyboard()
        self.assertGreaterEqual(len(search_kb.inline_keyboard), 4)

    async def test_branch_inventory_summary(self):
        """تست واکشی آمار موجودی یک شعبه"""
        async with self.session_factory() as session:
            branches = await BranchService.ensure_canonical_branches(session)
            mir_branch = branches["mirdamad"]

            brand = LaptopBrand(name="Asus")
            session.add(brand)
            await session.flush()

            laptop = Laptop(
                brand_id=brand.id,
                model="TUF A15",
                price=55000000,
                status="active",
            )
            session.add(laptop)
            await session.flush()

            inv = BranchInventory(
                laptop_id=laptop.id,
                branch_id=mir_branch.id,
                quantity=3,
            )
            session.add(inv)
            await session.commit()

            summary = await BranchService.get_branch_inventory_summary(session, mir_branch.id)
            self.assertEqual(summary["total_items"], 3)
            self.assertIn("Asus", summary["brands"])
            self.assertEqual(summary["brands"]["Asus"], 3)
            self.assertEqual(len(summary["items"]), 1)

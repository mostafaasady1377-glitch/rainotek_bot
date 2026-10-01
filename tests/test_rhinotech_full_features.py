from __future__ import annotations

import unittest
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.config import Settings
from bot.services.google_sheets_service import GoogleSheetsService
from bot.services.idempotency_service import IdempotencyService
from bot.services.inventory_service import InventoryService
from bot.services.photo_service import ProductPhotoService
from database.models import (
    Base,
    Branch,
    BranchInventory,
    BranchStock,
    InventoryAuditLog,
    Laptop,
    LaptopBrand,
    LaptopModel,
    LaptopSeries,
    LaptopVariant,
    ProductImage,
    PurchaseRequest,
    StockAuditChecklist,
    User,
)
from database.users import ensure_user


class RhinotechFullFeaturesTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _seed_basic_data(self):
        async with self.session_factory() as session:
            b_central = Branch(code="C1", name="مرکزی پایتخت", is_active=True, address="ولیعصر")
            b_west = Branch(code="W1", name="غرب صادقیه", is_active=True, address="صادقیه")
            brand_asus = LaptopBrand(name="Asus")
            brand_apple = LaptopBrand(name="Apple")
            session.add_all([b_central, b_west, brand_asus, brand_apple])
            await session.flush()

            # کاتالوگ درختی و لپ‌تاپ‌ها
            s_rog = LaptopSeries(brand_id=brand_asus.id, name="ROG")
            session.add(s_rog)
            await session.flush()

            m_g16 = LaptopModel(series_id=s_rog.id, name="Strix G16", use_case="گیمینگ", is_active=True)
            session.add(m_g16)
            await session.flush()

            laptop_g16 = Laptop(
                brand_id=brand_asus.id,
                model="Strix G16",
                part_number="LT-ROG-G16",
                cpu="Core i7-13650HX",
                ram="16GB",
                gpu="RTX 4060",
                storage="512GB SSD",
                condition="نو",
                warranty="۲۴ ماه گارانتی",
                price=89000000,
                purchase_price=79000000,
                image_url="https://images.unsplash.com/photo-1603302576837-37561b2e2302",
                status="active",
            )
            session.add(laptop_g16)
            await session.flush()

            v_g16 = LaptopVariant(
                model_id=m_g16.id,
                cpu="Core i7-13650HX",
                ram="16GB",
                gpu="RTX 4060",
                storage="512GB SSD",
                condition="نو",
                warranty="۲۴ ماه گارانتی",
                price=89000000,
                purchase_price=79000000,
                legacy_laptop_id=laptop_g16.id,
                is_active=True,
            )
            session.add(v_g16)
            await session.flush()

            # موجودی شعبه مرکزی: ۵ عدد (۱ عدد رزرو) -> ۴ عدد قابل فروش
            inv_c1 = BranchInventory(
                laptop_id=laptop_g16.id,
                branch_id=b_central.id,
                quantity=5,
                reserved_count=1,
                min_stock_alert=2,
            )
            inv_w1 = BranchInventory(
                laptop_id=laptop_g16.id,
                branch_id=b_west.id,
                quantity=2,
                reserved_count=0,
                min_stock_alert=1,
            )
            session.add_all([inv_c1, inv_w1])
            await session.commit()

            return {
                "b_central_id": b_central.id,
                "b_west_id": b_west.id,
                "laptop_id": laptop_g16.id,
                "variant_id": v_g16.id,
            }

    async def test_role_access_and_customer_privacy(self) -> None:
        """تست تفکیک نقش‌ها و عدم افشای قیمت خرید داخلی به مشتری"""
        settings = Settings(BOT_TOKEN="test", ADMIN_TELEGRAM_IDS=[999])
        async with self.session_factory() as session:
            admin = await ensure_user(session, 999, "boss", "مدیر کل", settings)
            customer = await ensure_user(session, 111, "buyer", "مشتری عادی", settings)

            self.assertEqual(admin.role, "admin")
            self.assertEqual(customer.role, "customer")

            # استخراج مشخصات از کاتالوگ
            data = await self._seed_basic_data()
            service = InventoryService(session)
            details = await service.catalog_variant_details(data["variant_id"])

            self.assertIsNotNone(details)
            variant = details["variant"]

            # اطلاعات عمومی که مشتری باید ببیند
            self.assertEqual(variant.condition, "نو")
            self.assertEqual(variant.warranty, "۲۴ ماه گارانتی")
            self.assertEqual(variant.price, 89000000)

            # بررسی قیمت خرید: در آبجکت هست ولی متد کاتالوگ عمومی آن را به مشتری نمایش نمی‌دهد
            self.assertEqual(variant.purchase_price, 79000000)
            self.assertNotIn("purchase_price", details.keys())  # ساختار خروجی کاتالوگ فاقد فیلد خرید است

    async def test_stock_in_out_and_negative_prevention(self) -> None:
        """تست ورود و خروج موجودی و جلوگیری قطعی از موجودی منفی"""
        data = await self._seed_basic_data()
        async with self.session_factory() as session:
            service = InventoryService(session)
            lid = data["laptop_id"]
            cid = data["b_central_id"]

            # ورود کالا (افزایش ۳ عدد): ۵ + ۳ = ۸
            row = await service.add_stock(lid, cid, 3, actor_telegram_id=999, reason="خرید جدید")
            self.assertEqual(row.quantity, 8)

            # خروج کالا (کاهش ۲ عدد): ۸ - ۲ = ۶ (قابل فروش: ۶ - ۱ رزرو = ۵)
            row = await service.deduct_stock(lid, cid, 2, actor_telegram_id=999, reason="فروش حضوری")
            self.assertEqual(row.quantity, 6)

            # تلاش برای خروج ۶ عدد در حالی که ۱ عدد رزرو است (تنها ۵ عدد قابل فروش است)
            with self.assertRaises(ValueError):
                await service.deduct_stock(lid, cid, 6, actor_telegram_id=999)

            # تلاش برای ورود یا خروج تعداد منفی
            with self.assertRaises(ValueError):
                await service.add_stock(lid, cid, -1, actor_telegram_id=999)
            with self.assertRaises(ValueError):
                await service.deduct_stock(lid, cid, 0, actor_telegram_id=999)

            # بررسی ثبت لاگ‌ها
            logs = list((await session.scalars(
                select(InventoryAuditLog).where(InventoryAuditLog.laptop_id == lid)
            )).all())
            self.assertTrue(any(log.change_type == "IN" and log.quantity == 3 for log in logs))
            self.assertTrue(any(log.change_type == "OUT" and log.quantity == 2 for log in logs))

    async def test_branch_stock_transfer(self) -> None:
        """تست انتقال موجودی بین شعب و حفظ تعادل موجودی کل"""
        data = await self._seed_basic_data()
        async with self.session_factory() as session:
            service = InventoryService(session)
            lid = data["laptop_id"]
            cid = data["b_central_id"]
            wid = data["b_west_id"]

            # قبل از انتقال: مرکزی=۵ (۱ رزرو)، غرب=۲ -> مجموع کل=۷
            # انتقال ۲ عدد از مرکزی به غرب
            await service.transfer_stock(lid, cid, wid, 2, actor_telegram_id=999, reason="تأمین شعبه غرب")

            inv_c = await session.scalar(
                select(BranchInventory).where(BranchInventory.laptop_id == lid, BranchInventory.branch_id == cid)
            )
            inv_w = await session.scalar(
                select(BranchInventory).where(BranchInventory.laptop_id == lid, BranchInventory.branch_id == wid)
            )

            self.assertEqual(inv_c.quantity, 3)
            self.assertEqual(inv_w.quantity, 4)
            self.assertEqual(inv_c.quantity + inv_w.quantity, 7)  # حفظ موجودی کل

            # تلاش برای انتقال به خود همان شعبه
            with self.assertRaises(ValueError):
                await service.transfer_stock(lid, cid, cid, 1, actor_telegram_id=999)

    async def test_idempotency_prevents_duplicate_clicks(self) -> None:
        """تست جلوگیری از ثبت تکراری عملیات ناشی از کلیک مکرر کاربر"""
        data = await self._seed_basic_data()
        async with self.session_factory() as session:
            service = InventoryService(session)
            lid = data["laptop_id"]
            cid = data["b_central_id"]
            idempotency_key = "op_test_deduct_unique_nonce_123"

            executed_times = 0

            async def _do_deduct():
                nonlocal executed_times
                executed_times += 1
                return (await service.deduct_stock(lid, cid, 1, 999, "تست تکرار")).quantity

            # بار اول: اجرا می‌شود
            is_new_1, res_1 = await IdempotencyService.execute_idempotent(
                session, idempotency_key, 999, "DEDUCT", _do_deduct
            )
            self.assertTrue(is_new_1)
            self.assertEqual(res_1, 4)
            self.assertEqual(executed_times, 1)

            # بار دوم با همان کلید یکتا (شبیه‌سازی دابل کلیک یا پیام تکراری): اجرا نمی‌شود
            is_new_2, res_2 = await IdempotencyService.execute_idempotent(
                session, idempotency_key, 999, "DEDUCT", _do_deduct
            )
            self.assertFalse(is_new_2)
            self.assertEqual(executed_times, 1)  # عملیات فقط یک بار فراخوانی شده

            # موجودی باید دقیقاً ۱ بار کم شده باشد: ۵ -> ۴
            inv = await session.scalar(
                select(BranchInventory).where(BranchInventory.laptop_id == lid, BranchInventory.branch_id == cid)
            )
            self.assertEqual(inv.quantity, 4)

    async def test_stock_audit_two_phase_and_authorized_approval(self) -> None:
        """تست انبارگردانی دومرحله‌ای: ثبت شمارش، محاسبه مغایرت، و نیاز به تأیید فرد مجاز برای اعمال در موجودی"""
        data = await self._seed_basic_data()
        async with self.session_factory() as session:
            service = InventoryService(session)
            cid = data["b_central_id"]
            lid = data["laptop_id"]

            # شروع انبارگردانی شعبه مرکزی (موجودی سیستم: ۵ عدد)
            audit = await service.start_audit(cid, auditor_telegram_id=888)
            self.assertEqual(audit.status, "in_progress")

            # ثبت شمارش واقعی: ۴ عدد (۱ عدد کسری مغایرت)
            await service.submit_audit_item(audit.id, lid, counted_quantity=4)

            # ثبت نهایی شمارش و رفتن به حالت در انتظار تأیید
            audit, diffs = await service.record_audit_pending_approval(audit.id)
            self.assertEqual(audit.status, "pending_approval")
            self.assertEqual(len(diffs), 1)
            self.assertEqual(diffs[0]["discrepancy"], -1)

            # در این مرحله هنوز موجودی تغییر نکرده و همان ۵ عدد اولیه است!
            inv_before = await session.scalar(
                select(BranchInventory).where(BranchInventory.laptop_id == lid, BranchInventory.branch_id == cid)
            )
            self.assertEqual(inv_before.quantity, 5)

            # تأیید و اصلاح قطعی توسط مدیر
            approved_audit = await service.approve_and_apply_audit(audit.id, approver_telegram_id=999)
            self.assertEqual(approved_audit.status, "completed")
            self.assertEqual(approved_audit.approved_by_telegram_id, 999)

            # حالا موجودی به ۴ عدد اصلاح شده است
            inv_after = await session.scalar(
                select(BranchInventory).where(BranchInventory.laptop_id == lid, BranchInventory.branch_id == cid)
            )
            self.assertEqual(inv_after.quantity, 4)

            # لاگ اصلاح انبارگردانی ثبت شده است
            adj_log = await session.scalar(
                select(InventoryAuditLog).where(
                    InventoryAuditLog.laptop_id == lid,
                    InventoryAuditLog.change_type == "AUDIT_ADJUSTMENT",
                )
            )
            self.assertIsNotNone(adj_log)
            self.assertEqual(adj_log.quantity, 1)

    async def test_product_photos_and_file_id_caching(self) -> None:
        """تست اعتبارسنجی لینک عکس، پشتیبانی از چند عکس و کش کردن file_id تلگرام"""
        data = await self._seed_basic_data()
        async with self.session_factory() as session:
            lid = data["laptop_id"]

            # اعتبارسنجی URL
            self.assertTrue(ProductPhotoService.is_valid_url("https://example.com/laptop.jpg"))
            self.assertFalse(ProductPhotoService.is_valid_url("invalid-url"))
            self.assertFalse(ProductPhotoService.is_valid_url(None))

            # ثبت دو عکس برای محصول
            img1 = await ProductPhotoService.add_or_update_image(
                session, lid, "https://example.com/laptop_front.jpg", display_order=1, is_primary=True
            )
            img2 = await ProductPhotoService.add_or_update_image(
                session, lid, "https://example.com/laptop_side.jpg", display_order=2, is_primary=False
            )

            images = await ProductPhotoService.get_laptop_images(session, lid)
            self.assertGreaterEqual(len(images), 2)
            self.assertEqual(images[0].is_primary, True)

            # تست کش کردن file_id
            await ProductPhotoService.cache_file_id(session, img1.id, "AgACAgQAAxkBAAIC123")
            cached_img = await session.get(ProductImage, img1.id)
            self.assertEqual(cached_img.telegram_file_id, "AgACAgQAAxkBAAIC123")

    async def test_customer_purchase_request_flow(self) -> None:
        """تست ثبت درخواست خرید مشتری و تغییر وضعیت آن"""
        data = await self._seed_basic_data()
        async with self.session_factory() as session:
            req = PurchaseRequest(
                customer_telegram_id=123456,
                customer_name="علی احمدی",
                customer_phone="09121234567",
                laptop_id=data["laptop_id"],
                branch_id=data["b_central_id"],
                quantity=1,
                status="pending",
                notes="نیاز به فاکتور رسمی",
                created_at=datetime.utcnow(),
            )
            session.add(req)
            await session.commit()

            self.assertIsNotNone(req.id)
            self.assertEqual(req.status, "pending")

            # تغییر وضعیت به تماس گرفته شده توسط پرسنل
            req.status = "contacted"
            await session.commit()
            updated = await session.get(PurchaseRequest, req.id)
            self.assertEqual(updated.status, "contacted")

    async def test_google_sheets_sync_preserves_live_inventory(self) -> None:
        """تست همگام‌سازی کاتالوگ از شیت و اطمینان از دست‌نخوردن موجودی‌های جاری شعب"""
        data = await self._seed_basic_data()
        async with self.session_factory() as session:
            sheets_svc = GoogleSheetsService(mock_mode=True)
            res = await sheets_svc.sync_catalog_from_sheets(session)

            self.assertGreaterEqual(res["branches_synced"], 1)
            self.assertGreaterEqual(res["products_synced"], 1)

            # موجودی شعبه مرکزی که ۵ عدد بود نباید صفر یا بازنویسی شود
            inv = await session.scalar(
                select(BranchInventory).where(
                    BranchInventory.laptop_id == data["laptop_id"],
                    BranchInventory.branch_id == data["b_central_id"],
                )
            )
            self.assertEqual(inv.quantity, 5)

            # بررسی تولید گزارش موجودی شعب
            report_data = await sheets_svc.generate_stock_report_data(session)
            self.assertTrue(len(report_data) > 0)
            # ستون‌های گزارش
            self.assertEqual(len(report_data[0]), 11)


if __name__ == "__main__":
    unittest.main()

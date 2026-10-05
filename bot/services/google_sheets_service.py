from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Optional

import aiohttp
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from database.models import (
    Branch,
    BranchInventory,
    BranchStock,
    InventoryAuditLog,
    Laptop,
    LaptopBrand,
    LaptopModel,
    LaptopSeries,
    LaptopVariant,
    PurchaseRequest,
)
from bot.services.photo_service import ProductPhotoService

# ساختار استاندارد و پیشنهادی شیت‌ها و ستون‌ها
SHEET_SPECIFICATIONS = {
    "محصولات": [
        "کد کالا", "برند", "سری", "مدل", "پردازنده", "رم", "حافظه", "گرافیک",
        "وضعیت", "گارانتی", "قیمت فروش (تومان)", "قیمت خرید (تومان)",
        "لینک عکس ۱", "لینک عکس ۲", "لینک عکس ۳", "کاربری / توضیحات", "وضعیت نمایش"
    ],
    "شعب": [
        "کد شعبه", "نام شعبه", "تلفن تماس", "آدرس کامل",
        "عرض جغرافیایی (Latitude)", "طول جغرافیایی (Longitude)", "وضعیت"
    ],
    "موجودی_اولیه": [
        "کد کالا", "کد شعبه", "موجودی اولیه", "حداقل هشدار موجودی"
    ],
    "گزارش_موجودی_شعب": [
        "کد کالا", "برند", "مدل", "کانفیگ", "کد شعبه", "نام شعبه",
        "موجودی کل", "تعداد رزرو", "موجودی قابل فروش", "وضعیت هشدار", "آخرین بروزرسانی"
    ],
    "گردش_کالا_و_تراکنش‌ها": [
        "شناسه لاگ", "زمان شمسی (تهران)", "کد کالا", "مدل کالا",
        "شعبه مبدا", "شعبه مقصد", "نوع عملیات", "تعداد", "شناسه کاربر", "دلیل"
    ],
    "درخواست‌های_خرید_مشتریان": [
        "شماره سفارش", "زمان ثبت", "شناسه تلگرام مشتری", "نام مشتری",
        "شماره تماس", "مدل درخواستی", "شعبه انتخابی", "تعداد", "وضعیت", "توضیحات"
    ],
}

SAMPLE_PRODUCTS_DATA = [
    [
        "LT-ASUS-G16-01", "Asus", "ROG Strix", "ROG Strix G16", "Core i7-13650HX", "16GB DDR5", "512GB NVMe SSD", "RTX 4060 8GB",
        "نو", "۲۴ ماه گارانتی یکپارچه حامی/آواژنگ", 89000000, 79000000,
        "https://images.unsplash.com/photo-1603302576837-37561b2e2302",
        "https://images.unsplash.com/photo-1588872657578-7efd1f1555ed",
        "", "گیمینگ و مهندسی سنگین", "فعال"
    ],
    [
        "LT-LEN-X1-02", "Lenovo", "ThinkPad", "ThinkPad X1 Carbon Gen 10", "Core i7-1260P", "16GB LPDDR5", "1TB NVMe SSD", "Intel Iris Xe",
        "کارکرده (استوک Grade A+)", "۶ ماه مهلت تست و گارانتی راینوتک", 48000000, 41000000,
        "https://images.unsplash.com/photo-1541807084-5c52b6b3adef",
        "", "", "مدیریتی، برنامه‌نویسی و اداری سبک‌وزن", "فعال"
    ],
    [
        "LT-APP-M3P-03", "Apple", "MacBook Pro", "MacBook Pro 14 M3 Pro", "Apple M3 Pro (11-Core)", "18GB Unified", "512GB SSD", "14-Core GPU",
        "نو", "۱۸ ماه گارانتی شرکتی + مهلت تست ۱۰ روزه", 135000000, 122000000,
        "https://images.unsplash.com/photo-1517336714731-489689fd1ca8",
        "", "", "طراحی حرفه‌ای، گرافیک، تدوین و تدوین صوت", "فعال"
    ],
    [
        "LT-HP-VIC16-04", "HP", "Victus", "Victus 16", "Ryzen 7 7840HS", "16GB DDR5", "1TB NVMe SSD", "RTX 4060 8GB",
        "نو", "۱۸ ماه گارانتی الماس ایران", 76000000, 68000000,
        "https://images.unsplash.com/photo-1593642632823-8f785ba67e45",
        "", "", "گیمینگ اقتصادی و رندرینگ دانشجویی", "فعال"
    ],
]

SAMPLE_BRANCHES_DATA = [
    ["C1", "شعبه مرکزی راینوتک (پایتخت)", "021-88881234", "تهران، خیابان ولیعصر، تقاطع میرداماد، مجتمع پایتخت، ط ۲", 35.758, 51.411, "فعال"],
    ["N1", "شعبه شمال (اندرزگو)", "021-22225678", "تهران، بلوار اندرزگو، نبش خیابان شریفی، پلاک ۱۲", 35.801, 51.455, "فعال"],
    ["W1", "شعبه غرب (صادقیه)", "021-44449012", "تهران، فلکه دوم صادقیه، برج اداری تجاری گلدیس، ط ۴", 35.720, 51.335, "فعال"],
]

SAMPLE_INITIAL_STOCK = [
    ["LT-ASUS-G16-01", "C1", 5, 2],
    ["LT-ASUS-G16-01", "N1", 3, 1],
    ["LT-LEN-X1-02", "C1", 8, 2],
    ["LT-LEN-X1-02", "W1", 4, 1],
    ["LT-APP-M3P-03", "C1", 3, 1],
    ["LT-HP-VIC16-04", "N1", 6, 2],
]


class GoogleSheetsService:
    """
    سرویس اتصال، خواندن و نوشتن Google Sheets برای فروشگاه Rhinotech.
    - پایگاه داده (SQLite / PostgreSQL) منبع معتبر و نهایی (Source of Truth) برای موجودی شعب و تراکنش‌ها است.
    - شیت جهت تعریف کاتالوگ، شعب و دریافت گزارش‌های خروجی استفاده می‌شود.
    - همگام‌سازی کاتالوگ فقط مشخصات و قیمت را بروز می‌کند و موجودی فیزیکی تراکنش‌خورده را بازنویسی نمی‌کند.
    """

    def __init__(
        self,
        sheet_id: str | None = None,
        service_account_file: str | None = None,
        mock_mode: bool = False,
    ):
        self.sheet_id = sheet_id or os.getenv("GOOGLE_SHEET_ID", "")
        self.service_account_file = service_account_file or os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "")
        self.mock_mode = mock_mode or not (self.sheet_id and self.service_account_file and os.path.exists(self.service_account_file))

    def is_configured(self) -> bool:
        return bool(self.sheet_id and self.service_account_file and os.path.exists(self.service_account_file))

    @staticmethod
    def get_template_specifications() -> dict[str, list[str]]:
        """برگرداندن لیست برگهها و ستونهای تعریف شده"""
        return SHEET_SPECIFICATIONS

    async def fetch_sheet_rows(self, sheet_name: str) -> list[list[Any]]:
        """
        دریافت سطرهای یک برگه از شیت گوگل. در حالت Mock از داده‌های نمونه استفاده می‌شود.
        """
        if self.mock_mode:
            logger.info(f"خواندن برگه '{sheet_name}' در حالت آزمایشی (Mock Mode)")
            if sheet_name == "محصولات":
                return SAMPLE_PRODUCTS_DATA
            elif sheet_name == "شعب":
                return SAMPLE_BRANCHES_DATA
            elif sheet_name == "موجودی_اولیه":
                return SAMPLE_INITIAL_STOCK
            return []

        # در حالت اتصال واقعی:
        try:
            # بارگذاری توکن و ارتباط با Google Sheets API v4
            # در صورت عدم نصب کتابخانه‌های سنگین، از کلاینت استاندارد پشتیبانی می‌شود
            import gspread
            gc = gspread.service_account(filename=self.service_account_file)
            sh = gc.open_by_key(self.sheet_id)
            worksheet = sh.worksheet(sheet_name)
            return worksheet.get_all_values()[1:]  # بدون هدر
        except Exception as exc:
            logger.error(f"خطا در فراخوانی گوگل شیت {sheet_name}: {exc}")
            raise

    async def sync_catalog_from_sheets(self, session: AsyncSession) -> dict[str, int]:
        """
        همگام‌سازی محصولات و شعب از شیت گوگل به دیتابیس بدون دست‌زدن به موجودی جاری شعب.
        """
        # 1. همگام‌سازی شعب
        branch_rows = await self.fetch_sheet_rows("شعب")
        branches_synced = 0
        for row in branch_rows:
            if not row or len(row) < 2:
                continue
            code = str(row[0]).strip()
            name = str(row[1]).strip()
            phone = str(row[2]).strip() if len(row) > 2 and row[2] else None
            address = str(row[3]).strip() if len(row) > 3 and row[3] else None
            lat = float(row[4]) if len(row) > 4 and str(row[4]).replace(".", "", 1).isdigit() else None
            lng = float(row[5]) if len(row) > 5 and str(row[5]).replace(".", "", 1).isdigit() else None
            is_active = True if len(row) <= 6 or str(row[6]).strip() in ("فعال", "active", "1", "True") else False

            branch = await session.scalar(select(Branch).where(Branch.code == code))
            if branch is None:
                branch = Branch(
                    code=code,
                    name=name,
                    phone=phone,
                    address=address,
                    address_full=address,
                    latitude=lat,
                    longitude=lng,
                    is_active=is_active,
                )
                session.add(branch)
            else:
                branch.name = name
                branch.phone = phone
                branch.address = address
                branch.address_full = address
                branch.latitude = lat
                branch.longitude = lng
                branch.is_active = is_active
            branches_synced += 1

        await session.flush()

        # 2. همگام‌سازی محصولات
        product_rows = await self.fetch_sheet_rows("محصولات")
        products_synced = 0
        for row in product_rows:
            if not row or len(row) < 4:
                continue
            sku = str(row[0]).strip()
            brand_name = str(row[1]).strip()
            series_name = str(row[2]).strip() if row[2] else "عمومی"
            model_name = str(row[3]).strip()
            cpu = str(row[4]).strip() if len(row) > 4 and row[4] else None
            ram = str(row[5]).strip() if len(row) > 5 and row[5] else None
            storage = str(row[6]).strip() if len(row) > 6 and row[6] else None
            gpu = str(row[7]).strip() if len(row) > 7 and row[7] else None
            condition = str(row[8]).strip() if len(row) > 8 and row[8] else "نو"
            warranty = str(row[9]).strip() if len(row) > 9 and row[9] else None
            
            def parse_price(val: Any) -> int:
                if not val:
                    return 0
                clean = "".join(ch for ch in str(val) if ch.isdigit())
                return int(clean) if clean else 0

            price = parse_price(row[10]) if len(row) > 10 else 0
            purchase_price = parse_price(row[11]) if len(row) > 11 else 0
            img1 = str(row[12]).strip() if len(row) > 12 and row[12] else None
            img2 = str(row[13]).strip() if len(row) > 13 and row[13] else None
            img3 = str(row[14]).strip() if len(row) > 14 and row[14] else None
            description = str(row[15]).strip() if len(row) > 15 and row[15] else None
            status = "active" if len(row) <= 16 or str(row[16]).strip() in ("فعال", "active", "1") else "inactive"

            # برند
            brand = await session.scalar(select(LaptopBrand).where(func.lower(LaptopBrand.name) == brand_name.lower()))
            if brand is None:
                brand = LaptopBrand(name=brand_name)
                session.add(brand)
                await session.flush()

            # لپ‌تاپ (سطح پایه کاتالوگ)
            laptop = await session.scalar(
                select(Laptop).where(
                    Laptop.brand_id == brand.id,
                    Laptop.model == model_name,
                    Laptop.cpu == cpu,
                    Laptop.ram == ram,
                    Laptop.gpu == gpu,
                    Laptop.storage == storage,
                )
            )
            if laptop is None:
                laptop = Laptop(
                    brand_id=brand.id,
                    model=model_name,
                    part_number=sku,
                    cpu=cpu,
                    ram=ram,
                    gpu=gpu,
                    storage=storage,
                    condition=condition,
                    warranty=warranty,
                    price=price,
                    purchase_price=purchase_price,
                    image_url=img1,
                    status=status,
                )
                session.add(laptop)
                await session.flush()
            else:
                laptop.part_number = sku
                laptop.condition = condition
                laptop.warranty = warranty
                laptop.price = price
                laptop.purchase_price = purchase_price
                laptop.image_url = img1 or laptop.image_url
                laptop.status = status

            # عکس‌های چندگانه
            for order, img_url in enumerate([img1, img2, img3], start=1):
                if img_url and ProductPhotoService.is_valid_url(img_url):
                    await ProductPhotoService.add_or_update_image(
                        session=session,
                        laptop_id=laptop.id,
                        image_url=img_url,
                        display_order=order,
                        is_primary=order == 1,
                    )

            # سری و مدل درختی
            series = await session.scalar(
                select(LaptopSeries).where(LaptopSeries.brand_id == brand.id, LaptopSeries.name == series_name)
            )
            if series is None:
                series = LaptopSeries(brand_id=brand.id, name=series_name)
                session.add(series)
                await session.flush()

            tree_model = await session.scalar(
                select(LaptopModel).where(LaptopModel.series_id == series.id, LaptopModel.name == model_name)
            )
            if tree_model is None:
                tree_model = LaptopModel(series_id=series.id, name=model_name, description=description, is_active=True)
                session.add(tree_model)
                await session.flush()

            variant = await session.scalar(
                select(LaptopVariant).where(
                    LaptopVariant.model_id == tree_model.id,
                    LaptopVariant.cpu == cpu,
                    LaptopVariant.ram == ram,
                    LaptopVariant.gpu == gpu,
                    LaptopVariant.storage == storage,
                )
            )
            if variant is None:
                variant = LaptopVariant(
                    model_id=tree_model.id,
                    cpu=cpu,
                    ram=ram,
                    gpu=gpu,
                    storage=storage,
                    condition=condition,
                    warranty=warranty,
                    price=price,
                    purchase_price=purchase_price,
                    legacy_laptop_id=laptop.id,
                    is_active=True,
                )
                session.add(variant)
            else:
                variant.condition = condition
                variant.warranty = warranty
                variant.price = price
                variant.purchase_price = purchase_price

            products_synced += 1

        await session.commit()
        return {"branches_synced": branches_synced, "products_synced": products_synced}

    async def import_initial_stock(self, session: AsyncSession) -> int:
        """
        وارد کردن موجودی اولیه از شیت 'موجودی_اولیه'.
        تنها در صورتی موجودی تعیین می‌شود که برای آن شعبه هنوز موجودی تعریف نشده باشد
        یا مقدار فعلی صفر باشد، تا تراکنش‌های زنده بازنویسی نشوند.
        """
        rows = await self.fetch_sheet_rows("موجودی_اولیه")
        imported_count = 0
        for row in rows:
            if not row or len(row) < 3:
                continue
            sku = str(row[0]).strip()
            branch_code = str(row[1]).strip()
            qty = int(row[2]) if str(row[2]).isdigit() else 0
            min_alert = int(row[3]) if len(row) > 3 and str(row[3]).isdigit() else 0

            branch = await session.scalar(select(Branch).where(Branch.code == branch_code))
            laptop = await session.scalar(select(Laptop).where(Laptop.part_number == sku))
            if branch is None or laptop is None:
                continue

            inv = await session.scalar(
                select(BranchInventory).where(
                    BranchInventory.laptop_id == laptop.id,
                    BranchInventory.branch_id == branch.id,
                )
            )
            if inv is None:
                inv = BranchInventory(
                    laptop_id=laptop.id,
                    branch_id=branch.id,
                    quantity=qty,
                    reserved_count=0,
                    min_stock_alert=min_alert,
                )
                session.add(inv)
                imported_count += 1
            elif inv.quantity == 0:
                inv.quantity = qty
                inv.min_stock_alert = min_alert
                imported_count += 1

            # همگام با کاتالوگ درختی
            variants = list((await session.scalars(
                select(LaptopVariant).where(LaptopVariant.legacy_laptop_id == laptop.id)
            )).all())
            for var in variants:
                b_stock = await session.scalar(
                    select(BranchStock).where(BranchStock.variant_id == var.id, BranchStock.branch_id == branch.id)
                )
                if b_stock is None:
                    session.add(BranchStock(variant_id=var.id, branch_id=branch.id, quantity=qty))
                elif b_stock.quantity == 0:
                    b_stock.quantity = qty

        await session.commit()
        return imported_count

    async def generate_stock_report_data(self, session: AsyncSession) -> list[list[Any]]:
        """ایجاد داده‌های به‌روز گزارش موجودی تمام شعب برای ارسال به شیت یا CSV"""
        result = await session.execute(
            select(
                Laptop.part_number,
                LaptopBrand.name,
                Laptop.model,
                Laptop.cpu,
                Laptop.ram,
                Laptop.gpu,
                Laptop.storage,
                Branch.code,
                Branch.name,
                BranchInventory.quantity,
                BranchInventory.reserved_count,
                BranchInventory.min_stock_alert,
            )
            .join(LaptopBrand, LaptopBrand.id == Laptop.brand_id)
            .join(BranchInventory, BranchInventory.laptop_id == Laptop.id)
            .join(Branch, Branch.id == BranchInventory.branch_id)
            .where(Laptop.status == "active", Branch.is_active.is_(True))
            .order_by(Branch.name, Laptop.model)
        )
        from bot.services.local_time import format_local
        now_str = format_local(datetime.utcnow())
        report_rows = []
        for sku, brand, model, cpu, ram, gpu, storage, b_code, b_name, qty, res, min_alert in result.all():
            available = max(qty - res, 0)
            status_warning = "⚠️ کمبود موجودی" if available <= min_alert else "عادی"
            config_str = f"{cpu or '-'} / {ram or '-'} / {gpu or '-'} / {storage or '-'}"
            report_rows.append([
                sku or "-", brand, model, config_str, b_code, b_name,
                qty, res, available, status_warning, now_str
            ])
        return report_rows

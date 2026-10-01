from __future__ import annotations

import asyncio
import csv
import os
import sys
from pathlib import Path

# اطمینان از قرار داشتن مسیر ریشه پروژه در sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
    ProductImage,
    PurchaseRequest,
    User,
)
from database.session import AsyncSessionLocal, create_db, engine
from bot.services.google_sheets_service import (
    SAMPLE_BRANCHES_DATA,
    SAMPLE_INITIAL_STOCK,
    SAMPLE_PRODUCTS_DATA,
    SHEET_SPECIFICATIONS,
)


def export_csv_templates(output_dir: str = "sheets_templates") -> None:
    os.makedirs(output_dir, exist_ok=True)

    # 1. محصولات
    with open(os.path.join(output_dir, "محصولات.csv"), "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(SHEET_SPECIFICATIONS["محصولات"])
        writer.writerows(SAMPLE_PRODUCTS_DATA)

    # 2. شعب
    with open(os.path.join(output_dir, "شعب.csv"), "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(SHEET_SPECIFICATIONS["شعب"])
        writer.writerows(SAMPLE_BRANCHES_DATA)

    # 3. موجودی اولیه
    with open(os.path.join(output_dir, "موجودی_اولیه.csv"), "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(SHEET_SPECIFICATIONS["موجودی_اولیه"])
        writer.writerows(SAMPLE_INITIAL_STOCK)

    # 4. سایر شیت‌های خالی با هدر استاندارد
    for name in ["گزارش_موجودی_شعب", "گردش_کالا_و_تراکنش‌ها", "درخواست‌های_خرید_مشتریان"]:
        with open(os.path.join(output_dir, f"{name}.csv"), "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(SHEET_SPECIFICATIONS[name])

    try:
        print(f"قالب‌های CSV گوگل شیت در پوشه '{output_dir}' با موفقیت ایجاد شدند.")
    except UnicodeEncodeError:
        print(f"CSV templates generated in '{output_dir}'.")


async def seed_demo_database(session_factory=AsyncSessionLocal) -> None:
    async with session_factory() as session:
        # ایجاد شعب
        branches_map = {}
        for row in SAMPLE_BRANCHES_DATA:
            code, name, phone, address, lat, lng, status = row
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
                    is_active=True,
                )
                session.add(branch)
                await session.flush()
            branches_map[code] = branch

        # ایجاد محصولات
        laptops_map = {}
        for row in SAMPLE_PRODUCTS_DATA:
            sku, brand_name, series_name, model_name, cpu, ram, storage, gpu, condition, warranty, price, p_price, img1, img2, img3, desc, status = row

            brand = await session.scalar(select(LaptopBrand).where(LaptopBrand.name == brand_name))
            if brand is None:
                brand = LaptopBrand(name=brand_name)
                session.add(brand)
                await session.flush()

            laptop = await session.scalar(select(Laptop).where(Laptop.part_number == sku))
            if laptop is None:
                laptop = Laptop(
                    brand_id=brand.id,
                    model=model_name,
                    part_number=sku,
                    cpu=cpu,
                    ram=ram,
                    storage=storage,
                    gpu=gpu,
                    condition=condition,
                    warranty=warranty,
                    price=price,
                    purchase_price=p_price,
                    image_url=img1,
                    status="active",
                )
                session.add(laptop)
                await session.flush()
            laptops_map[sku] = laptop

            # عکس‌ها
            for order, img_url in enumerate([img1, img2, img3], start=1):
                if img_url:
                    existing_img = await session.scalar(
                        select(ProductImage).where(
                            ProductImage.laptop_id == laptop.id,
                            ProductImage.image_url == img_url,
                        )
                    )
                    if existing_img is None:
                        session.add(ProductImage(
                            laptop_id=laptop.id,
                            image_url=img_url,
                            display_order=order,
                            is_primary=order == 1,
                        ))

            # کاتالوگ درختی
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
                tree_model = LaptopModel(series_id=series.id, name=model_name, description=desc, is_active=True)
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
                    purchase_price=p_price,
                    legacy_laptop_id=laptop.id,
                    is_active=True,
                )
                session.add(variant)
                await session.flush()

        # موجودی اولیه
        for sku, branch_code, qty, min_alert in SAMPLE_INITIAL_STOCK:
            branch = branches_map.get(branch_code)
            laptop = laptops_map.get(sku)
            if branch and laptop:
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
                else:
                    inv.quantity = qty
                    inv.min_stock_alert = min_alert

                variant = await session.scalar(
                    select(LaptopVariant).where(LaptopVariant.legacy_laptop_id == laptop.id)
                )
                if variant:
                    b_stock = await session.scalar(
                        select(BranchStock).where(
                            BranchStock.variant_id == variant.id,
                            BranchStock.branch_id == branch.id,
                        )
                    )
                    if b_stock is None:
                        session.add(BranchStock(
                            variant_id=variant.id,
                            branch_id=branch.id,
                            quantity=qty,
                        ))
                    else:
                        b_stock.quantity = qty

        # ایجاد کاربر ادمین نمونه و مشتری نمونه
        admin_user = await session.scalar(select(User).where(User.telegram_id == 123456789))
        if admin_user is None:
            session.add(User(
                telegram_id=123456789,
                username="rhinotech_admin",
                full_name="مدیر فروشگاه راینوتک",
                role="admin",
                is_active=True,
            ))

        await session.commit()
        try:
            print("داده‌های آزمایشی فروشگاه راینوتک با موفقیت در دیتابیس ثبت شدند.")
        except UnicodeEncodeError:
            print("Demo database seeded successfully.")


async def main() -> None:
    await create_db()
    await seed_demo_database()
    export_csv_templates()


if __name__ == "__main__":
    asyncio.run(main())

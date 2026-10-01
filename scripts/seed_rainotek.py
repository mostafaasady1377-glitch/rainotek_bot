from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from bot.config import get_settings
from database.models import Branch, BranchInventory, Laptop, LaptopBrand
from database.session import AsyncSessionLocal, create_db


async def seed() -> None:
    await create_db()
    async with AsyncSessionLocal() as session:
        existing = await session.scalar(select(Branch.id).limit(1))
        if existing is not None:
            print("داده‌های اولیه راینوتک قبلاً وارد شده‌اند.")
            return

        branches = [
            Branch(name="شعبه مرکزی راینوتک", code="MAIN", phone="021-00000000", address="تهران، خیابان مرکزی", is_active=True),
            Branch(name="انبار قطعات راینوتک", code="WAREHOUSE", phone="021-11111111", address="تهران، انبار مرکزی", is_active=True),
            Branch(name="شعبه ۲ راینوتک", code="BR2", phone="021-22222222", address="شیراز، خیابان مدرس", is_active=True),
        ]
        session.add_all(branches)
        await session.flush()

        brands = [
            LaptopBrand(name="Asus"),
            LaptopBrand(name="Lenovo"),
            LaptopBrand(name="HP"),
            LaptopBrand(name="Dell"),
            LaptopBrand(name="Apple"),
        ]
        session.add_all(brands)
        await session.flush()

        laptop_data = [
            {"brand_id": brands[0].id, "model": "ROG Strix G16", "part_number": "ASUS-ROG-01", "cpu": "Core i7", "ram": "32GB", "gpu": "RTX 4060", "storage": "1TB SSD", "screen_size": "16\"", "color": "Black"},
            {"brand_id": brands[1].id, "model": "ThinkPad X1 Carbon", "part_number": "LEN-TPX1", "cpu": "Core i7", "ram": "16GB", "gpu": "Intel Iris Xe", "storage": "512GB SSD", "screen_size": "14\"", "color": "Silver"},
            {"brand_id": brands[2].id, "model": "Victus 16", "part_number": "HP-VIC-16", "cpu": "Core i5", "ram": "16GB", "gpu": "RTX 3050", "storage": "512GB SSD", "screen_size": "16\"", "color": "Blue"},
        ]

        laptops = [Laptop(**item) for item in laptop_data]
        session.add_all(laptops)
        await session.flush()

        inventory_rows = [
            BranchInventory(laptop_id=laptops[0].id, branch_id=branches[0].id, quantity=8, reserved_count=1, min_stock_alert=3),
            BranchInventory(laptop_id=laptops[1].id, branch_id=branches[0].id, quantity=5, reserved_count=0, min_stock_alert=2),
            BranchInventory(laptop_id=laptops[2].id, branch_id=branches[1].id, quantity=10, reserved_count=2, min_stock_alert=4),
            BranchInventory(laptop_id=laptops[0].id, branch_id=branches[2].id, quantity=4, reserved_count=1, min_stock_alert=2),
        ]
        session.add_all(inventory_rows)
        await session.commit()
        print("داده‌های نمونه راینوتک با موفقیت درج شدند.")


if __name__ == "__main__":
    asyncio.run(seed())

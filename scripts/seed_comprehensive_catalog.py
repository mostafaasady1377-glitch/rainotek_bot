from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select

from database.models import (
    Branch,
    BranchInventory,
    BranchStock,
    Laptop,
    LaptopBrand,
    LaptopModel,
    LaptopSeries,
    LaptopVariant,
)
from database.session import AsyncSessionLocal, create_db

CATALOG = [
    {
        "brand": "Asus",
        "series": "ROG",
        "models": [
            ("ROG Strix G16", "مناسب گیمینگ سنگین، رندر و طراحی سه‌بعدی", [
                ("Gen 13", "Core i7-13650HX", "16GB", "RTX 4060", "1TB NVMe"),
                ("Gen 14", "Core i9-14900HX", "32GB", "RTX 4070", "1TB NVMe"),
            ]),
            ("ROG Zephyrus G14", "گیمینگ و تولید محتوا در بدنه سبک", [
                ("Gen 13", "Ryzen 9 7940HS", "16GB", "RTX 4060", "1TB SSD"),
                ("Gen 14", "Ryzen 9 8945HS", "32GB", "RTX 4070", "1TB SSD"),
            ]),
        ],
    },
    {
        "brand": "Asus", "series": "TUF Gaming", "models": [
            ("TUF Gaming A15", "مناسب بازی و کارهای گرافیکی", [
                ("Gen 12", "Ryzen 7 6800H", "16GB", "RTX 3050", "512GB NVMe"),
                ("Gen 13", "Ryzen 7 7735HS", "16GB", "RTX 4060", "1TB NVMe"),
            ])
        ],
    },
    {
        "brand": "Lenovo", "series": "ThinkPad", "models": [
            ("ThinkPad X1 Carbon", "مناسب مدیریت، جلسات و کار اداری حرفه‌ای", [
                ("Gen 11", "Core i7-1365U", "16GB", "Iris Xe", "512GB NVMe"),
                ("Gen 12", "Core Ultra 7 155U", "32GB", "Intel Graphics", "1TB NVMe"),
            ]),
            ("ThinkPad T14", "مناسب حسابداری، برنامه‌نویسی و استفاده سازمانی", [
                ("Gen 12", "Core i5-1245U", "16GB", "Iris Xe", "512GB NVMe"),
                ("Gen 14", "Core Ultra 7 155U", "32GB", "Intel Graphics", "1TB NVMe"),
            ]),
        ],
    },
    {
        "brand": "Lenovo", "series": "Legion", "models": [
            ("Legion 5 Pro", "مناسب گیمینگ سنگین، تدوین و رندر سه‌بعدی", [
                ("Gen 12", "Core i7-12700H", "16GB", "RTX 3060", "1TB NVMe"),
                ("Gen 13", "Core i7-13700HX", "32GB", "RTX 4070", "1TB NVMe"),
            ]),
            ("Legion Slim 5", "گیمینگ و تولید محتوا با قابلیت حمل بهتر", [
                ("Gen 13", "Ryzen 7 7840HS", "16GB", "RTX 4060", "1TB NVMe"),
            ]),
        ],
    },
    {
        "brand": "Lenovo", "series": "Yoga", "models": [
            ("Yoga 7", "مناسب دانشگاه، طراحی سبک و کار روزانه", [
                ("Gen 13", "Core i7-1355U", "16GB", "Iris Xe", "512GB SSD"),
            ])
        ],
    },
    {
        "brand": "Lenovo", "series": "IdeaPad", "models": [
            ("IdeaPad Slim 5", "مناسب کارهای اداری، آموزشی و حسابداری", [
                ("Gen 12", "Ryzen 5 5625U", "16GB", "Radeon Graphics", "512GB SSD"),
                ("Gen 13", "Core i5-13420H", "16GB", "Iris Xe", "512GB NVMe"),
            ])
        ],
    },
    {
        "brand": "HP", "series": "Victus", "models": [
            ("Victus 16", "مناسب بازی، تدوین و طراحی گرافیکی", [
                ("Gen 12", "Core i5-12500H", "16GB", "RTX 3050", "512GB NVMe"),
                ("Gen 13", "Core i7-13700H", "16GB", "RTX 4060", "1TB NVMe"),
            ])
        ],
    },
    {
        "brand": "HP", "series": "EliteBook", "models": [
            ("EliteBook 840", "مناسب کسب‌وکار، جلسات و امور اداری", [
                ("Gen 12", "Core i7-1265U", "16GB", "Iris Xe", "512GB NVMe"),
                ("Gen 13", "Core i7-1355U", "32GB", "Iris Xe", "1TB NVMe"),
            ])
        ],
    },
    {
        "brand": "Dell", "series": "XPS", "models": [
            ("XPS 15", "مناسب طراحی، تدوین و تولید محتوا", [
                ("Gen 12", "Core i7-12700H", "16GB", "RTX 3050 Ti", "1TB NVMe"),
                ("Gen 13", "Core i7-13700H", "32GB", "RTX 4060", "1TB NVMe"),
            ])
        ],
    },
    {
        "brand": "Dell", "series": "Latitude", "models": [
            ("Latitude 5440", "مناسب امور سازمانی، حسابداری و ترید", [
                ("Gen 13", "Core i5-1345U", "16GB", "Iris Xe", "512GB NVMe"),
            ])
        ],
    },
    {
        "brand": "Apple", "series": "MacBook Air", "models": [
            ("MacBook Air 13", "مناسب دانشگاه، برنامه‌نویسی و کارهای روزانه", [
                ("M2", "Apple M2", "16GB", "Apple GPU 10-core", "512GB SSD"),
                ("M3", "Apple M3", "16GB", "Apple GPU 10-core", "512GB SSD"),
            ])
        ],
    },
    {
        "brand": "Apple", "series": "MacBook Pro", "models": [
            ("MacBook Pro 14", "مناسب تدوین حرفه‌ای، توسعه و تولید محتوا", [
                ("M2 Pro", "Apple M2 Pro", "16GB", "Apple GPU 16-core", "512GB SSD"),
                ("M3 Pro", "Apple M3 Pro", "18GB", "Apple GPU 18-core", "1TB SSD"),
            ])
        ],
    },
    {
        "brand": "Acer", "series": "Nitro", "models": [
            ("Nitro 5", "مناسب گیمینگ اقتصادی و کار گرافیکی", [
                ("Gen 12", "Core i5-12500H", "16GB", "RTX 3050", "512GB NVMe"),
                ("Gen 13", "Core i7-13620H", "16GB", "RTX 4060", "1TB NVMe"),
            ])
        ],
    },
    {
        "brand": "Acer", "series": "Swift", "models": [
            ("Swift Go 14", "مناسب کارهای اداری، دانشگاه و حمل روزانه", [
                ("Gen 13", "Core i5-13500H", "16GB", "Iris Xe", "512GB NVMe"),
            ])
        ],
    },
    {
        "brand": "MSI", "series": "Katana", "models": [
            ("Katana 15", "مناسب گیمینگ و رندرینگ میان‌رده", [
                ("Gen 13", "Core i7-13620H", "16GB", "RTX 4060", "1TB NVMe"),
                ("Gen 14", "Core i7-14650HX", "32GB", "RTX 4070", "1TB NVMe"),
            ])
        ],
    },
    {
        "brand": "MSI", "series": "Prestige", "models": [
            ("Prestige 14", "مناسب کار حرفه‌ای، تولید محتوا و جابه‌جایی", [
                ("Gen 13", "Core i7-1360P", "16GB", "Iris Xe", "1TB NVMe"),
            ])
        ],
    },
    {
        "brand": "NEC", "series": "VersaPro", "models": [
            ("VersaPro VKT", "مناسب اداری، حسابداری و استفاده سازمانی", [
                ("Gen 11", "Core i5-1135G7", "8GB", "Iris Xe", "256GB SSD"),
            ])
        ],
    },
    {
        "brand": "Microsoft Surface", "series": "Surface Laptop", "models": [
            ("Surface Laptop 5", "مناسب کار اداری، دانشگاه و جلسات", [
                ("Gen 12", "Core i5-1235U", "16GB", "Iris Xe", "512GB SSD"),
            ])
        ],
    },
    {
        "brand": "Toshiba", "series": "Dynabook", "models": [
            ("Dynabook Tecra A40", "مناسب امور اداری و کسب‌وکار", [
                ("Gen 12", "Core i5-1235U", "16GB", "Iris Xe", "512GB SSD"),
            ])
        ],
    },
    {
        "brand": "Samsung", "series": "Galaxy Book", "models": [
            ("Galaxy Book3 Pro", "مناسب دانشگاه، تولید محتوا و کار روزانه", [
                ("Gen 13", "Core i7-1360P", "16GB", "Iris Xe", "512GB NVMe"),
            ])
        ],
    },
    {
        "brand": "Sony", "series": "VAIO", "models": [
            ("VAIO SX14", "مناسب مدیریت، جلسات و استفاده‌ی قابل‌حمل", [
                ("Gen 12", "Core i7-1260P", "16GB", "Iris Xe", "1TB NVMe"),
            ])
        ],
    },
]

BRANCHES = [
    ("شعبه مرکزی راینوتک", "MAIN", "021-00000000", "تهران، خیابان مرکزی", 35.7000, 51.4000),
    ("انبار قطعات راینوتک", "WAREHOUSE", "021-11111111", "تهران، انبار مرکزی", 35.7100, 51.4100),
    ("شعبه ۲ راینوتک", "BR2", "021-22222222", "شیراز، خیابان مدرس", 29.6100, 52.5300),
]


async def _get_or_create(session, model_type, **lookup):
    row = await session.scalar(select(model_type).filter_by(**lookup))
    if row is None:
        row = model_type(**lookup)
        session.add(row)
        await session.flush()
    return row


async def seed(session_factory=AsyncSessionLocal, initialize_db=create_db) -> None:
    await initialize_db()
    async with session_factory() as session:
        branches: dict[str, Branch] = {}
        for name, code, phone, address, latitude, longitude in BRANCHES:
            branch = await session.scalar(select(Branch).where(Branch.code == code))
            if branch is None:
                branch = Branch(
                    name=name,
                    code=code,
                    phone=phone,
                    address=address,
                    address_full=address,
                    latitude=latitude,
                    longitude=longitude,
                    is_active=True,
                )
                session.add(branch)
                await session.flush()
            else:
                branch.name = name
                branch.phone = phone
                branch.address = address
                branch.address_full = address
                branch.latitude = latitude
                branch.longitude = longitude
                branch.is_active = True
            branches[code] = branch

        variant_count = 0
        model_count = 0
        for group in CATALOG:
            brand_name = group["brand"]
            brand = await _get_or_create(session, LaptopBrand, name=brand_name)
            series = await _get_or_create(
                session, LaptopSeries, brand_id=brand.id, name=group["series"]
            )
            for model_name, use_case, configs in group["models"]:
                model = await session.scalar(
                    select(LaptopModel).where(
                        LaptopModel.series_id == series.id,
                        func.lower(LaptopModel.name) == model_name.lower(),
                    )
                )
                if model is None:
                    model = LaptopModel(series_id=series.id, name=model_name, use_case=use_case)
                    session.add(model)
                    await session.flush()
                else:
                    model.use_case = use_case
                    model.is_active = True
                model_count += 1

                for generation, cpu, ram, gpu, storage in configs:
                    variant = await session.scalar(
                        select(LaptopVariant).where(
                            LaptopVariant.model_id == model.id,
                            LaptopVariant.generation == generation,
                            LaptopVariant.cpu == cpu,
                            LaptopVariant.ram == ram,
                            LaptopVariant.gpu == gpu,
                            LaptopVariant.storage == storage,
                        )
                    )
                    legacy = await session.scalar(
                        select(Laptop).where(
                            Laptop.brand_id == brand.id,
                            func.lower(Laptop.model) == model_name.lower(),
                            Laptop.cpu == cpu,
                            Laptop.ram == ram,
                            Laptop.gpu == gpu,
                            Laptop.storage == storage,
                        )
                    )
                    if legacy is None:
                        legacy = Laptop(
                            brand_id=brand.id,
                            model=model_name,
                            cpu=cpu,
                            ram=ram,
                            gpu=gpu,
                            storage=storage,
                            status="active",
                        )
                        session.add(legacy)
                        await session.flush()
                    if variant is None:
                        variant = LaptopVariant(
                            model_id=model.id,
                            generation=generation,
                            cpu=cpu,
                            ram=ram,
                            gpu=gpu,
                            storage=storage,
                            legacy_laptop_id=legacy.id,
                            is_active=True,
                        )
                        session.add(variant)
                        await session.flush()
                    else:
                        variant.legacy_laptop_id = legacy.id
                        variant.is_active = True

                    for branch_index, (branch_code, stock_quantity) in enumerate(
                        (("MAIN", 3), ("WAREHOUSE", 5), ("BR2", 0))
                    ):
                        branch = branches[branch_code]
                        stock = await session.scalar(
                            select(BranchStock).where(
                                BranchStock.variant_id == variant.id,
                                BranchStock.branch_id == branch.id,
                            )
                        )
                        if stock is None:
                            stock = BranchStock(
                                variant_id=variant.id,
                                branch_id=branch.id,
                                quantity=stock_quantity if branch_index < 2 else 0,
                                reserved_count=0,
                            )
                            session.add(stock)
                        elif stock.quantity == 0 and branch_index < 2:
                            stock.quantity = stock_quantity

                        legacy_stock = await session.scalar(
                            select(BranchInventory).where(
                                BranchInventory.laptop_id == legacy.id,
                                BranchInventory.branch_id == branch.id,
                            )
                        )
                        if legacy_stock is None:
                            legacy_stock = BranchInventory(
                                laptop_id=legacy.id,
                                branch_id=branch.id,
                                quantity=stock_quantity if branch_index < 2 else 0,
                                reserved_count=0,
                                min_stock_alert=2,
                            )
                            session.add(legacy_stock)
                        elif legacy_stock.quantity == 0 and branch_index < 2:
                            legacy_stock.quantity = stock_quantity
                    variant_count += 1

        await session.commit()
        brand_count = len({group["brand"] for group in CATALOG})
        series_count = len({(group["brand"], group["series"]) for group in CATALOG})
        try:
            print(
                f"کاتالوگ جامع آماده شد: {brand_count} برند، {series_count} سری، "
                f"{model_count} مدل، {variant_count} کانفیگ، {len(branches)} شعبه."
            )
        except UnicodeEncodeError:
            print(
                f"Catalog seeded: {brand_count} brands, {series_count} series, "
                f"{model_count} models, {variant_count} variants, {len(branches)} branches."
            )


if __name__ == "__main__":
    asyncio.run(seed())

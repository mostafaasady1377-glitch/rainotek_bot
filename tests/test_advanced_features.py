from __future__ import annotations

import unittest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.config import Settings
from bot.handlers.voice_assistant import format_voice_results
from bot.handlers.stock_entry import parse_quick_add
from bot.keyboards.catalog_builder import PAGE_SIZE, paginated_catalog_keyboard
from bot.services.ai_voice_service import AIVoiceService, LaptopVoiceFilters
from bot.services.inventory_service import InventoryService
from database.session import backfill_legacy_catalog
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
)
from scripts.seed_comprehensive_catalog import seed as seed_comprehensive_catalog


class AdvancedFeatureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_faceted_search_and_branch_location_data(self) -> None:
        async with self.sessions() as session:
            branch = Branch(
                name="شعبه مرکزی راینوتک",
                code="MAIN",
                phone="021-12345678",
                address_full="تهران، خیابان نمونه، پلاک ۱۰",
                latitude=35.7,
                longitude=51.4,
            )
            brand = LaptopBrand(name="Asus")
            session.add_all([branch, brand])
            await session.flush()
            laptop = Laptop(
                brand_id=brand.id,
                model="ROG Strix G16",
                cpu="Core i7",
                ram="16GB",
                gpu="RTX 4060",
                storage="512GB SSD",
                status="active",
            )
            session.add(laptop)
            await session.flush()
            session.add(BranchInventory(laptop_id=laptop.id, branch_id=branch.id, quantity=4, reserved_count=1))
            await session.commit()

            service = InventoryService(session)
            self.assertEqual(await service.get_laptop_facet_values("ROG Strix G16", "ram"), ["16GB"])
            matches = await service.search_laptops_by_specs({"brand": "Asus", "gpu": "4060", "ram": "16"})
            self.assertEqual(len(matches), 1)
            details = await service.get_laptop_branch_details([laptop.id])
            branch_info = details[laptop.id][0]
            self.assertEqual(branch_info["available"], 3)
            self.assertEqual(branch_info["address"], "تهران، خیابان نمونه، پلاک ۱۰")
            self.assertEqual((branch_info["latitude"], branch_info["longitude"]), (35.7, 51.4))

    async def test_quick_add_template_accepts_persian_digits(self) -> None:
        parsed = parse_quick_add("افزودن / شعبه مرکزی / G16 / i7 / 16GB / 4060 / 512GB / تعداد: ۴")
        self.assertEqual(parsed, ("شعبه مرکزی", "G16", "i7", "16GB", "4060", "512GB", 4))
        self.assertIsNone(parse_quick_add("افزودن / شعبه مرکزی / G16 / تعداد: چهار"))

    async def test_voice_filter_schema_and_configuration(self) -> None:
        settings = Settings(BOT_TOKEN="test-only", GROQ_API_KEY="test-key")
        service = AIVoiceService(settings)
        parsed = LaptopVoiceFilters.model_validate({
            "brand": "Asus",
            "ram": "16GB",
            "gpu": "RTX 4060",
            "unexpected": "ignored",
        })
        self.assertEqual(parsed.as_filters(), {"brand": "Asus", "ram": "16GB", "gpu": "RTX 4060"})
        self.assertEqual(service._headers()["Authorization"], "Bearer test-key")
        with self.assertRaises(ValueError):
            AIVoiceService(Settings(BOT_TOKEN="test-only"))

    async def test_voice_result_escapes_html_markup(self) -> None:
        laptop = type("LaptopResult", (), {
            "id": 1,
            "brand": type("Brand", (), {"name": "A&B"})(),
            "model": "Model <X>",
            "cpu": "CPU <7>",
            "ram": "16GB",
            "gpu": "RTX & 4060",
            "storage": "1TB",
        })()
        output = format_voice_results(
            {"model": "Model <X>"},
            [laptop],
            {1: [{"available": 2, "name": "Main <Branch>", "phone": None, "address": "A&B"}]},
        )
        self.assertIn("Model &lt;X&gt;", output)
        self.assertIn("A&amp;B", output)

    async def test_hierarchical_catalog_and_pagination(self) -> None:
        async with self.sessions() as session:
            brand = LaptopBrand(name="Acer")
            session.add(brand)
            await session.flush()
            series = LaptopSeries(brand_id=brand.id, name="Nitro")
            session.add(series)
            await session.flush()
            model = LaptopModel(series_id=series.id, name="Nitro 5", use_case="Gaming")
            session.add(model)
            await session.flush()
            variant = LaptopVariant(
                model_id=model.id,
                generation="Gen 13",
                cpu="Core i7",
                ram="16GB",
                gpu="RTX 4060",
                storage="1TB NVMe",
            )
            branch = Branch(name="Central", code="H1", address_full="Tehran", latitude=35.7, longitude=51.4)
            session.add_all([variant, branch])
            await session.flush()
            session.add(BranchStock(variant_id=variant.id, branch_id=branch.id, quantity=3))
            await session.commit()

            service = InventoryService(session)
            brands, brand_count = await service.catalog_brands(0, PAGE_SIZE)
            self.assertEqual((brand_count, brands[0].name), (1, "Acer"))
            series_rows, series_count = await service.catalog_series(brand.id, 0)
            self.assertEqual((series_count, series_rows[0].name), (1, "Nitro"))
            model_rows, model_count = await service.catalog_models(series.id, 0)
            self.assertEqual((model_count, model_rows[0].name), (1, "Nitro 5"))
            self.assertEqual(await service.catalog_generations(model.id), ["Gen 13"])
            self.assertEqual(await service.catalog_cpus(model.id, "Gen 13"), ["Core i7"])
            variant_rows, variant_count = await service.catalog_variants(model.id, "Gen 13", "Core i7", 0)
            self.assertEqual((variant_count, variant_rows[0].ram), (1, "16GB"))
            detail = await service.catalog_variant_details(variant.id)
            self.assertEqual(detail["branches"][0]["quantity"], 3)

        keyboard = paginated_catalog_keyboard(
            [(f"item-{index}", f"callback:{index}") for index in range(PAGE_SIZE + 2)],
            page=0,
            total=PAGE_SIZE + 2,
        )
        self.assertTrue(
            any(button.callback_data == "tree:page:1" for row in keyboard.inline_keyboard for button in row)
        )
        second_page = paginated_catalog_keyboard(
            [(f"item-{index}", f"callback:{index}") for index in range(PAGE_SIZE + 2)],
            page=1,
            total=PAGE_SIZE + 2,
        )
        second_page_callbacks = [
            button.callback_data
            for row in second_page.inline_keyboard
            for button in row
            if button.callback_data.startswith("callback:")
        ]
        self.assertEqual(second_page_callbacks, ["callback:8", "callback:9"])

    async def test_tree_stock_sync_and_actor_log(self) -> None:
        async with self.sessions() as session:
            branch = Branch(name="Central", code="T1")
            session.add(branch)
            await session.commit()
            service = InventoryService(session)
            variant = await service.get_or_create_tree_variant(
                "Asus", "ROG", "G16", "Gen 13", "Core i7", "16GB", "RTX 4060", "1TB NVMe", "Gaming"
            )
            await service.set_tree_stock_quantity(variant.id, branch.id, 5, 919)
            tree_stock = await session.scalar(select(BranchStock).where(BranchStock.variant_id == variant.id))
            legacy_stock = await session.scalar(select(BranchInventory).where(BranchInventory.branch_id == branch.id))
            log = await session.scalar(select(InventoryAuditLog).where(InventoryAuditLog.change_type == "TREE_SET"))
            self.assertEqual((tree_stock.quantity, legacy_stock.quantity), (5, 5))
            self.assertEqual(log.actor_telegram_id, 919)

    async def test_legacy_catalog_backfill_is_repeatable(self) -> None:
        async with self.sessions() as session:
            branch = Branch(name="Legacy Branch", code="LEG")
            brand = LaptopBrand(name="Dell")
            session.add_all([branch, brand])
            await session.flush()
            laptop = Laptop(
                brand_id=brand.id,
                model="XPS 15",
                cpu="Core i7",
                ram="16GB",
                gpu="RTX 4060",
                storage="1TB SSD",
                status="active",
            )
            session.add(laptop)
            await session.flush()
            session.add(BranchInventory(laptop_id=laptop.id, branch_id=branch.id, quantity=6, reserved_count=1))
            await session.commit()

            await backfill_legacy_catalog(session)
            await backfill_legacy_catalog(session)
            variants = (await session.scalars(select(LaptopVariant))).all()
            series = (await session.scalars(select(LaptopSeries))).all()
            tree_stock = await session.scalar(select(BranchStock).where(BranchStock.branch_id == branch.id))
            self.assertEqual(len(variants), 1)
            self.assertEqual(len(series), 1)
            self.assertEqual((tree_stock.quantity, tree_stock.reserved_count), (6, 1))

    async def test_comprehensive_seed_is_idempotent(self) -> None:
        async def no_initialize() -> None:
            return None

        await seed_comprehensive_catalog(self.sessions, no_initialize)
        await seed_comprehensive_catalog(self.sessions, no_initialize)
        async with self.sessions() as session:
            brands = (await session.scalars(select(LaptopBrand))).all()
            series = (await session.scalars(select(LaptopSeries))).all()
            models = (await session.scalars(select(LaptopModel))).all()
            variants = (await session.scalars(select(LaptopVariant))).all()
            branches = (await session.scalars(select(Branch))).all()
            self.assertGreaterEqual(len(brands), 10)
            self.assertGreaterEqual(len(series), 15)
            self.assertGreaterEqual(len(models), 15)
            self.assertGreaterEqual(len(variants), 25)
            self.assertEqual(len(branches), 3)


if __name__ == "__main__":
    unittest.main()

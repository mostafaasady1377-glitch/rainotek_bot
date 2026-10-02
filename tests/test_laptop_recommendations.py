import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.handlers import ai_hub
from bot.services.laptop_recommendations import capacity_gb, dedicated_gpu, gpu_strength, recommend_laptops, score_laptop
from database.models import Base, Branch, BranchInventory, Laptop, LaptopBrand


class LaptopRecommendationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    def test_spec_parsing_distinguishes_integrated_from_dedicated_gpu(self):
        self.assertEqual(capacity_gb("1 TB SSD"), 1024)
        self.assertEqual(capacity_gb("۱۶ گیگ"), 16)
        self.assertFalse(dedicated_gpu("Intel UHD up to 4GB"))
        self.assertTrue(dedicated_gpu("NVIDIA RTX 4060"))
        self.assertGreater(gpu_strength("RTX 4060"), gpu_strength("RTX 3050"))
        self.assertLess(gpu_strength("GeForce 920M"), 2)
        self.assertLess(gpu_strength("Nvidia Quadro K1100"), 2)
        basic = SimpleNamespace(cpu="Celeron N4020", ram="4GB", storage="500GB HDD", gpu="Intel UHD", price=12_000_000)
        self.assertIsNotNone(score_laptop("study", basic))
        self.assertIsNone(score_laptop("office", basic))
        light_editor = SimpleNamespace(cpu="Core i7-4600U", ram="8GB", storage="256 SSD",
                                       gpu="Nvidia Quadro K1100", price=52_000_000)
        self.assertIn("تدوین سبک", " ".join(score_laptop("content", light_editor)[1]))
        self.assertIsNone(score_laptop("gaming", light_editor))

    async def test_only_active_priced_in_stock_laptops_are_recommended(self):
        async with self.factory() as session:
            brand = LaptopBrand(name="Dell")
            branch = Branch(name="میرداماد", code="MRD")
            session.add_all([brand, branch])
            await session.flush()
            gaming = Laptop(brand_id=brand.id, model="G15", cpu="Core i7-12700H", ram="16 GB",
                            storage="512 GB SSD", gpu="RTX 3060", price=50_000_000, status="active")
            office = Laptop(brand_id=brand.id, model="Latitude", cpu="Core i5-10310U", ram="8 GB",
                            storage="256 GB SSD", gpu="Intel UHD", price=20_000_000, status="active")
            sold = Laptop(brand_id=brand.id, model="Sold", cpu="Core i7-12700H", ram="16 GB",
                          storage="512 GB SSD", gpu="RTX 4060", price=40_000_000, status="active")
            basic = Laptop(brand_id=brand.id, model="Basic", cpu="Celeron N4020", ram="4 GB",
                           storage="500 GB HDD", gpu="Intel UHD", price=10_000_000, status="active")
            session.add_all([gaming, office, sold, basic])
            await session.flush()
            session.add_all([
                BranchInventory(laptop_id=gaming.id, branch_id=branch.id, quantity=2, reserved_count=1),
                BranchInventory(laptop_id=office.id, branch_id=branch.id, quantity=1, reserved_count=0),
                BranchInventory(laptop_id=sold.id, branch_id=branch.id, quantity=1, reserved_count=1),
                BranchInventory(laptop_id=basic.id, branch_id=branch.id, quantity=1, reserved_count=0),
            ])
            await session.commit()
            games = await recommend_laptops(session, "gaming")
            study = await recommend_laptops(session, "study")
        self.assertEqual([item["model"] for item in games], ["G15"])
        self.assertEqual(games[0]["quantity"], 1)
        self.assertEqual(study[0]["model"], "Basic")
        self.assertEqual([item["model"] for item in await self._recommend("office")], ["Latitude"])
        self.assertTrue(all(item["model"] != "Sold" for item in study))

    async def _recommend(self, purpose):
        async with self.factory() as session:
            return await recommend_laptops(session, purpose)

    async def test_purpose_click_immediately_shows_inventory_results(self):
        callback = SimpleNamespace(data="ai:purpose:study", answer=AsyncMock(),
                                   message=SimpleNamespace(answer=AsyncMock()))
        state = SimpleNamespace(clear=AsyncMock(), set_state=AsyncMock())
        result = {"id": 4, "brand": "Dell", "model": "Latitude", "price": 20_000_000,
                  "quantity": 1, "score": 81, "reasons": ["CPU: Core i5", "رم: 8 GB", "حافظه: 256 GB"]}
        with patch.object(ai_hub, "AsyncSessionLocal", self.factory), patch.object(
            ai_hub, "recommend_laptops", new_callable=AsyncMock, return_value=[result]
        ):
            await ai_hub.assistant_purpose(callback, state)
        state.set_state.assert_not_awaited()
        text = callback.message.answer.await_args.args[0]
        self.assertIn("Dell Latitude", text)
        self.assertIn("20,000,000", text)
        buttons = callback.message.answer.await_args.kwargs["reply_markup"].inline_keyboard
        self.assertEqual(buttons[0][0].callback_data, "ai:laptop:4")

    async def test_recommendations_show_next_page_of_twenty(self):
        callback = SimpleNamespace(data="ai:purpose_page:study:1", answer=AsyncMock(),
                                   message=SimpleNamespace(answer=AsyncMock()))
        results = [{"id": number, "brand": "Dell", "model": f"Model {number}",
                    "price": 10_000_000 + number * 1_000_000, "quantity": 1,
                    "score": 70, "reasons": ["CPU: Core i5", "رم: 8GB", "حافظه: 256GB"]}
                   for number in range(20)]
        with patch.object(ai_hub, "AsyncSessionLocal", self.factory), patch.object(
            ai_hub, "recommend_laptops", new_callable=AsyncMock, return_value=results
        ):
            await ai_hub.assistant_purpose_page(callback)
        text = callback.message.answer.await_args.args[0]
        self.assertIn("صفحهٔ 2 از 2", text)
        self.assertIn("Model 10", text)
        self.assertNotIn("Model 0</b>", text)
        buttons = callback.message.answer.await_args.kwargs["reply_markup"].inline_keyboard
        self.assertEqual(buttons[0][0].callback_data, "ai:laptop:10")
        self.assertEqual(buttons[10][0].callback_data, "ai:purpose_page:study:0")

    async def test_assistant_menu_has_eight_use_cases_in_pairs(self):
        callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()))
        state = SimpleNamespace(clear=AsyncMock(), set_state=AsyncMock())
        await ai_hub.assistant_home(callback, state)
        rows = callback.message.answer.await_args.kwargs["reply_markup"].inline_keyboard
        self.assertEqual([len(row) for row in rows], [2, 2, 2, 2, 1, 1])
        self.assertEqual({button.callback_data for row in rows[:4] for button in row},
                         {f"ai:purpose:{key}" for key in ai_hub.PURPOSES})
        self.assertEqual(rows[4][0].callback_data, "ai:development")
        self.assertEqual(rows[3][1].callback_data, "ai:purpose:content")

from __future__ import annotations

import unittest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.config import Settings
from bot.handlers.reports import build_stock_csv
from bot.services.inventory_service import InventoryService
from database.models import (
    Base,
    Branch,
    BranchInventory,
    InventoryAuditLog,
    Laptop,
    LaptopBrand,
    User,
)
from database.users import ensure_user


class RainotekPhaseAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def seed_inventory(self):
        session = self.session_factory()
        branch_one = Branch(name="مرکزی", code="C1", is_active=True)
        branch_two = Branch(name="شمال", code="N1", is_active=True)
        brand = LaptopBrand(name="Lenovo")
        session.add_all([branch_one, branch_two, brand])
        await session.flush()
        laptop = Laptop(brand_id=brand.id, model="ThinkPad X1", cpu="Core i7", status="active")
        session.add(laptop)
        await session.flush()
        self.branch_one_id = branch_one.id
        self.branch_two_id = branch_two.id
        self.laptop_id = laptop.id
        session.add(
            BranchInventory(
                laptop_id=laptop.id,
                branch_id=branch_one.id,
                quantity=8,
                reserved_count=2,
                min_stock_alert=3,
            )
        )
        await session.commit()
        return session

    async def test_phase_one_user_registration_and_roles(self) -> None:
        settings = Settings(BOT_TOKEN="test-only", ADMIN_TELEGRAM_IDS=[101])
        async with self.session_factory() as session:
            admin = await ensure_user(session, 101, "owner", "مدیر", settings)
            seller = await ensure_user(session, 202, "seller", "فروشنده", settings)
            self.assertEqual(admin.role, "admin")
            self.assertEqual(seller.role, "seller")
            seller.is_active = False
            await session.commit()
            updated = await ensure_user(session, 202, "seller-new", "فروشنده جدید", settings)
            self.assertFalse(updated.is_active)
            self.assertEqual(updated.username, "seller-new")

    async def test_phase_two_brand_search_and_sellable_stock(self) -> None:
        session = await self.seed_inventory()
        async with session:
            service = InventoryService(session)
            found = await service.search_laptops("Lenovo")
            self.assertEqual([laptop.id for laptop in found], [self.laptop_id])
            self.assertEqual(found[0].brand.name, "Lenovo")
            availability = await service.get_laptop_availability([self.laptop_id])
            self.assertEqual(availability[self.laptop_id][0]["available"], 6)
            self.assertEqual(await service.search_laptops("   "), [])

    async def test_phase_three_protected_stock_transfer_and_log(self) -> None:
        session = await self.seed_inventory()
        async with session:
            service = InventoryService(session)
            with self.assertRaises(ValueError):
                await service.deduct_stock(self.laptop_id, self.branch_one_id, 7, 101)
            with self.assertRaises(ValueError):
                await service.transfer_stock(self.laptop_id, self.branch_one_id, self.branch_two_id, 7, 101)
            with self.assertRaises(ValueError):
                await service.transfer_stock(self.laptop_id, self.branch_one_id, self.branch_one_id, 1, 101)

            await service.transfer_stock(self.laptop_id, self.branch_one_id, self.branch_two_id, 2, 101)
            source = await session.scalar(
                select(BranchInventory).where(
                    BranchInventory.laptop_id == self.laptop_id,
                    BranchInventory.branch_id == self.branch_one_id,
                )
            )
            destination = await session.scalar(
                select(BranchInventory).where(
                    BranchInventory.laptop_id == self.laptop_id,
                    BranchInventory.branch_id == self.branch_two_id,
                )
            )
            log = await session.scalar(
                select(InventoryAuditLog).where(InventoryAuditLog.change_type == "TRANSFER")
            )
            self.assertEqual(source.quantity, 6)
            self.assertEqual(destination.quantity, 2)
            self.assertIsNotNone(log)

    async def test_phase_four_complete_audit_and_reconciliation(self) -> None:
        session = await self.seed_inventory()
        async with session:
            service = InventoryService(session)
            audit = await service.start_audit(self.branch_one_id, 101)
            audit_id = audit.id
            with self.assertRaises(ValueError):
                await service.start_audit(self.branch_one_id, 202)
            with self.assertRaises(ValueError):
                await service.finish_audit(audit_id)

            item = await service.submit_audit_item(audit_id, self.laptop_id, 7)
            self.assertEqual(item.system_quantity, 8)
            self.assertEqual(item.discrepancy, -1)
            await service.finish_audit(audit_id)

            stock = await session.scalar(
                select(BranchInventory).where(
                    BranchInventory.laptop_id == self.laptop_id,
                    BranchInventory.branch_id == self.branch_one_id,
                )
            )
            adjustment = await session.scalar(
                select(InventoryAuditLog).where(
                    InventoryAuditLog.change_type == "AUDIT_ADJUSTMENT"
                )
            )
            self.assertEqual(stock.quantity, 7)
            self.assertEqual(adjustment.quantity, 1)

    async def test_phase_five_low_stock_and_csv_export(self) -> None:
        session = await self.seed_inventory()
        async with session:
            alerts = await InventoryService(session).get_low_stock_alerts()
            self.assertEqual(len(alerts), 0)
            stock = await session.scalar(
                select(BranchInventory).where(
                    BranchInventory.laptop_id == self.laptop_id,
                    BranchInventory.branch_id == self.branch_one_id,
                )
            )
            stock.quantity = 2
            await session.commit()
            alerts = await InventoryService(session).get_low_stock_alerts()
            self.assertEqual(len(alerts), 1)
            payload = build_stock_csv([("مرکزی", "C1", "ThinkPad X1", None, 2, 0, 3)])
            content = payload.decode("utf-8-sig")
            self.assertIn("branch_code", content)
            self.assertIn("ThinkPad X1", content)


if __name__ == "__main__":
    unittest.main()

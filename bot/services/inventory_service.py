from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from database.models import (
    Branch,
    Branch,
    BranchInventory,
    BranchStock,
    InventoryAuditLog,
    Laptop,
    LaptopBrand,
    LaptopModel,
    LaptopSeries,
    LaptopVariant,
    StockAuditChecklist,
    StockAuditItem,
)


class InventoryService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _sync_tree_stock(
        self,
        laptop_id: int,
        branch_id: int,
        quantity: int,
        reserved_count: int = 0,
    ) -> None:
        variants = list((await self.session.scalars(
            select(LaptopVariant).where(LaptopVariant.legacy_laptop_id == laptop_id)
        )).all())
        for variant in variants:
            stock = await self.session.scalar(
                select(BranchStock).where(
                    BranchStock.variant_id == variant.id,
                    BranchStock.branch_id == branch_id,
                )
            )
            if stock is None:
                stock = BranchStock(
                    variant_id=variant.id,
                    branch_id=branch_id,
                    quantity=quantity,
                    reserved_count=reserved_count,
                )
                self.session.add(stock)
            else:
                stock.quantity = quantity
                stock.reserved_count = reserved_count

    async def catalog_brands(self, page: int, page_size: int = 8) -> tuple[list[LaptopBrand], int]:
        safe_page = max(page, 0)
        total = await self.session.scalar(
            select(func.count(func.distinct(LaptopBrand.id)))
            .join(LaptopSeries, LaptopSeries.brand_id == LaptopBrand.id)
            .join(LaptopModel, LaptopModel.series_id == LaptopSeries.id)
            .where(LaptopModel.is_active.is_(True))
        ) or 0
        result = await self.session.scalars(
            select(LaptopBrand)
            .join(LaptopSeries, LaptopSeries.brand_id == LaptopBrand.id)
            .join(LaptopModel, LaptopModel.series_id == LaptopSeries.id)
            .where(LaptopModel.is_active.is_(True))
            .distinct()
            .order_by(LaptopBrand.name)
            .offset(safe_page * page_size)
            .limit(page_size)
        )
        return list(result.all()), total

    async def catalog_series(self, brand_id: int, page: int, page_size: int = 8) -> tuple[list[LaptopSeries], int]:
        condition = LaptopSeries.brand_id == brand_id
        total = await self.session.scalar(select(func.count(LaptopSeries.id)).where(condition)) or 0
        result = await self.session.scalars(
            select(LaptopSeries).where(condition).order_by(LaptopSeries.name)
            .offset(max(page, 0) * page_size).limit(page_size)
        )
        return list(result.all()), total

    async def catalog_models(self, series_id: int, page: int, page_size: int = 8) -> tuple[list[LaptopModel], int]:
        condition = (LaptopModel.series_id == series_id) & LaptopModel.is_active.is_(True)
        total = await self.session.scalar(select(func.count(LaptopModel.id)).where(condition)) or 0
        result = await self.session.scalars(
            select(LaptopModel).where(condition).order_by(LaptopModel.name)
            .offset(max(page, 0) * page_size).limit(page_size)
        )
        return list(result.all()), total

    async def catalog_generations(self, model_id: int) -> list[str]:
        result = await self.session.scalars(
            select(LaptopVariant.generation)
            .where(LaptopVariant.model_id == model_id, LaptopVariant.is_active.is_(True))
            .distinct().order_by(LaptopVariant.generation)
        )
        return [value or "نامشخص" for value in result.all()]

    async def catalog_cpus(self, model_id: int, generation: str) -> list[str]:
        generation_condition = (
            LaptopVariant.generation.is_(None) if generation == "نامشخص"
            else LaptopVariant.generation == generation
        )
        result = await self.session.scalars(
            select(LaptopVariant.cpu)
            .where(
                LaptopVariant.model_id == model_id,
                LaptopVariant.is_active.is_(True),
                generation_condition,
                LaptopVariant.cpu.is_not(None),
            )
            .distinct().order_by(LaptopVariant.cpu)
        )
        return [value for value in result.all() if value]

    async def catalog_variants(
        self,
        model_id: int,
        generation: str,
        cpu: str,
        page: int,
        page_size: int = 8,
    ) -> tuple[list[LaptopVariant], int]:
        generation_condition = (
            LaptopVariant.generation.is_(None) if generation == "نامشخص"
            else LaptopVariant.generation == generation
        )
        condition = (
            (LaptopVariant.model_id == model_id)
            & LaptopVariant.is_active.is_(True)
            & generation_condition
            & (LaptopVariant.cpu == cpu)
        )
        total = await self.session.scalar(select(func.count(LaptopVariant.id)).where(condition)) or 0
        result = await self.session.scalars(
            select(LaptopVariant).where(condition)
            .order_by(LaptopVariant.ram, LaptopVariant.gpu, LaptopVariant.storage)
            .offset(max(page, 0) * page_size).limit(page_size)
        )
        return list(result.all()), total

    async def catalog_variant_details(self, variant_id: int) -> dict[str, Any] | None:
        variant = await self.session.scalar(
            select(LaptopVariant)
            .options(joinedload(LaptopVariant.model).joinedload(LaptopModel.series).joinedload(LaptopSeries.brand))
            .where(LaptopVariant.id == variant_id, LaptopVariant.is_active.is_(True))
        )
        if variant is None:
            return None
        result = await self.session.execute(
                 select(Branch.id, Branch.name, Branch.phone, Branch.address_full, Branch.address,
                   Branch.latitude, Branch.longitude, BranchStock.quantity, BranchStock.reserved_count)
            .select_from(Branch)
            .outerjoin(BranchStock, (BranchStock.branch_id == Branch.id) & (BranchStock.variant_id == variant_id))
            .where(Branch.is_active.is_(True))
            .order_by(Branch.name)
        )
        stocks = [
            {
                "branch_id": branch_id,
                "name": name,
                "phone": phone,
                "address": address_full or address,
                "latitude": latitude,
                "longitude": longitude,
                "quantity": quantity or 0,
                "available": max((quantity or 0) - (reserved or 0), 0),
            }
            for branch_id, name, phone, address_full, address, latitude, longitude, quantity, reserved in result.all()
        ]
        model = variant.model
        return {
            "variant": variant,
            "brand": model.series.brand.name,
            "series": model.series.name,
            "model": model.name,
            "use_case": model.use_case,
            "description": model.description,
            "branches": stocks,
        }

    async def get_or_create_tree_variant(
        self,
        brand_name: str,
        series_name: str,
        model_name: str,
        generation: str,
        cpu: str,
        ram: str,
        gpu: str,
        storage: str,
        use_case: str | None = None,
    ) -> LaptopVariant:
        brand = await self.session.scalar(
            select(LaptopBrand).where(func.lower(LaptopBrand.name) == brand_name.strip().lower())
        )
        if brand is None:
            brand = LaptopBrand(name=brand_name.strip())
            self.session.add(brand)
            await self.session.flush()

        series = await self.session.scalar(
            select(LaptopSeries).where(
                LaptopSeries.brand_id == brand.id,
                func.lower(LaptopSeries.name) == series_name.strip().lower(),
            )
        )
        if series is None:
            series = LaptopSeries(brand_id=brand.id, name=series_name.strip())
            self.session.add(series)
            await self.session.flush()

        model = await self.session.scalar(
            select(LaptopModel).where(
                LaptopModel.series_id == series.id,
                func.lower(LaptopModel.name) == model_name.strip().lower(),
            )
        )
        if model is None:
            model = LaptopModel(series_id=series.id, name=model_name.strip(), use_case=use_case)
            self.session.add(model)
            await self.session.flush()
        elif use_case and not model.use_case:
            model.use_case = use_case

        generation_value = None if generation == "نامشخص" else generation.strip()
        variant = await self.session.scalar(
            select(LaptopVariant).where(
                LaptopVariant.model_id == model.id,
                LaptopVariant.generation == generation_value,
                LaptopVariant.cpu == cpu.strip(),
                LaptopVariant.ram == ram.strip(),
                LaptopVariant.gpu == gpu.strip(),
                LaptopVariant.storage == storage.strip(),
            )
        )
        if variant is None:
            legacy = await self.session.scalar(
                select(Laptop).where(
                    Laptop.brand_id == brand.id,
                    func.lower(Laptop.model) == model.name.lower(),
                    Laptop.cpu == cpu.strip(),
                    Laptop.ram == ram.strip(),
                    Laptop.gpu == gpu.strip(),
                    Laptop.storage == storage.strip(),
                )
            )
            if legacy is None:
                legacy = Laptop(
                    brand_id=brand.id,
                    model=model.name,
                    cpu=cpu.strip(),
                    ram=ram.strip(),
                    gpu=gpu.strip(),
                    storage=storage.strip(),
                    status="active",
                )
                self.session.add(legacy)
                await self.session.flush()
            variant = LaptopVariant(
                model_id=model.id,
                generation=generation_value,
                cpu=cpu.strip(),
                ram=ram.strip(),
                gpu=gpu.strip(),
                storage=storage.strip(),
                legacy_laptop_id=legacy.id,
            )
            self.session.add(variant)
        await self.session.commit()
        return variant

    async def set_tree_stock_quantity(
        self,
        variant_id: int,
        branch_id: int,
        quantity: int,
        actor_telegram_id: int,
    ) -> BranchStock:
        if quantity < 0:
            raise ValueError("موجودی نمی‌تواند منفی باشد.")
        variant = await self.session.get(LaptopVariant, variant_id)
        if variant is None:
            raise ValueError("کانفیگ درختی پیدا نشد.")
        branch = await self.session.get(Branch, branch_id)
        if branch is None or not branch.is_active:
            raise ValueError("شعبه فعال پیدا نشد.")

        stock = await self.session.scalar(
            select(BranchStock).where(
                BranchStock.variant_id == variant_id,
                BranchStock.branch_id == branch_id,
            )
        )
        if stock is None:
            stock = BranchStock(variant_id=variant_id, branch_id=branch_id, quantity=0, reserved_count=0)
            self.session.add(stock)
            await self.session.flush()
        if quantity < stock.reserved_count:
            raise ValueError("موجودی نمی‌تواند کمتر از تعداد رزروشده باشد.")

        delta = quantity - stock.quantity
        stock.quantity = quantity
        legacy_id = variant.legacy_laptop_id
        if legacy_id is None:
            await self.session.flush()
            legacy_id = variant.legacy_laptop_id
        if legacy_id is not None:
            legacy_stock = await self.session.scalar(
                select(BranchInventory).where(
                    BranchInventory.laptop_id == legacy_id,
                    BranchInventory.branch_id == branch_id,
                )
            )
            if legacy_stock is None:
                legacy_stock = BranchInventory(
                    laptop_id=legacy_id,
                    branch_id=branch_id,
                    quantity=quantity,
                    reserved_count=stock.reserved_count,
                    min_stock_alert=0,
                )
                self.session.add(legacy_stock)
            else:
                legacy_stock.quantity = quantity
                legacy_stock.reserved_count = stock.reserved_count
            if delta:
                self.session.add(
                    InventoryAuditLog(
                        laptop_id=legacy_id,
                        source_branch_id=branch_id if delta < 0 else None,
                        destination_branch_id=branch_id if delta > 0 else None,
                        change_type="TREE_SET",
                        quantity=abs(delta),
                        actor_telegram_id=actor_telegram_id,
                        timestamp=datetime.utcnow(),
                        reason=f"ثبت از کاتالوگ درختی؛ variant_id={variant_id}",
                    )
                )
        await self.session.commit()
        return stock

    async def create_branch(
        self,
        name: str,
        code: str,
        phone: str | None = None,
        address: str | None = None,
    ) -> Branch:
        branch = Branch(name=name, code=code, phone=phone, address=address, is_active=True)
        self.session.add(branch)
        await self.session.commit()
        return branch

    async def create_brand(self, name: str) -> LaptopBrand:
        brand = LaptopBrand(name=name)
        self.session.add(brand)
        await self.session.commit()
        return brand

    async def create_laptop(
        self,
        brand_id: int,
        model: str,
        part_number: str | None = None,
        cpu: str | None = None,
        ram: str | None = None,
        gpu: str | None = None,
        storage: str | None = None,
        screen_size: str | None = None,
        color: str | None = None,
        image_url: str | None = None,
    ) -> Laptop:
        laptop = Laptop(
            brand_id=brand_id,
            model=model,
            part_number=part_number,
            cpu=cpu,
            ram=ram,
            gpu=gpu,
            storage=storage,
            screen_size=screen_size,
            color=color,
            image_url=image_url,
            status="active",
        )
        self.session.add(laptop)
        await self.session.commit()
        return laptop

    async def list_active_laptops(self) -> list[Laptop]:
        result = await self.session.execute(
            select(Laptop)
            .options(joinedload(Laptop.brand))
            .where(Laptop.status == "active")
            .order_by(Laptop.model, Laptop.id)
        )
        return list(result.scalars().all())

    async def get_laptop_variants(self, brand_name: str, model: str) -> list[Laptop]:
        result = await self.session.execute(
            select(Laptop)
            .join(LaptopBrand, LaptopBrand.id == Laptop.brand_id)
            .options(joinedload(Laptop.brand))
            .where(
                LaptopBrand.name == brand_name,
                Laptop.model == model,
                Laptop.status == "active",
            )
            .order_by(Laptop.ram, Laptop.cpu, Laptop.gpu, Laptop.storage)
        )
        return list(result.scalars().all())

    async def get_or_create_laptop_variant(
        self,
        brand_name: str,
        model: str,
        cpu: str,
        ram: str,
        gpu: str,
        storage: str,
    ) -> Laptop:
        brand = await self.session.scalar(
            select(LaptopBrand).where(LaptopBrand.name.ilike(brand_name.strip()))
        )
        if brand is None:
            brand = LaptopBrand(name=brand_name.strip())
            self.session.add(brand)
            await self.session.flush()

        variant = await self.session.scalar(
            select(Laptop).where(
                Laptop.brand_id == brand.id,
                Laptop.model.ilike(model.strip()),
                Laptop.cpu.ilike(cpu.strip()),
                Laptop.ram.ilike(ram.strip()),
                Laptop.gpu.ilike(gpu.strip()),
                Laptop.storage.ilike(storage.strip()),
            )
        )
        if variant is None:
            variant = Laptop(
                brand_id=brand.id,
                model=model.strip(),
                cpu=cpu.strip(),
                ram=ram.strip(),
                gpu=gpu.strip(),
                storage=storage.strip(),
                status="active",
            )
            self.session.add(variant)
        await self.session.commit()
        return variant

    async def upsert_inventory(self, laptop_id: int, branch_id: int, quantity: int, min_stock_alert: int = 0) -> BranchInventory:
        row = await self.session.scalar(
            select(BranchInventory).where(
                BranchInventory.laptop_id == laptop_id,
                BranchInventory.branch_id == branch_id,
            )
        )
        if row is None:
            row = BranchInventory(
                laptop_id=laptop_id,
                branch_id=branch_id,
                quantity=quantity,
                reserved_count=0,
                min_stock_alert=min_stock_alert,
            )
            self.session.add(row)
        else:
            row.quantity = quantity
            row.min_stock_alert = min_stock_alert
        await self._sync_tree_stock(laptop_id, branch_id, row.quantity, row.reserved_count)
        await self.session.commit()
        return row

    async def transfer_stock(
        self,
        laptop_id: int,
        source_branch_id: int,
        destination_branch_id: int,
        quantity: int,
        actor_telegram_id: int,
        reason: str = "انتقال بین شعب",
    ) -> None:
        if quantity <= 0:
            raise ValueError("تعداد انتقال باید بیشتر از صفر باشد.")
        if source_branch_id == destination_branch_id:
            raise ValueError("شعبه مبدا و مقصد باید متفاوت باشند.")

        async with self.session.begin_nested():
            source = await self.session.scalar(
                select(BranchInventory).where(
                    BranchInventory.laptop_id == laptop_id,
                    BranchInventory.branch_id == source_branch_id,
                )
            )
            destination = await self.session.scalar(
                select(BranchInventory).where(
                    BranchInventory.laptop_id == laptop_id,
                    BranchInventory.branch_id == destination_branch_id,
                )
            )

            if source is None or source.quantity - source.reserved_count < quantity:
                raise ValueError("موجودی انبار مبدا برای انتقال کافی نیست.")

            if destination is None:
                destination = BranchInventory(
                    laptop_id=laptop_id,
                    branch_id=destination_branch_id,
                    quantity=0,
                    reserved_count=0,
                    min_stock_alert=0,
                )
                self.session.add(destination)

            source.quantity -= quantity
            destination.quantity += quantity
            await self._sync_tree_stock(
                laptop_id, source_branch_id, source.quantity, source.reserved_count
            )
            await self._sync_tree_stock(
                laptop_id, destination_branch_id, destination.quantity, destination.reserved_count
            )

            self.session.add(
                InventoryAuditLog(
                    laptop_id=laptop_id,
                    source_branch_id=source_branch_id,
                    destination_branch_id=destination_branch_id,
                    change_type="TRANSFER",
                    quantity=quantity,
                    actor_telegram_id=actor_telegram_id,
                    timestamp=datetime.utcnow(),
                    reason=reason,
                )
            )
        await self.session.commit()

    async def add_stock(
        self,
        laptop_id: int,
        branch_id: int,
        quantity: int,
        actor_telegram_id: int,
        reason: str = "افزایش دستی موجودی",
    ) -> BranchInventory:
        if quantity <= 0:
            raise ValueError("تعداد افزایش موجودی باید بیشتر از صفر باشد.")

        row = await self.session.scalar(
            select(BranchInventory).where(
                BranchInventory.laptop_id == laptop_id,
                BranchInventory.branch_id == branch_id,
            )
        )
        if row is None:
            row = BranchInventory(
                laptop_id=laptop_id,
                branch_id=branch_id,
                quantity=0,
                reserved_count=0,
                min_stock_alert=0,
            )
            self.session.add(row)

        row.quantity += quantity
        await self._sync_tree_stock(laptop_id, branch_id, row.quantity, row.reserved_count)
        self.session.add(
            InventoryAuditLog(
                laptop_id=laptop_id,
                source_branch_id=None,
                destination_branch_id=branch_id,
                change_type="IN",
                quantity=quantity,
                actor_telegram_id=actor_telegram_id,
                timestamp=datetime.utcnow(),
                reason=reason,
            )
        )
        await self.session.commit()
        return row

    async def set_stock_quantity(
        self,
        laptop_id: int,
        branch_id: int,
        quantity: int,
        actor_telegram_id: int,
        reason: str = "ثبت موجودی نهایی از ویزارد",
    ) -> BranchInventory:
        if quantity < 0:
            raise ValueError("موجودی نهایی نمی‌تواند منفی باشد.")
        row = await self.session.scalar(
            select(BranchInventory).where(
                BranchInventory.laptop_id == laptop_id,
                BranchInventory.branch_id == branch_id,
            )
        )
        if row is None:
            row = BranchInventory(
                laptop_id=laptop_id,
                branch_id=branch_id,
                quantity=0,
                reserved_count=0,
                min_stock_alert=0,
            )
            self.session.add(row)
        if quantity < row.reserved_count:
            raise ValueError("موجودی نهایی نمی‌تواند کمتر از تعداد رزروشده باشد.")

        delta = quantity - row.quantity
        row.quantity = quantity
        await self._sync_tree_stock(laptop_id, branch_id, row.quantity, row.reserved_count)
        if delta:
            self.session.add(
                InventoryAuditLog(
                    laptop_id=laptop_id,
                    source_branch_id=branch_id if delta < 0 else None,
                    destination_branch_id=branch_id if delta > 0 else None,
                    change_type="SET",
                    quantity=abs(delta),
                    actor_telegram_id=actor_telegram_id,
                    timestamp=datetime.utcnow(),
                    reason=reason,
                )
            )
        await self.session.commit()
        return row

    async def deduct_stock(
        self,
        laptop_id: int,
        branch_id: int,
        quantity: int,
        actor_telegram_id: int,
        reason: str = "کاهش دستی موجودی",
    ) -> BranchInventory:
        if quantity <= 0:
            raise ValueError("تعداد کاهش موجودی باید بیشتر از صفر باشد.")

        row = await self.session.scalar(
            select(BranchInventory).where(
                BranchInventory.laptop_id == laptop_id,
                BranchInventory.branch_id == branch_id,
            )
        )
        if row is None or row.quantity - row.reserved_count < quantity:
            raise ValueError("موجودی کافی برای کسر وجود ندارد.")

        row.quantity -= quantity
        await self._sync_tree_stock(laptop_id, branch_id, row.quantity, row.reserved_count)
        self.session.add(
            InventoryAuditLog(
                laptop_id=laptop_id,
                source_branch_id=branch_id,
                destination_branch_id=None,
                change_type="OUT",
                quantity=quantity,
                actor_telegram_id=actor_telegram_id,
                timestamp=datetime.utcnow(),
                reason=reason,
            )
        )
        await self.session.commit()
        return row

    async def start_audit(self, branch_id: int, auditor_telegram_id: int) -> StockAuditChecklist:
        branch = await self.session.get(Branch, branch_id)
        if branch is None or not branch.is_active:
            raise ValueError("شعبه فعال پیدا نشد.")

        active_audit = await self.session.scalar(
            select(StockAuditChecklist.id).where(
                StockAuditChecklist.branch_id == branch_id,
                StockAuditChecklist.status == "in_progress",
            )
        )
        if active_audit is not None:
            raise ValueError(f"برای این شعبه انبارگردانی باز با شناسه {active_audit} وجود دارد.")

        audit = StockAuditChecklist(
            branch_id=branch_id,
            auditor_telegram_id=auditor_telegram_id,
            status="in_progress",
            created_at=datetime.utcnow(),
            notes="انبارگردانی شروع شده است.",
        )
        self.session.add(audit)
        await self.session.flush()

        inventory_rows = await self.session.execute(
            select(Laptop.id, BranchInventory.quantity)
            .outerjoin(
                BranchInventory,
                (BranchInventory.laptop_id == Laptop.id)
                & (BranchInventory.branch_id == branch_id),
            )
            .where(Laptop.status == "active")
            .order_by(Laptop.model)
        )
        self.session.add_all(
            StockAuditItem(
                audit_id=audit.id,
                laptop_id=laptop_id,
                system_quantity=quantity or 0,
                counted_quantity=0,
                discrepancy=0,
                verified=False,
            )
            for laptop_id, quantity in inventory_rows.all()
        )
        await self.session.commit()
        return audit

    async def submit_audit_item(
        self,
        audit_id: int,
        laptop_id: int,
        counted_quantity: int,
    ) -> StockAuditItem:
        if counted_quantity < 0:
            raise ValueError("تعداد شمارش‌شده نمی‌تواند منفی باشد.")

        audit = await self.session.get(StockAuditChecklist, audit_id)
        if audit is None or audit.status != "in_progress":
            raise ValueError("انبارگردانی باز با این شناسه پیدا نشد.")

        item = await self.session.scalar(
            select(StockAuditItem).where(
                StockAuditItem.audit_id == audit_id,
                StockAuditItem.laptop_id == laptop_id,
            )
        )
        if item is None:
            raise ValueError("این لپ‌تاپ در چک‌لیست این شعبه وجود ندارد.")

        item.counted_quantity = counted_quantity
        item.discrepancy = counted_quantity - item.system_quantity
        item.verified = True
        await self.session.commit()
        return item

    async def finish_audit(self, audit_id: int, notes: str | None = None) -> StockAuditChecklist:
        audit = await self.session.get(StockAuditChecklist, audit_id)
        if audit is None or audit.status != "in_progress":
            raise ValueError("انبارگردانی باز با این شناسه پیدا نشد.")

        items = (
            await self.session.scalars(
                select(StockAuditItem).where(StockAuditItem.audit_id == audit_id)
            )
        ).all()
        unverified_count = sum(not item.verified for item in items)
        if unverified_count:
            raise ValueError(f"{unverified_count} قلم هنوز شمارش نشده است.")

        for item in items:
            stock = await self.session.scalar(
                select(BranchInventory).where(
                    BranchInventory.laptop_id == item.laptop_id,
                    BranchInventory.branch_id == audit.branch_id,
                )
            )
            if stock is None:
                stock = BranchInventory(
                    laptop_id=item.laptop_id,
                    branch_id=audit.branch_id,
                    quantity=item.counted_quantity,
                    reserved_count=0,
                    min_stock_alert=0,
                )
                self.session.add(stock)
            else:
                if item.counted_quantity < stock.reserved_count:
                    raise ValueError("شمارش کمتر از تعداد رزروشده است؛ ابتدا رزروها را بررسی کنید.")
                stock.quantity = item.counted_quantity
            await self._sync_tree_stock(
                item.laptop_id,
                audit.branch_id,
                int(item.counted_quantity),
                stock.reserved_count,
            )

            if item.discrepancy:
                self.session.add(
                    InventoryAuditLog(
                        laptop_id=item.laptop_id,
                        source_branch_id=audit.branch_id,
                        destination_branch_id=audit.branch_id,
                        change_type="AUDIT_ADJUSTMENT",
                        quantity=abs(item.discrepancy),
                        actor_telegram_id=audit.auditor_telegram_id,
                        timestamp=datetime.utcnow(),
                        reason=f"اصلاح انبارگردانی #{audit_id}: اختلاف {item.discrepancy:+d}",
                    )
                )

        audit.status = "completed"
        audit.notes = notes or audit.notes
        audit.closed_at = datetime.utcnow()
        await self.session.commit()
        return audit

    async def record_audit_pending_approval(self, audit_id: int) -> tuple[StockAuditChecklist, list[dict[str, Any]]]:
        audit = await self.session.get(StockAuditChecklist, audit_id)
        if audit is None:
            raise ValueError("انبارگردانی یافت نشد.")
        audit.status = "pending_approval"
        await self.session.commit()
        details = await self.get_audit_discrepancy_details(audit_id)
        return audit, details

    async def get_audit_discrepancy_details(self, audit_id: int) -> list[dict[str, Any]]:
        result = await self.session.execute(
            select(
                StockAuditItem.laptop_id,
                LaptopBrand.name,
                Laptop.model,
                StockAuditItem.system_quantity,
                StockAuditItem.counted_quantity,
                StockAuditItem.discrepancy,
                StockAuditItem.verified,
            )
            .join(Laptop, Laptop.id == StockAuditItem.laptop_id)
            .join(LaptopBrand, LaptopBrand.id == Laptop.brand_id)
            .where(StockAuditItem.audit_id == audit_id)
            .order_by(LaptopBrand.name, Laptop.model)
        )
        return [
            {
                "laptop_id": laptop_id,
                "brand": brand,
                "model": model,
                "system_quantity": sys_qty,
                "counted_quantity": cnt_qty,
                "discrepancy": disc,
                "verified": ver,
            }
            for laptop_id, brand, model, sys_qty, cnt_qty, disc, ver in result.all()
        ]

    async def approve_and_apply_audit(
        self,
        audit_id: int,
        approver_telegram_id: int,
        notes: str | None = None,
    ) -> StockAuditChecklist:
        audit = await self.session.get(StockAuditChecklist, audit_id)
        if audit is None:
            raise ValueError("انبارگردانی یافت نشد.")
        if audit.status == "completed":
            return audit
        if audit.status not in ("pending_approval", "in_progress"):
            raise ValueError(f"وضعیت انبارگردانی '{audit.status}' قابل تأیید نیست.")

        items = list((await self.session.scalars(
            select(StockAuditItem).where(StockAuditItem.audit_id == audit_id)
        )).all())

        for item in items:
            stock = await self.session.scalar(
                select(BranchInventory).where(
                    BranchInventory.laptop_id == item.laptop_id,
                    BranchInventory.branch_id == audit.branch_id,
                )
            )
            if stock is None:
                stock = BranchInventory(
                    laptop_id=item.laptop_id,
                    branch_id=audit.branch_id,
                    quantity=item.counted_quantity,
                    reserved_count=0,
                    min_stock_alert=0,
                )
                self.session.add(stock)
            else:
                if item.counted_quantity < stock.reserved_count:
                    raise ValueError(f"شمارش برای لپ‌تاپ #{item.laptop_id} کمتر از تعداد رزروشده است.")
                stock.quantity = item.counted_quantity

            await self._sync_tree_stock(
                item.laptop_id,
                audit.branch_id,
                int(item.counted_quantity),
                stock.reserved_count,
            )

            if item.discrepancy:
                self.session.add(
                    InventoryAuditLog(
                        laptop_id=item.laptop_id,
                        source_branch_id=audit.branch_id if item.discrepancy < 0 else None,
                        destination_branch_id=audit.branch_id if item.discrepancy > 0 else None,
                        change_type="AUDIT_ADJUSTMENT",
                        quantity=abs(item.discrepancy),
                        actor_telegram_id=approver_telegram_id,
                        timestamp=datetime.utcnow(),
                        reason=f"تأیید انبارگردانی #{audit_id} توسط مدیر {approver_telegram_id}: مغایرت {item.discrepancy:+d}",
                    )
                )

        audit.status = "completed"
        audit.approved_by_telegram_id = approver_telegram_id
        audit.approved_at = datetime.utcnow()
        if notes:
            audit.notes = (audit.notes or "") + f" | تأیید: {notes}"
        audit.closed_at = datetime.utcnow()
        await self.session.commit()
        return audit

    async def get_audit_summary(self, audit_id: int) -> list[dict[str, int | bool]]:
        result = await self.session.execute(
            select(StockAuditItem.counted_quantity, StockAuditItem.discrepancy)
            .where(StockAuditItem.audit_id == audit_id)
        )
        return [
            {
                "counted_quantity": counted_quantity or 0,
                "discrepancy": discrepancy or 0,
            }
            for counted_quantity, discrepancy in result.all()
        ]

    async def get_audit_items(self, audit_id: int) -> list[dict[str, Any]]:
        result = await self.session.execute(
            select(
                StockAuditItem.id,
                StockAuditItem.laptop_id,
                StockAuditItem.system_quantity,
                LaptopBrand.name,
                Laptop.model,
                Laptop.cpu,
                Laptop.ram,
                Laptop.gpu,
                Laptop.storage,
            )
            .join(Laptop, Laptop.id == StockAuditItem.laptop_id)
            .join(LaptopBrand, LaptopBrand.id == Laptop.brand_id)
            .where(StockAuditItem.audit_id == audit_id)
            .order_by(LaptopBrand.name, Laptop.model)
        )
        return [
            {
                "item_id": item_id,
                "laptop_id": laptop_id,
                "system_quantity": quantity,
                "brand": brand,
                "model": model,
                "cpu": cpu,
                "ram": ram,
                "gpu": gpu,
                "storage": storage,
            }
            for item_id, laptop_id, quantity, brand, model, cpu, ram, gpu, storage in result.all()
        ]

    async def search_laptops(self, query: str) -> list[Laptop]:
        normalized_query = query.strip()
        if not normalized_query:
            return []

        search_term = f"%{normalized_query}%"
        result = await self.session.execute(
            select(Laptop)
            .options(joinedload(Laptop.brand))
            .join(LaptopBrand, LaptopBrand.id == Laptop.brand_id)
            .where(
                (Laptop.model.ilike(search_term))
                | (Laptop.part_number.ilike(search_term))
                | (Laptop.cpu.ilike(search_term))
                | (LaptopBrand.name.ilike(search_term))
            )
            .limit(20)
        )
        return list(result.scalars().all())

    async def list_laptop_models(self) -> list[tuple[str, str]]:
        result = await self.session.execute(
            select(Laptop.model, LaptopBrand.name)
            .join(LaptopBrand, LaptopBrand.id == Laptop.brand_id)
            .where(Laptop.status == "active")
            .distinct()
            .order_by(Laptop.model, LaptopBrand.name)
        )
        return [(model, brand) for model, brand in result.all()]

    async def get_laptop_facet_values(
        self,
        model: str,
        field: str,
        brand: str | None = None,
    ) -> list[str]:
        allowed_fields = {"ram", "cpu", "gpu", "storage"}
        if field not in allowed_fields:
            raise ValueError("فیلتر مشخصات نامعتبر است.")
        column = getattr(Laptop, field)
        statement = select(column).where(
            Laptop.model == model,
            Laptop.status == "active",
            column.is_not(None),
        )
        if brand:
            statement = statement.join(LaptopBrand, LaptopBrand.id == Laptop.brand_id).where(
                LaptopBrand.name == brand
            )
        result = await self.session.scalars(statement.distinct().order_by(column))
        return [value for value in result.all() if value]

    async def search_laptops_by_specs(self, filters: dict[str, str | None]) -> list[Laptop]:
        allowed_fields = {"model", "brand", "cpu", "ram", "gpu", "storage"}
        if any(key not in allowed_fields for key in filters):
            raise ValueError("فیلتر مشخصات نامعتبر است.")

        statement = (
            select(Laptop)
            .options(joinedload(Laptop.brand))
            .join(LaptopBrand, LaptopBrand.id == Laptop.brand_id)
            .where(Laptop.status == "active")
        )
        for field, value in filters.items():
            if not value:
                continue
            column = LaptopBrand.name if field == "brand" else getattr(Laptop, field)
            statement = statement.where(column.ilike(f"%{value.strip()}%"))
        result = await self.session.execute(statement.limit(30))
        return list(result.scalars().all())

    async def get_laptop_branch_details(self, laptop_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
        if not laptop_ids:
            return {}

        result = await self.session.execute(
            select(
                BranchInventory.branch_id,
                BranchInventory.laptop_id,
                Branch.name,
                Branch.phone,
                Branch.address_full,
                Branch.address,
                Branch.latitude,
                Branch.longitude,
                BranchInventory.quantity,
                BranchInventory.reserved_count,
                Laptop.cpu,
                Laptop.ram,
                Laptop.gpu,
                Laptop.storage,
            )
            .join(Branch, Branch.id == BranchInventory.branch_id)
            .join(Laptop, Laptop.id == BranchInventory.laptop_id)
            .where(BranchInventory.laptop_id.in_(laptop_ids), Branch.is_active.is_(True))
            .order_by(Branch.name)
        )
        rows: dict[int, list[dict[str, Any]]] = {}
        for (
            branch_id, laptop_id, name, phone, address_full, address, latitude, longitude,
            quantity, reserved, cpu, ram, gpu, storage,
        ) in result.all():
            rows.setdefault(laptop_id, []).append({
                "branch_id": branch_id,
                "name": name,
                "phone": phone,
                "address": address_full or address,
                "latitude": latitude,
                "longitude": longitude,
                "quantity": quantity,
                "available": max(quantity - reserved, 0),
                "cpu": cpu,
                "ram": ram,
                "gpu": gpu,
                "storage": storage,
            })
        return rows

    async def get_laptop_availability(self, laptop_ids: list[int]) -> dict[int, list[dict[str, str | int]]]:
        if not laptop_ids:
            return {}

        result = await self.session.execute(
            select(BranchInventory.laptop_id, Branch.name, BranchInventory.quantity, BranchInventory.reserved_count)
            .join(Branch, Branch.id == BranchInventory.branch_id)
            .where(
                BranchInventory.laptop_id.in_(laptop_ids),
                Branch.is_active.is_(True),
            )
            .order_by(Branch.name)
        )
        availability: dict[int, list[dict[str, str | int]]] = {}
        for laptop_id, branch_name, quantity, reserved_count in result.all():
            availability.setdefault(laptop_id, []).append({
                "branch": branch_name,
                "quantity": quantity,
                "available": max(quantity - reserved_count, 0),
            })
        return availability

    async def get_inventory_by_branch(self, branch_id: int) -> list[dict[str, Any]]:
        result = await self.session.execute(
            select(BranchInventory, Laptop.model, Branch.name)
            .join(Laptop, Laptop.id == BranchInventory.laptop_id)
            .join(Branch, Branch.id == BranchInventory.branch_id)
            .where(BranchInventory.branch_id == branch_id)
        )
        rows: list[dict[str, Any]] = []
        for inventory, model_name, branch_name in result.all():
            rows.append({
                "model": model_name,
                "branch": branch_name,
                "quantity": inventory.quantity,
                "reserved_count": inventory.reserved_count,
                "min_stock_alert": inventory.min_stock_alert,
            })
        return rows

    async def get_low_stock_alerts(self) -> list[dict[str, Any]]:
        result = await self.session.execute(
            select(BranchInventory, Laptop.model, Branch.name)
            .join(Laptop, Laptop.id == BranchInventory.laptop_id)
            .join(Branch, Branch.id == BranchInventory.branch_id)
            .where(BranchInventory.quantity <= BranchInventory.min_stock_alert)
        )
        rows: list[dict[str, Any]] = []
        for inventory, model_name, branch_name in result.all():
            rows.append({
                "model": model_name,
                "branch": branch_name,
                "quantity": inventory.quantity,
                "threshold": inventory.min_stock_alert,
            })
        return rows

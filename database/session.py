from __future__ import annotations

from sqlalchemy import func, select, inspect
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.config import get_settings

settings = get_settings()
engine = create_async_engine(settings.DATABASE_URL, echo=False, future=True)
AsyncSessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


async def create_db() -> None:
    from database.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)

    async with AsyncSessionLocal() as session:
        await backfill_legacy_catalog(session)


async def backfill_legacy_catalog(session) -> None:
    from database.models import (
        BranchInventory,
        BranchStock,
        Laptop,
        LaptopBrand,
        LaptopModel,
        LaptopSeries,
        LaptopVariant,
    )

    legacy_rows = await session.execute(
        select(Laptop, LaptopBrand.name)
        .join(LaptopBrand, LaptopBrand.id == Laptop.brand_id)
        .where(Laptop.status == "active")
    )
    for laptop, _brand_name in legacy_rows.all():
        series_name = laptop.model.split(maxsplit=1)[0] if laptop.model.strip() else "Other"
        series = await session.scalar(
            select(LaptopSeries).where(
                LaptopSeries.brand_id == laptop.brand_id,
                func.lower(LaptopSeries.name) == series_name.lower(),
            )
        )
        if series is None:
            series = LaptopSeries(brand_id=laptop.brand_id, name=series_name)
            session.add(series)
            await session.flush()

        model = await session.scalar(
            select(LaptopModel).where(
                LaptopModel.series_id == series.id,
                func.lower(LaptopModel.name) == laptop.model.lower(),
            )
        )
        if model is None:
            model = LaptopModel(series_id=series.id, name=laptop.model, is_active=True)
            session.add(model)
            await session.flush()

        variant = await session.scalar(
            select(LaptopVariant).where(LaptopVariant.legacy_laptop_id == laptop.id)
        )
        if variant is None:
            variant = LaptopVariant(
                model_id=model.id,
                generation="نامشخص",
                cpu=laptop.cpu,
                ram=laptop.ram,
                gpu=laptop.gpu,
                storage=laptop.storage,
                legacy_laptop_id=laptop.id,
                is_active=True,
            )
            session.add(variant)
            await session.flush()

        legacy_stocks = await session.scalars(
            select(BranchInventory).where(BranchInventory.laptop_id == laptop.id)
        )
        for legacy_stock in legacy_stocks.all():
            tree_stock = await session.scalar(
                select(BranchStock).where(
                    BranchStock.variant_id == variant.id,
                    BranchStock.branch_id == legacy_stock.branch_id,
                )
            )
            if tree_stock is None:
                session.add(
                    BranchStock(
                        variant_id=variant.id,
                        branch_id=legacy_stock.branch_id,
                        quantity=legacy_stock.quantity,
                        reserved_count=legacy_stock.reserved_count,
                    )
                )
            else:
                tree_stock.quantity = legacy_stock.quantity
                tree_stock.reserved_count = legacy_stock.reserved_count
    await session.commit()


def _add_missing_columns(connection) -> None:
    inspector = inspect(connection)
    tables = inspector.get_table_names()

    if "branches" in tables:
        branch_columns = {column["name"] for column in inspector.get_columns("branches")}
        for name, sql_type in (
            ("address_full", "TEXT"),
            ("latitude", "FLOAT"),
            ("longitude", "FLOAT"),
        ):
            if name not in branch_columns:
                connection.exec_driver_sql(f"ALTER TABLE branches ADD COLUMN {name} {sql_type}")

    if "users" in tables:
        user_columns = {column["name"] for column in inspector.get_columns("users")}
        if "accounting_access" not in user_columns:
            connection.exec_driver_sql("ALTER TABLE users ADD COLUMN accounting_access BOOLEAN NOT NULL DEFAULT 0")
        if "view_panel" not in user_columns:
            connection.exec_driver_sql("ALTER TABLE users ADD COLUMN view_panel VARCHAR(20) NOT NULL DEFAULT 'customer'")
        if "product_edit_allowed" not in user_columns:
            connection.exec_driver_sql("ALTER TABLE users ADD COLUMN product_edit_allowed BOOLEAN NOT NULL DEFAULT 1")
        if "referrer_telegram_id" not in user_columns:
            connection.exec_driver_sql("ALTER TABLE users ADD COLUMN referrer_telegram_id BIGINT")
        if "managed_branch_id" not in user_columns:
            connection.exec_driver_sql(
                "ALTER TABLE users ADD COLUMN managed_branch_id INTEGER REFERENCES branches(id)"
            )
        for name, sql_type in (("first_name", "VARCHAR(120)"), ("phone_number", "VARCHAR(32)"), ("joined_at", "TIMESTAMP"), ("crm_stage", "VARCHAR(40)"), ("warranty_stage", "VARCHAR(40)"), ("first_seen_at", "TIMESTAMP")):
            if name not in user_columns:
                connection.exec_driver_sql(f"ALTER TABLE users ADD COLUMN {name} {sql_type}")

    if "laptops" in tables:
        laptop_columns = {column["name"] for column in inspector.get_columns("laptops")}
        for name, sql_type in (
            ("condition", "VARCHAR(40) DEFAULT 'نو'"),
            ("warranty", "VARCHAR(120)"),
            ("price", "BIGINT DEFAULT 0"),
            ("purchase_price", "BIGINT DEFAULT 0"),
        ):
            if name not in laptop_columns:
                connection.exec_driver_sql(f"ALTER TABLE laptops ADD COLUMN {name} {sql_type}")

    if "purchase_requests" in tables:
        request_columns = {column["name"] for column in inspector.get_columns("purchase_requests")}
        if "referrer_telegram_id" not in request_columns:
            connection.exec_driver_sql("ALTER TABLE purchase_requests ADD COLUMN referrer_telegram_id BIGINT")

    if "laptop_variants" in tables:
        variant_columns = {column["name"] for column in inspector.get_columns("laptop_variants")}
        for name, sql_type in (
            ("condition", "VARCHAR(40) DEFAULT 'نو'"),
            ("warranty", "VARCHAR(120)"),
            ("price", "BIGINT DEFAULT 0"),
            ("purchase_price", "BIGINT DEFAULT 0"),
        ):
            if name not in variant_columns:
                connection.exec_driver_sql(f"ALTER TABLE laptop_variants ADD COLUMN {name} {sql_type}")

    if "stock_audit_checklists" in tables:
        audit_columns = {column["name"] for column in inspector.get_columns("stock_audit_checklists")}
        if "approved_by_telegram_id" not in audit_columns:
            connection.exec_driver_sql("ALTER TABLE stock_audit_checklists ADD COLUMN approved_by_telegram_id BIGINT")
        if "approved_at" not in audit_columns:
            connection.exec_driver_sql("ALTER TABLE stock_audit_checklists ADD COLUMN approved_at TIMESTAMP")

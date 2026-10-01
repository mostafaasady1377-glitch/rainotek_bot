from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class SheetSyncState(Base):
    __tablename__ = "sheet_sync_state"
    source: Mapped[str] = mapped_column(String(180), primary_key=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class Branch(Base):
    __tablename__ = "branches"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    address_full: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    inventory_rows: Mapped[list["BranchInventory"]] = relationship(back_populates="branch")
    audit_rows: Mapped[list["StockAuditChecklist"]] = relationship(back_populates="branch")


class LaptopBrand(Base):
    __tablename__ = "laptop_brands"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)

    laptops: Mapped[list["Laptop"]] = relationship(back_populates="brand")
    series_rows: Mapped[list["LaptopSeries"]] = relationship(back_populates="brand")


class LaptopSeries(Base):
    __tablename__ = "laptop_series"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("laptop_brands.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (UniqueConstraint("brand_id", "name", name="uq_series_brand_name"),)

    brand: Mapped[LaptopBrand] = relationship(back_populates="series_rows")
    models: Mapped[list["LaptopModel"]] = relationship(back_populates="series")


class LaptopModel(Base):
    __tablename__ = "laptop_models"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("laptop_series.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    use_case: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    __table_args__ = (UniqueConstraint("series_id", "name", name="uq_model_series_name"),)

    series: Mapped[LaptopSeries] = relationship(back_populates="models")
    variants: Mapped[list["LaptopVariant"]] = relationship(back_populates="model")


class LaptopVariant(Base):
    __tablename__ = "laptop_variants"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("laptop_models.id"), nullable=False, index=True)
    generation: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    cpu: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    ram: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    gpu: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    storage: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    condition: Mapped[Optional[str]] = mapped_column(String(40), default="نو", nullable=True)
    warranty: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    price: Mapped[Optional[int]] = mapped_column(BigInteger, default=0, nullable=True)
    purchase_price: Mapped[Optional[int]] = mapped_column(BigInteger, default=0, nullable=True)
    legacy_laptop_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("laptops.id"), unique=True, nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "model_id", "generation", "cpu", "ram", "gpu", "storage",
            name="uq_variant_configuration",
        ),
    )

    model: Mapped[LaptopModel] = relationship(back_populates="variants")
    legacy_laptop: Mapped[Optional[Laptop]] = relationship()
    branch_stocks: Mapped[list["BranchStock"]] = relationship(back_populates="variant")


class BranchStock(Base):
    __tablename__ = "branch_stock"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("laptop_variants.id"), nullable=False, index=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id"), nullable=False, index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reserved_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (UniqueConstraint("variant_id", "branch_id", name="uq_branch_stock_variant_branch"),)

    variant: Mapped[LaptopVariant] = relationship(back_populates="branch_stocks")
    branch: Mapped[Branch] = relationship()


class Laptop(Base):
    __tablename__ = "laptops"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("laptop_brands.id"), nullable=False)
    model: Mapped[str] = mapped_column(String(180), nullable=False)
    part_number: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    cpu: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    ram: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    gpu: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    storage: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    screen_size: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    color: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    condition: Mapped[Optional[str]] = mapped_column(String(40), default="نو", nullable=True)
    warranty: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    price: Mapped[Optional[int]] = mapped_column(BigInteger, default=0, nullable=True)
    purchase_price: Mapped[Optional[int]] = mapped_column(BigInteger, default=0, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    image_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    brand: Mapped[LaptopBrand] = relationship(back_populates="laptops")
    inventory_rows: Mapped[list["BranchInventory"]] = relationship(back_populates="laptop")
    audit_items: Mapped[list["StockAuditItem"]] = relationship(back_populates="laptop")
    inventory_logs: Mapped[list["InventoryAuditLog"]] = relationship(back_populates="laptop")
    images: Mapped[list["ProductImage"]] = relationship(back_populates="laptop", cascade="all, delete-orphan")
    purchase_requests: Mapped[list["PurchaseRequest"]] = relationship(back_populates="laptop")


class ProductImage(Base):
    __tablename__ = "product_images"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    laptop_id: Mapped[int] = mapped_column(ForeignKey("laptops.id"), nullable=False, index=True)
    image_url: Mapped[str] = mapped_column(Text, nullable=False)
    telegram_file_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    caption: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    laptop: Mapped[Laptop] = relationship(back_populates="images")


class BranchInventory(Base):
    __tablename__ = "branch_inventory"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    laptop_id: Mapped[int] = mapped_column(ForeignKey("laptops.id"), nullable=False)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reserved_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    min_stock_alert: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (UniqueConstraint("laptop_id", "branch_id", name="uq_branch_inventory_laptop_branch"),)

    laptop: Mapped[Laptop] = relationship(back_populates="inventory_rows")
    branch: Mapped[Branch] = relationship(back_populates="inventory_rows")


class StockAuditChecklist(Base):
    __tablename__ = "stock_audit_checklists"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id"), nullable=False)
    auditor_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="in_progress", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    approved_by_telegram_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    branch: Mapped[Branch] = relationship(back_populates="audit_rows")
    items: Mapped[list["StockAuditItem"]] = relationship(back_populates="audit")


class StockAuditItem(Base):
    __tablename__ = "stock_audit_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    audit_id: Mapped[int] = mapped_column(ForeignKey("stock_audit_checklists.id"), nullable=False)
    laptop_id: Mapped[int] = mapped_column(ForeignKey("laptops.id"), nullable=False)
    system_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    counted_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    discrepancy: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    audit: Mapped[StockAuditChecklist] = relationship(back_populates="items")
    laptop: Mapped[Laptop] = relationship(back_populates="audit_items")


class InventoryAuditLog(Base):
    __tablename__ = "inventory_audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    laptop_id: Mapped[int] = mapped_column(ForeignKey("laptops.id"), nullable=False)
    source_branch_id: Mapped[Optional[int]] = mapped_column(ForeignKey("branches.id"), nullable=True)
    destination_branch_id: Mapped[Optional[int]] = mapped_column(ForeignKey("branches.id"), nullable=True)
    change_type: Mapped[str] = mapped_column(String(40), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    laptop: Mapped[Laptop] = relationship(back_populates="inventory_logs")


class PurchaseRequest(Base):
    __tablename__ = "purchase_requests"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    customer_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    customer_name: Mapped[str] = mapped_column(String(200), nullable=False)
    customer_phone: Mapped[str] = mapped_column(String(60), nullable=False)
    laptop_id: Mapped[int] = mapped_column(ForeignKey("laptops.id"), nullable=False, index=True)
    branch_id: Mapped[Optional[int]] = mapped_column(ForeignKey("branches.id"), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="pending", nullable=False)  # pending, confirmed, cancelled
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    laptop: Mapped[Laptop] = relationship(back_populates="purchase_requests")
    branch: Mapped[Optional[Branch]] = relationship()


class SupportRequest(Base):
    __tablename__ = "support_requests"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    customer_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    active_key: Mapped[Optional[str]] = mapped_column(String(80), unique=True, nullable=True)
    branch_id: Mapped[Optional[int]] = mapped_column(ForeignKey("branches.id"), nullable=True)
    assigned_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="waiting", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False, index=True)
    actor_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    action_type: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="completed", nullable=False)
    result_data: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    full_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    role: Mapped[str] = mapped_column(String(40), default="customer", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    managed_branch_id: Mapped[Optional[int]] = mapped_column(ForeignKey("branches.id"), nullable=True)


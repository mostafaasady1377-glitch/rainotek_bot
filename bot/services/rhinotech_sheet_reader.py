from __future__ import annotations

import csv
import io
import os
import re
from datetime import datetime
from typing import Any, Optional

import aiohttp
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    Branch,
    BranchInventory,
    BranchStock,
    Laptop,
    LaptopBrand,
    LaptopModel,
    LaptopSeries,
    LaptopVariant,
    ProductImage,
)
from bot.config import get_settings
from bot.services.branch_service import BranchService, RHINOTECH_BRANCHES
from bot.services.laptop_assets import get_laptop_image_source

DEFAULT_SPREADSHEET_ID = "1jDWTufbdaTqG8gAn8xHp096j91wl9_pDXeKbrZ8KLYg"


def get_active_spreadsheet_id() -> str:
    try:
        cfg = get_settings()
        if cfg.GOOGLE_SHEET_ID and cfg.GOOGLE_SHEET_ID.strip():
            return cfg.GOOGLE_SHEET_ID.strip()
    except Exception:
        pass
    return DEFAULT_SPREADSHEET_ID


BRAND_MAPPINGS: dict[str, str] = {
    "APPLE": "Apple",
    "IPHONE": "Apple iPhone",
    "MICROSOFT": "Microsoft Surface",
    "ASUS": "Asus",
    "HP": "HP",
    "LENOVO": "Lenovo",
    "ACER": "Acer",
    "DELL": "Dell",
    "TOSHIBA": "Toshiba Dynabook",
    "MSI": "MSI Gaming",
    "SONY": "Sony Vaio",
    "MONITOR": "Monitor & All in One",
    "NEC": "NEC Japan",
    "FUJITSU": "Fujitsu Japan",
    "CASE": "Case & Mini PC",
    "ALL IN ONE": "Monitor & All in One",
}


def infer_series_name(brand: str, model: str) -> str:
    m = model.upper()
    b = brand.upper()

    if "APPLE" in b or "IPHONE" in b:
        if "IPHONE" in b or "IPHONE" in m:
            return "iPhone"
        if "AIR" in m:
            return "MacBook Air"
        return "MacBook Pro"

    if "MICROSOFT" in b or "SURFACE" in m:
        if "BOOK" in m:
            return "Surface Book"
        if "LAP" in m:
            return "Surface Laptop"
        if "GO" in m:
            return "Surface Go"
        return "Surface Pro"

    if "ASUS" in b:
        if "TUF" in m:
            return "TUF Gaming"
        if "ROG" in m:
            return "ROG Strix"
        if "ZEN" in m:
            return "ZenBook"
        if "VIVO" in m:
            return "VivoBook"
        return "Asus Classic"

    if "HP" in b:
        if "ZBOOK" in m or "FIREFLY" in m:
            return "ZBook Workstation"
        if "VICTUS" in m or "OMEN" in m:
            return "Victus & Omen Gaming"
        if "ELITE" in m:
            return "EliteBook"
        if "PRO" in m or "645" in m or "650" in m or "470" in m:
            return "ProBook"
        if "ENVY" in m:
            return "Envy Series"
        return "HP Essential"

    if "LENOVO" in b:
        if "THINK" in m or "T4" in m or "T5" in m or "X1" in m or "L5" in m or "W5" in m or "P1" in m:
            return "ThinkPad"
        if "YOGA" in m:
            return "Yoga Touch"
        if "LEGION" in m:
            return "Legion Gaming"
        return "IdeaPad"

    if "DELL" in b:
        if "PRECISION" in m:
            return "Precision Workstation"
        if "LATITUDE" in m or any(num in m for num in ["5420", "5520", "5430", "6430", "5500", "5580", "5540", "6540", "3420", "3590", "7410", "7280"]):
            return "Latitude Business"
        if "XPS" in m:
            return "XPS Premium"
        if "VOSTRO" in m:
            return "Vostro"
        return "Inspiron"

    if "ACER" in b:
        if "NITRO" in m or "PREDATOR" in m:
            return "Predator & Nitro Gaming"
        if "SWIFT" in m:
            return "Swift Ultrabook"
        if "TRAVEL" in m:
            return "TravelMate"
        return "Aspire"

    if "MSI" in b:
        return "MSI Gaming"
    if "TOSHIBA" in b:
        return "Dynabook"
    if "SONY" in b:
        return "Vaio"
    if "NEC" in b:
        return "Lavie & VersaPro"
    if "FUJITSU" in b:
        return "Lifebook"
    if "CASE" in b:
        return "Case & Mini PC"
    if "ALL IN ONE" in b or "MONITOR" in b:
        return "All in One & Monitor"

    return "عمومی"


def infer_generation(cpu: str, model: str) -> str:
    text = f"{cpu} {model}".upper()
    if "M1" in text:
        return "Apple M1"
    if "M2" in text:
        return "Apple M2"
    if "M3" in text:
        return "Apple M3"
    if "RYZEN 9" in text:
        return "AMD Ryzen 9"
    if "RYZEN 7" in text:
        return "AMD Ryzen 7"
    if "RYZEN 5" in text:
        return "AMD Ryzen 5"
    if "RYZEN 3" in text:
        return "AMD Ryzen 3"

    gen_match = re.search(r"I[3579][-\s]?(\d{1,2})\d{2,3}", text)
    if gen_match:
        return f"نسل {gen_match.group(1)}"

    if "13" in text and ("HX" in text or "H" in text or "U" in text):
        return "نسل ۱۳"
    if "12" in text and ("HX" in text or "H" in text or "U" in text):
        return "نسل ۱۲"
    if "11" in text and ("G" in text or "H" in text or "U" in text):
        return "نسل ۱۱"
    if "10" in text and ("G" in text or "U" in text):
        return "نسل ۱۰"
    if "9" in text and ("H" in text):
        return "نسل ۹"
    if "8" in text and ("U" in text):
        return "نسل ۸"
    if "7" in text and ("U" in text or "HQ" in text):
        return "نسل ۷"
    if "6" in text and ("U" in text or "HQ" in text):
        return "نسل ۶"

    return "استاندارد"


# Live inventory only; no catalog enrichment or synthetic defaults.
class RhinotechSheetReader:
    _last_sync_time = None
    _last_sync_status = False
    _last_error_message = None
    _last_content_hash = None
    _cached_items = []

    @classmethod
    def get_last_sync_info(cls):
        return dict(last_sync_time=cls._last_sync_time, last_sync_status=cls._last_sync_status,
                    error_message=cls._last_error_message, cached_items_count=len(cls._cached_items),
                    content_hash=cls._last_content_hash)

    @classmethod
    async def fetch_sheet_csv(cls, gid="0", timeout_seconds=20):
        sheet_id = get_active_spreadsheet_id()
        url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout_seconds)) as client:
            async with client.get(url) as response:
                if response.status != 200:
                    raise ConnectionError(f"Sheet HTTP {response.status}")
                content = await response.text(encoding="utf-8")
                if content.lstrip().lower().startswith(("<!doctype", "<html")):
                    raise ValueError("Sheet returned HTML instead of CSV")
                return content

    @classmethod
    def parse_csv(cls, content):
        rows = list(csv.reader(io.StringIO(content)))
        header = next((row for row in rows if "شعبه" in row and "مدل" in row), None)
        if header is None:
            raise ValueError("Inventory header missing; existing inventory preserved")
        price_column = next((index for index, value in enumerate(header) if value.strip() == "قیمت"), 7)
        columns = {value.strip(): index for index, value in enumerate(header) if value.strip()}
        def source_value(row, names):
            index = next((columns[name] for name in names if name in columns), None)
            return row[index].strip() if index is not None and index < len(row) else ""
        items, brand = [], None
        for index, row in enumerate(rows):
            clean = [value.strip() for value in row if value.strip()]
            if not clean:
                continue
            if len(clean) == 1:
                label = clean[0].upper()
                if label in BRAND_MAPPINGS:
                    brand = BRAND_MAPPINGS[label]
                elif not any(word in label for word in ("راینو", "R I N O", "📍", "مترو", "هفته", "میرداماد", "515900")):
                    brand = clean[0]
                continue
            if "شعبه" in clean[0] or "مدل" in clean or not brand:
                continue
            values = (row + [""] * 8)[:8]
            branch, model, cpu, ram, storage, screen, gpu, price = [re.sub(r"\s+", " ", v).strip() for v in values]
            if model.lower() in ("", "x", "xx", "xxx"):
                continue
            price = row[price_column].strip() if price_column < len(row) else ""
            digits = ''.join(c for c in price if c.isdecimal())
            number = int(digits) if digits else 0
            number = number * get_settings().SHEET_PRICE_MULTIPLIER if 0 < number < 1000000 else number
            condition = source_value(row, ("وضعیت", "وضعیت کالا", "نوع کالا", "condition", "Condition"))
            if not condition:
                from bot.services.product_condition import normalize_condition
                condition = normalize_condition(source_value(row, ("توضیحات", "شرح", "Description")) + " " + model) or "ثبت نشده"
            image_url = source_value(row, ("عکس", "تصویر", "لینک عکس", "image_url", "Image"))
            color = ""
            if brand == "Apple iPhone":
                color = cpu
                model = "iPhone " + model + " (" + color + " / " + ram + "GB)"
                condition = branch + " / " + storage + " / " + gpu
                branch, storage, cpu, ram, screen, gpu = "", ram + "GB", "", "", screen, ""
            # Preserve the source specifications, including battery health and cycle count.
            items.append(dict(brand=brand, branch_raw=branch, model=model, cpu=cpu, ram=ram,
                              storage=storage, screen=screen, gpu=gpu, condition=condition, color=color,
                              price_tomans=number, image_url=image_url, sheet_row=index + 1))
        if not items:
            raise ValueError("No valid inventory rows; existing inventory preserved")
        return items

    @classmethod
    async def parse_live_inventory(cls, gid="0"):
        return datetime.utcnow(), cls.parse_csv(await cls.fetch_sheet_csv(gid))

    @classmethod
    async def sync_sheet_to_database(cls, session, csv_content=None, gid="0"):
        import hashlib, json
        from bot.services.stock_lock import stock_lock
        from database.models import SheetSyncState, StockAuditChecklist, InventoryAuditLog, LaptopStaffOverride
        try:
            content = csv_content if csv_content is not None else await cls.fetch_sheet_csv(gid)
            items = cls.parse_csv(content)
            async with stock_lock:
                branch_map = await BranchService.ensure_canonical_branches(session)
                source = f"{get_active_spreadsheet_id()}:{gid}"
                state = await session.get(SheetSyncState, source)
                previous = json.loads(state.payload) if state else {}
                current = {}
                for item in items:
                    config = [item[k].casefold() for k in ("brand", "model", "cpu", "ram", "storage", "gpu", "screen")]
                    key = hashlib.sha256(json.dumps(config, ensure_ascii=False).encode()).hexdigest()
                    if key not in current:
                        current[key] = dict(item=item, counts={})
                    # A slash means a listed unit in each named branch, as in the original sheet.
                    for branch_key in BranchService.normalize_branch_string(item['branch_raw']):
                        branch_id = str(branch_map[branch_key].id)
                        counts = current[key]['counts']
                        counts[branch_id] = counts.get(branch_id, 0) + 1
                if await session.scalar(select(StockAuditChecklist.id).where(StockAuditChecklist.status.in_(['in_progress','pending_approval']))):
                    raise ValueError('Active stock audit; reconciliation deferred')
                used_ids = set()
                for key, entry in current.items():
                    item = entry['item']
                    brand = await session.scalar(select(LaptopBrand).where(LaptopBrand.name == item['brand']))
                    if brand is None:
                        brand = LaptopBrand(name=item['brand']); session.add(brand); await session.flush()
                    old = previous.get(key)
                    laptop = await session.get(Laptop, old['id']) if old else None
                    if laptop is None:
                        candidates = list((await session.scalars(select(Laptop).where(
                            Laptop.brand_id == brand.id, Laptop.model == item['model']))).all())
                        laptop = next((l for l in candidates if all((getattr(l,k) or '').casefold() == item[k].casefold()
                            for k in ('cpu','ram','storage','gpu')) and (l.screen_size or '').casefold() == item['screen'].casefold()), None)
                        # Reuse a single edited configuration only when the match is unambiguous.
                        if laptop is None:
                            incoming = sum(e['item']['brand']==item['brand'] and e['item']['model']==item['model'] for e in current.values())
                            if incoming == 1 and len(candidates) == 1:
                                laptop = candidates[0]
                    if laptop is None:
                        laptop = Laptop(brand_id=brand.id, model=item['model'], status='active')
                        session.add(laptop); await session.flush()
                    used_ids.add(laptop.id)
                    for field in ('cpu','ram','storage','gpu','condition','color'):
                        setattr(laptop, field, item[field])
                    laptop.screen_size = item['screen']; laptop.price = item['price_tomans']; laptop.status = 'active'
                    laptop.warranty = get_settings().STORE_WARRANTY
                    if item.get("image_url", "").startswith(("https://", "http://")):
                        laptop.image_url = item["image_url"]
                    # Generic stock photographs are not evidence of a model's appearance.
                    if laptop.image_url and 'images.unsplash.com' in laptop.image_url:
                        laptop.image_url = None
                    override = await session.get(LaptopStaffOverride, laptop.id)
                    if override:
                        for field in ('cpu', 'ram', 'storage', 'gpu', 'screen_size', 'color', 'condition', 'price'):
                            value = getattr(override, field)
                            if value is not None:
                                setattr(laptop, field, value)
                        if override.image_file_id:
                            laptop.image_url = 'tgfile:' + override.image_file_id
                    series_name = infer_series_name(item['brand'], item['model'])
                    series = await session.scalar(select(LaptopSeries).where(LaptopSeries.brand_id==brand.id, LaptopSeries.name==series_name))
                    if series is None:
                        series= LaptopSeries(brand_id=brand.id,name=series_name); session.add(series); await session.flush()
                    model = await session.scalar(select(LaptopModel).where(LaptopModel.series_id==series.id,LaptopModel.name==item['model']))
                    if model is None:
                        model=LaptopModel(series_id=series.id,name=item['model'],is_active=True); session.add(model); await session.flush()
                    model.is_active=True
                    variant = await session.scalar(select(LaptopVariant).where(LaptopVariant.legacy_laptop_id==laptop.id))
                    if variant is None:
                        variant=LaptopVariant(model_id=model.id,legacy_laptop_id=laptop.id); session.add(variant)
                    variant.model_id=model.id; variant.is_active=True
                    variant.generation=infer_generation(item['cpu'],item['model']) + ' ' + item['screen']
                    for field in ('cpu','ram','storage','gpu','condition'):
                        setattr(variant,field,getattr(laptop,field))
                    variant.price=laptop.price; variant.warranty=get_settings().STORE_WARRANTY
                    await session.flush()
                    # Find prior baseline even when an unambiguous specification edit changed its key.
                    prior = old or next((e for e in previous.values() if e['id']==laptop.id), None)
                    entry['id']=laptop.id; entry['variant_id']=variant.id
                    for branch in branch_map.values():
                        stock = await session.scalar(select(BranchInventory).where(BranchInventory.laptop_id==laptop.id,BranchInventory.branch_id==branch.id))
                        incoming = entry['counts'].get(str(branch.id),0)
                        baseline = prior['counts'].get(str(branch.id),0) if prior else None
                        target = incoming if baseline is None else (stock.quantity if stock else 0) + incoming - baseline
                        if target < 0 or (stock and target < stock.reserved_count):
                            raise ValueError('Sheet/local stock conflict; reconciliation rolled back')
                        if stock is None:
                            stock=BranchInventory(laptop_id=laptop.id,branch_id=branch.id,quantity=target,reserved_count=0)
                            session.add(stock)
                        elif stock.quantity != target:
                            audit = await session.scalar(select(StockAuditChecklist.id).where(StockAuditChecklist.branch_id==branch.id,StockAuditChecklist.status.in_(['in_progress','pending_approval'])))
                            if audit:
                                raise ValueError('Active stock audit; sheet reconciliation deferred')
                            stock.quantity=target
                        tree = await session.scalar(select(BranchStock).where(BranchStock.variant_id==variant.id,BranchStock.branch_id==branch.id))
                        if tree is None:
                            session.add(BranchStock(variant_id=variant.id,branch_id=branch.id,quantity=target,reserved_count=stock.reserved_count or 0))
                        else:
                            tree.quantity=target; tree.reserved_count=stock.reserved_count
                # Retain historical IDs and transactions but retire removed/source-less demo products.
                for laptop in (await session.scalars(select(Laptop))).all():
                    if laptop.id in used_ids:
                        continue
                    prior = next((e for e in previous.values() if e['id']==laptop.id),None)
                    stocks = list((await session.scalars(select(BranchInventory).where(BranchInventory.laptop_id==laptop.id))).all())
                    has_log = await session.scalar(select(InventoryAuditLog.id).where(InventoryAuditLog.laptop_id==laptop.id))
                    if prior is None and (state is not None or has_log):
                        continue  # A manually created product is not owned by the sheet.
                    for stock in stocks:
                        target = stock.quantity - prior['counts'].get(str(stock.branch_id),0) if prior else 0
                        if target < 0 or target < stock.reserved_count:
                            raise ValueError('Removed product has conflicting local/reserved stock')
                        stock.quantity=target
                        variants=(await session.scalars(select(LaptopVariant).where(LaptopVariant.legacy_laptop_id==laptop.id))).all()
                        for v in variants:
                            for tree in (await session.scalars(select(BranchStock).where(BranchStock.variant_id==v.id,BranchStock.branch_id==stock.branch_id))).all():
                                tree.quantity=target; tree.reserved_count=stock.reserved_count
                    if prior:
                        current['retired:' + str(laptop.id)] = dict(prior, counts={}, retired=True)
                    laptop.status='active' if any(s.quantity>0 for s in stocks) else 'inactive'
                    for v in (await session.scalars(select(LaptopVariant).where(LaptopVariant.legacy_laptop_id==laptop.id))).all():
                        v.is_active=laptop.status=='active'
                await session.flush()
                for model in (await session.scalars(select(LaptopModel))).all():
                    active = await session.scalar(select(LaptopVariant.id).where(LaptopVariant.model_id==model.id,LaptopVariant.is_active.is_(True)))
                    model.is_active=active is not None
                payload=json.dumps(current,ensure_ascii=False)
                if state: state.payload=payload
                else: session.add(SheetSyncState(source=source,payload=payload))
                await session.commit()
            cls._last_content_hash=hashlib.sha256(content.encode()).hexdigest()
            cls._last_sync_time=datetime.utcnow(); cls._last_sync_status=True; cls._last_error_message=None; cls._cached_items=items
            return dict(products_synced=sum(not e.get('retired') for e in current.values()),source_rows=len(items),branches_count=len(branch_map),sync_time=cls._last_sync_time.isoformat())
        except Exception as exc:
            await session.rollback()
            cls._last_sync_status=False; cls._last_error_message=type(exc).__name__ + ': ' + str(exc)
            raise

    @classmethod
    async def check_and_sync_changes(cls, session, gid="0"):
        import hashlib
        try:
            content=await cls.fetch_sheet_csv(gid)
            if cls._last_content_hash==hashlib.sha256(content.encode()).hexdigest():
                cls._last_sync_status=True; cls._last_error_message=None
                logger.debug('Sheet check succeeded; content unchanged')
                return dict(changed=False,items_count=len(cls._cached_items))
            return dict(await cls.sync_sheet_to_database(session,content,gid),changed=True)
        except Exception as exc:
            cls._last_sync_status=False; cls._last_error_message=type(exc).__name__
            logger.warning('Sheet sync failed: {}',type(exc).__name__)
            return dict(changed=False,error=type(exc).__name__)

    @classmethod
    async def run_live_sync_loop(cls, interval_seconds=45, bot=None):
        import asyncio
        from database.session import AsyncSessionLocal
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                async with AsyncSessionLocal() as session:
                    await cls.check_and_sync_changes(session)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                cls._last_sync_status = False
                cls._last_error_message = type(exc).__name__
                logger.warning('Live sync loop retry scheduled after {}', type(exc).__name__)

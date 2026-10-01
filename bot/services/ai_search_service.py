from __future__ import annotations

import re
from typing import Any, Optional
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from database.models import Branch, BranchInventory, Laptop, LaptopBrand


class AISearchService:
    """
    موتور جستجوی هوشمند و دستیار انتخاب لپ‌تاپ راینوتک:
    - تحلیل هوشمند متن فارسی (استخراج برند، پردازنده، رم، بودجه، کاربری و شعبه)
    - جستجوی چندلایه‌ای و دقیق در دیتابیس محصولات واقعی
    - پیشنهاد هوشمندانه بهترین مدل‌های متناسب با نیاز مشتری
    """

    @classmethod
    def parse_query_intent(cls, query: str) -> dict[str, Any]:
        text = query.replace("ي", "ی").replace("ك", "ک").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")).strip()
        text = re.sub(r'(?:کور\s*)?آی\s*(سه|پنج|هفت|نه)', lambda m: {'سه':'i3','پنج':'i5','هفت':'i7','نه':'i9'}[m.group(1)], text)
        numbers={'یک':1,'دو':2,'سه':3,'چهار':4,'پنج':5,'شش':6,'هفت':7,'هشت':8,'نه':9,'ده':10,'شانزده':16,'سی و دو':32,'بیست':20,'سی':30,'چهل':40,'پنجاه':50,'شصت':60,'هفتاد':70,'هشتاد':80,'نود':90,'صد':100,'دویست و پنجاه و شش':256,'پانصد و دوازده':512}
        for word, number in sorted(numbers.items(),key=lambda pair:len(pair[0]),reverse=True):
            text=re.sub(r'(?<!\w)'+re.escape(word)+r'(?!\w)',str(number),text)
        lower = text.lower()

        filters: dict[str, Any] = {
            "raw_query": text,
            "brand": None,
            "min_ram": None,
            "cpu_family": None,
            "is_gaming": False,
            "is_touch": False,
            "max_price": None,
            "min_price": None,
            "branch_key": None,
            "keywords": [],
        }

        # ۱. تشخیص برند
        brand_rules = {
            "apple": ["اپل", "مک بوک", "مک‌بوک", "macbook", "apple", "آیفون", "ایفون", "iphone"],
            "microsoft": ["مایکروسافت", "سرفیس", "surface", "microsoft"],
            "asus": ["ایسوس", "اسوس", "asus", "تاف", "tuf", "راگ", "rog", "زنبوک", "zenbook"],
            "lenovo": ["لنوو", "لنووو", "lenovo", "تینک پد", "thinkpad", "لژیون", "legion", "آیدیاپد", "ideapad"],
            "hp": ["اچ پی", "اچ‌پی", "hp", "زدبوک", "zbook", "ویکتوس", "victus", "پروبوک", "probook", "الیت بوک", "elitebook"],
            "dell": ["دل", "dell", "لتیتود", "latitude", "پرسیژن", "precision", "اینسپایرون", "inspiron", "xps"],
            "acer": ["ایسر", "acer", "نیترو", "nitro", "پرداتور", "predator", "اسپایر", "aspire"],
            "msi": ["ام اس آی", "ام‌اس‌آی", "msi", "کاتانا", "katana"],
            "toshiba": ["توشیبا", "داینابوک", "toshiba", "dynabook"],
            "sony": ["سونی", "وایو", "sony", "vaio"],
            "nec": ["ان ای سی", "ان‌سی", "nec"],
            "fujitsu": ["فوجیتسو", "fujitsu"],
        }
        for b_key, synonyms in brand_rules.items():
            if any(re.search(r"(?<!\w)" + re.escape(syn) + r"(?!\w)", lower) for syn in synonyms):
                filters["brand"] = b_key
                break

        # ۲. تشخیص رم
        ram_match = re.search(r"(?:رم|ram)\s*(\d+)", lower) or re.search(r"(\d+)\s*(?:گیگ|gig|gb|رم|ram)", lower)
        if ram_match:
            try:
                filters["min_ram"] = int(ram_match.group(1))
            except ValueError:
                pass
        elif "رم بالا" in lower or "رم ۱۶" in lower or "رم 16" in lower:
            filters["min_ram"] = 16
        elif "رم ۳۲" in lower or "رم 32" in lower:
            filters["min_ram"] = 32

        # ۳. تشخیص پردازنده
        if any(w in lower for w in ["i9", "core i9", "کور آی ۹", "کور i9"]):
            filters["cpu_family"] = "i9"
        elif any(w in lower for w in ["i7", "core i7", "کور آی ۷", "کور i7"]):
            filters["cpu_family"] = "i7"
        elif any(w in lower for w in ["i5", "core i5", "کور آی ۵", "کور i5"]):
            filters["cpu_family"] = "i5"
        elif any(w in lower for w in ["i3", "core i3", "کور آی ۳", "کور i3"]):
            filters["cpu_family"] = "i3"
        elif any(w in lower for w in ["m1", "ام وان", "ام ۱"]):
            filters["cpu_family"] = "m1"
        elif any(w in lower for w in ["m2", "ام تو", "ام ۲"]):
            filters["cpu_family"] = "m2"
        elif any(w in lower for w in ["m3", "ام تری", "ام ۳"]):
            filters["cpu_family"] = "m3"
        elif any(w in lower for w in ["ryzen 9", "رایزن ۹", "رایزن 9"]):
            filters["cpu_family"] = "ryzen 9"
        elif any(w in lower for w in ["ryzen 7", "رایزن ۷", "رایزن 7"]):
            filters["cpu_family"] = "ryzen 7"
        elif any(w in lower for w in ["ryzen 5", "رایزن ۵", "رایزن 5"]):
            filters["cpu_family"] = "ryzen 5"
        elif any(w in lower for w in ["ryzen 3", "رایزن ۳", "رایزن 3"]):
            filters["cpu_family"] = "ryzen 3"

        # ۴. تشخیص کاربری
        if any(w in lower for w in ["گیم", "بازی", "gaming", "گیمینگ", "rtx", "gtx", "گرافیک دار", "گرافیک مجزا"]):
            filters["is_gaming"] = True
        if any(w in lower for w in ["لمسی", "touch", "تبلت", "چرخشی", "360", "سرفیس"]):
            filters["is_touch"] = True

        # ۵. تشخیص بودجه
        # الگوهای عدد به میلیون تومان (مثلاً: "تا ۵۰ میلیون", "زیر ۴۰ تومن", "۳۰ تا ۶۰ تومن")
        million_matches = re.findall(r"(\d+)(?:\s*(?:میلیون|تومن|م|ملیون))?", lower)
        
        budget_text = re.sub(r"(?:رم|ram|هارد|حافظه|ssd|storage)\s*\d+\s*(?:گیگ|gb|tb|ترابایت)?", "", lower)
        range_match = re.search(r"(?<!\w)(\d+)\s*(?:تا|-)\s*(\d+)\s*(?:میلیون|تومن|م|ملیون)", budget_text)
        if range_match:
            try:
                filters["min_price"] = int(range_match.group(1)) * 1_000_000
                filters["max_price"] = int(range_match.group(2)) * 1_000_000
            except ValueError:
                pass
        else:
            under_match = re.search(r"(?:تا|زیر|حداکثر|کمتر از|سقف)\s*(\d+)\s*(?:میلیون|تومن|م|ملیون)?", budget_text)
            if under_match:
                try:
                    val = int(under_match.group(1))
                    if val < 1000:
                        filters["max_price"] = val * 1_000_000
                except ValueError:
                    pass

            above_match = re.search(r"(?:بالای|بیشتر از|حداقل|از)\s*(\d+)\s*(?:میلیون|تومن|م|ملیون)?", budget_text)
            if above_match:
                try:
                    val = int(above_match.group(1))
                    if val < 1000:
                        filters["min_price"] = val * 1_000_000
                except ValueError:
                    pass

        # ۶. تشخیص شعبه
        if "میرداماد" in lower:
            filters["branch_key"] = "میرداماد"
        elif "صادقیه" in lower:
            filters["branch_key"] = "صادقیه"
        elif "هروی" in lower:
            filters["branch_key"] = "هروی"
        elif "شهرک" in lower:
            filters["branch_key"] = "شهرک"
        elif "فلاح" in lower:
            filters["branch_key"] = "فلاح"

        filters['ram_minimum'] = bool(re.search(r'(?:حداقل|بیشتر از)\s*(?:رم|ram)', lower))
        storage = re.search(r'(?:هارد|حافظه|ssd|storage)\s*(\d+)\s*(tb|ترابایت|gb|گیگ)?', lower)
        filters['storage_capacity'] = int(storage.group(1)) * (1024 if storage.group(2) in ('tb', 'ترابایت') else 1) if storage else None
        filters['gpu_model'] = next(iter(re.findall(r'(?:rtx|gtx)\s*\d{3,4}', lower)), None)
        model = re.search(r'(?:مدل|model)\s+([a-z0-9]+(?:\s+[a-z0-9]+)*)', lower)
        filters['model'] = model.group(1).strip() if model else None
        from bot.services.product_condition import normalize_condition
        filters["condition"] = normalize_condition(text)
        return filters

    @classmethod
    async def smart_search(
        cls,
        session: AsyncSession,
        query: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        جستجوی محصولات بر اساس پرس‌وجوی طبیعی کاربر.
        """
        intent = cls.parse_query_intent(query)
        raw = intent["raw_query"]
        tokens = [t for t in re.split(r"[\s,/+\-_]+", raw) if len(t) >= 2]

        stmt = (
            select(Laptop)
            .join(LaptopBrand, Laptop.brand_id == LaptopBrand.id)
            .options(joinedload(Laptop.brand), joinedload(Laptop.inventory_rows).joinedload(BranchInventory.branch))
            .where(Laptop.status == "active")
        )

        # فیلتر برند
        if intent["brand"]:
            b_key = intent["brand"]
            stmt = stmt.where(
                or_(
                    func.lower(LaptopBrand.name).like(f"%{b_key}%"),
                    func.lower(Laptop.model).like(f"%{b_key}%"),
                )
            )

        # فیلتر پردازنده
        if intent["cpu_family"]:
            cpu_val = intent["cpu_family"]
            stmt = stmt.where(
                or_(
                    func.lower(Laptop.cpu).like(f"%{cpu_val}%"),
                    func.lower(Laptop.model).like(f"%{cpu_val}%"),
                )
            )

        # فیلتر گیمینگ
        if intent["is_gaming"]:
            stmt = stmt.where(
                or_(
                    func.lower(Laptop.gpu).like("%rtx%"),
                    func.lower(Laptop.gpu).like("%gtx%"),
                    func.lower(Laptop.gpu).like("%geforce%"),
                    func.lower(Laptop.gpu).like("%radeon%"),
                    func.lower(Laptop.model).like("%tuf%"),
                    func.lower(Laptop.model).like("%rog%"),
                    func.lower(Laptop.model).like("%gaming%"),
                    func.lower(Laptop.model).like("%nitro%"),
                    func.lower(Laptop.model).like("%predator%"),
                    func.lower(Laptop.model).like("%legion%"),
                )
            )

        # فیلتر لمسی
        if intent["is_touch"]:
            stmt = stmt.where(
                or_(
                    func.lower(Laptop.screen_size).like("%touch%"),
                    func.lower(Laptop.screen_size).like("%tab%"),
                    func.lower(Laptop.model).like("%surface%"),
                    func.lower(Laptop.model).like("%yoga%"),
                    func.lower(Laptop.model).like("%360%"),
                    func.lower(Laptop.model).like("%touch%"),
                )
            )

        # فیلتر بودجه
        if intent["max_price"]:
            stmt = stmt.where(Laptop.price > 0, Laptop.price <= intent["max_price"])
        if intent["min_price"]:
            stmt = stmt.where(Laptop.price >= intent["min_price"])

        # اگر هیچ فیلتر سختگیرانه‌ای نبود، جستجوی متنی روی توکن‌ها
        if not any(intent[key] for key in ("brand", "cpu_family", "max_price", "min_price", "min_ram", "storage_capacity", "is_gaming", "is_touch", "branch_key", "model", "condition")) and tokens:
            conditions = []
            for token in tokens[:4]:
                pattern = f"%{token.lower()}%"
                conditions.append(func.lower(Laptop.model).like(pattern))
                conditions.append(func.lower(LaptopBrand.name).like(pattern))
                conditions.append(func.lower(Laptop.cpu).like(pattern))
                conditions.append(func.lower(Laptop.gpu).like(pattern))
            stmt = stmt.where(or_(*conditions))

        if intent["model"]:
            stmt = stmt.where(func.lower(Laptop.model).like(f"%{intent['model']}%"))
        if intent["gpu_model"]:
            stmt = stmt.where(func.lower(func.replace(Laptop.gpu, " ", "")).like(f"%{intent['gpu_model'].replace(' ', '')}%"))
        stmt = stmt.order_by(Laptop.price.asc())
        laptops = list((await session.scalars(stmt)).unique().all())

        results = []
        for lap in laptops:
            from bot.services.product_condition import condition_info
            if intent["condition"] and condition_info(lap)[0] != intent["condition"]:
                continue
            def capacity(value):
                match = re.search(r'(\d+)', (value or '').translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹', '0123456789')))
                if not match:
                    return 0
                return int(match.group(1)) * (1024 if 'tb' in (value or '').lower() or 'ترابایت' in (value or '') else 1)
            if intent['min_ram'] and (capacity(lap.ram) < intent['min_ram'] if intent['ram_minimum'] else capacity(lap.ram) != intent['min_ram']):
                continue
            if intent['storage_capacity'] and capacity(lap.storage) != intent['storage_capacity']:
                continue
            # بررسی موجودی شعب
            branch_stocks = []
            total_qty = 0
            for inv in lap.inventory_rows:
                if inv.branch and inv.branch.is_active and inv.quantity > inv.reserved_count:
                    total_qty += inv.quantity - inv.reserved_count
                    branch_stocks.append({
                        "branch_name": inv.branch.name,
                        "phone": inv.branch.phone,
                        "address": inv.branch.address,
                        "quantity": inv.quantity - inv.reserved_count,
                    })

            # اگر فیلتر شعبه خواسته شده بود و در این شعبه نبود رد شود
            if intent["branch_key"]:
                target_b = intent["branch_key"]
                if not any(target_b in bs["branch_name"] for bs in branch_stocks):
                    continue

            results.append({
                "id": lap.id,
                "brand": lap.brand.name if lap.brand else "",
                "model": lap.model,
                "cpu": lap.cpu,
                "ram": lap.ram,
                "storage": lap.storage,
                "gpu": lap.gpu,
                "screen": lap.screen_size,
                "price": lap.price,
                "image_url": lap.image_url,
                "condition": lap.condition,
                "warranty": lap.warranty,
                "total_quantity": total_qty,
                "branches": branch_stocks,
            })

        return results[:limit]

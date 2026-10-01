from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from database.models import Branch, BranchInventory, BranchStock, Laptop, LaptopModel, LaptopVariant, LaptopBrand, LaptopSeries


@dataclass
class BranchInfo:
    code: str
    name: str
    phone: str
    mobile: str
    address: str
    metro_station: str
    working_hours: str
    latitude: float
    longitude: float
    map_url: str
    map_label: str = "🧭 مسیریابی در نشان"


RHINOTECH_BRANCHES: dict[str, BranchInfo] = {
    "mirdamad": BranchInfo(
        code="BR-MIRDAMAD",
        name="شعبه میرداماد (مرکزی)",
        phone="021-26401850",
        mobile="09120894560",
        address="تهران، خیابان میرداماد، جنب ایستگاه مترو میرداماد، پاساژ رز، طبقه دوم، پلاک TS15",
        metro_station="دقیقاً جنب خروجی ایستگاه مترو میرداماد (خط ۱)",
        working_hours="همه‌روزه (حتی جمعه‌ها و ایام تعطیل) از ساعت ۱۰:۰۰ الی ۲۲:۰۰",
        latitude=35.7592,
        longitude=51.4285,
        map_url="https://nshn.ir/Qbv2Jjexucq3",
    ),
    "sadeghiyeh": BranchInfo(
        code="BR-SADEGHIYEH",
        name="شعبه صادقیه (غرب)",
        phone="021-44287050",
        mobile="09194567820",
        address="تهران، فلکه دوم صادقیه، ابتدای خیابان ستارخان، مجتمع تجاری اداری گلدیس، طبقه چهارم، واحد ۴۰۵",
        metro_station="۵ دقیقه فاصله تا ایستگاه مترو طرشت و صادقیه (خط ۲ و ۵)",
        working_hours="شنبه تا پنج‌شنبه از ساعت ۱۰:۰۰ الی ۲۱:۰۰",
        latitude=35.7208,
        longitude=51.3362,
        map_url="https://nshn.ir/de_bvk9xpxMQkn",
        map_label="🧭 مسیریابی در نشان — مجتمع گلدیس",
    ),
    "heravi": BranchInfo(
        code="BR-HERAVI",
        name="شعبه هروی (شمال‌شرق)",
        phone="021-22986040",
        mobile="09351234580",
        address="تهران، میدان هروی، خیابان موسوی، مرکز خرید الماس هروی، طبقه دوم، پلاک ۲۰",
        metro_station="دسترسی سریع از ایستگاه مترو میدان هروی (خط ۳)",
        working_hours="شنبه تا پنج‌شنبه از ساعت ۱۰:۰۰ الی ۲۱:۰۰",
        latitude=35.7725,
        longitude=51.4795,
        map_url="https://nshn.ir/f7_bvruZyxRpaA",
        map_label="🧭 مسیریابی در نشان — مجتمع الماس هروی",
    ),
    "shahrak": BranchInfo(
        code="BR-SHAHRAK",
        name="شعبه شهرک غرب",
        phone="021-88379020",
        mobile="09128901240",
        address="تهران، شهرک غرب، میدان صنعت، تقاطع بلوار فرحزادی و سیمای ایران، مجتمع تجاری پلاتین، طبقه ۳، واحد ۳۱۰",
        metro_station="دسترسی آسان از ایستگاه مترو میدان صنعت (خط ۷)",
        working_hours="شنبه تا پنج‌شنبه از ساعت ۱۰:۰۰ الی ۲۱:۰۰",
        latitude=35.7533,
        longitude=51.3705,
        map_url="https://nshn.ir/62_bvS1NIx4CvQ",
        map_label="🧭 مسیریابی در نشان — مجتمع پلاتین",
    ),
    "fallah": BranchInfo(
        code="BR-FALLAH",
        name="شعبه فلاح (جنوب‌غرب)",
        phone="021-55708030",
        mobile="09387654310",
        address="تهران، خیابان پیغمبری، سجاد جنوبی، بین شهید مرادبیگی و کیانی، پاساژ بهاران، طبقه اول، پلاک ۱۲",
        metro_station="دسترسی از ایستگاه مترو زمزم و خطوط اتوبوسرانی منطقه ۱۷",
        working_hours="شنبه تا پنج‌شنبه از ساعت ۱۰:۰۰ الی ۲۱:۰۰",
        latitude=35.6565,
        longitude=51.3650,
        map_url="https://nshn.ir/62_bvYHI0x4YWP",
        map_label="🧭 مسیریابی در نشان — پاساژ بهاران",
    ),
}


class BranchService:
    @staticmethod
    def normalize_branch_string(raw: str) -> list[str]:
        """
        تبدیل هر نام شعبه به یک یا چند کلید معتبر شعب راینوتک.
        مثال:
        'شهرک/صادقیه' -> ['shahrak', 'sadeghiyeh']
        'هروی/میرداماد' -> ['heravi', 'mirdamad']
        'صادقيه' -> ['sadeghiyeh']
        'lمیرداماد' -> ['mirdamad']
        """
        if not raw:
            return []

        # نرمال‌سازی حروف عربی به فارسی
        text = raw.replace("ي", "ی").replace("ك", "ک").strip()
        lower = text.lower()

        keys: list[str] = []
        if "میرداماد" in text or "mirdamad" in lower:
            keys.append("mirdamad")
        if "صادقیه" in text or "sadeghiyeh" in lower:
            keys.append("sadeghiyeh")
        if "هروی" in text or "heravi" in lower:
            keys.append("heravi")
        if "شهرک" in text or "shahrak" in lower:
            keys.append("shahrak")
        if "فلاح" in text or "fallah" in lower:
            keys.append("fallah")

        return keys

    @classmethod
    async def ensure_canonical_branches(cls, session: AsyncSession) -> dict[str, Branch]:
        """
        اطمینان از وجود ۵ شعبه اصلی با اطلاعات دقیق و رفع رکوردهای تکراری/ناقص.
        """
        branch_map: dict[str, Branch] = {}
        for key, info in RHINOTECH_BRANCHES.items():
            branch = await session.scalar(select(Branch).where(Branch.code == info.code))
            if branch is None:
                # جستجو بر اساس نام مشابه
                branch = await session.scalar(
                    select(Branch).where(Branch.name.like(f"%{info.name.split()[1]}%"))
                )

            if branch is None:
                branch = Branch(
                    code=info.code,
                    name=info.name,
                    phone=f"{info.phone} | {info.mobile}",
                    address=info.address,
                    address_full=f"{info.address} (مترو: {info.metro_station}) - ساعات کاری: {info.working_hours}",
                    latitude=info.latitude,
                    longitude=info.longitude,
                    is_active=True,
                )
                session.add(branch)
                await session.flush()
            else:
                branch.name = info.name
                branch.code = info.code
                branch.phone = f"{info.phone} | {info.mobile}"
                branch.address = info.address
                branch.address_full = f"{info.address} (مترو: {info.metro_station}) - ساعات کاری: {info.working_hours}"
                branch.latitude = info.latitude
                branch.longitude = info.longitude
                branch.is_active = True

            branch_map[key] = branch

        # غیرفعال‌سازی شعب قدیمی، نامعتبر یا تکراری
        canonical_codes = {info.code for info in RHINOTECH_BRANCHES.values()}
        all_branches = (await session.scalars(select(Branch))).all()
        for b in all_branches:
            if b.code not in canonical_codes:
                b.is_active = False

        await session.commit()
        return branch_map

    @classmethod
    async def get_branch_inventory_summary(cls, session: AsyncSession, branch_id: int) -> dict[str, Any]:
        """
        دریافت آمار و لیست کالاهای موجود در یک شعبه خاص.
        """
        branch = await session.get(Branch, branch_id)
        if not branch:
            return {"branch": None, "total_items": 0, "brands": {}, "items": []}

        # جستجو بر اساس BranchInventory یا BranchStock
        query = (
            select(Laptop, BranchInventory.quantity, LaptopBrand.name.label("brand_name"))
            .join(BranchInventory, BranchInventory.laptop_id == Laptop.id)
            .join(LaptopBrand, LaptopBrand.id == Laptop.brand_id)
            .where(BranchInventory.branch_id == branch_id, BranchInventory.quantity > 0)
            .order_by(LaptopBrand.name, Laptop.model)
        )
        results = (await session.execute(query)).all()

        total_quantity = sum(row[1] for row in results)
        brands_count: dict[str, int] = {}
        items_list: list[dict[str, Any]] = []

        for laptop, qty, b_name in results:
            brands_count[b_name] = brands_count.get(b_name, 0) + qty
            items_list.append({
                "id": laptop.id,
                "brand": b_name,
                "model": laptop.model,
                "cpu": laptop.cpu,
                "ram": laptop.ram,
                "storage": laptop.storage,
                "gpu": laptop.gpu,
                "price": laptop.price,
                "quantity": qty,
                "image_url": laptop.image_url,
            })

        return {
            "branch": branch,
            "total_items": total_quantity,
            "brands": brands_count,
            "items": items_list,
        }

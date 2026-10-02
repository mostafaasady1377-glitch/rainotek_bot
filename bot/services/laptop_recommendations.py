"""Rank in-stock laptops by declared hardware for a selected use case."""

from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Branch, BranchInventory, Laptop, LaptopBrand


def capacity_gb(value: str | None) -> int:
    text = (value or "").lower().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*(tb|ترابایت|gb|گیگ)?", text)
    if not match:
        return 0
    number = float(match.group(1).replace(",", "."))
    return int(number * (1024 if match.group(2) in {"tb", "ترابایت"} else 1))


def cpu_strength(cpu: str | None) -> tuple[int, int]:
    text = (cpu or "").lower()
    if "celeron" in text or "pentium" in text or "atom" in text:
        return 1, 0
    apple = re.search(r"\bm([1-9])\b", text)
    if apple:
        return 4, int(apple.group(1)) + 9
    family = re.search(r"(?:core\s*)?i([3579])|ryzen\s*([3579])", text)
    tier = {3: 2, 5: 3, 7: 4, 9: 5}.get(int(family.group(1) or family.group(2)), 0) if family else 0
    intel_model = re.search(r"\bi[3579]\s*[- ]?\s*(\d{4,5})", text)
    ryzen_model = re.search(r"ryzen\s*[3579]\s*[- ]?\s*(\d{4})", text)
    generation = (int(intel_model.group(1)[:2]) if len(intel_model.group(1)) == 5 else int(intel_model.group(1)[0])) if intel_model else 0
    if ryzen_model:
        generation = int(ryzen_model.group(1)[0]) + 6
    return tier, generation


def dedicated_gpu(gpu: str | None) -> bool:
    text = (gpu or "").lower()
    return any(token in text for token in ("rtx", "gtx", "geforce", "quadro", "firepro")) or bool(re.search(r"radeon\s+rx\b", text))


def gpu_strength(gpu: str | None) -> int:
    text = (gpu or "").lower()
    if match := re.search(r"rtx\s*([2345])\d{3}", text):
        return {2: 3, 3: 4, 4: 5, 5: 6}[int(match.group(1))]
    if re.search(r"quadro\s*[km]\d", text):
        return 1
    if "rtx" in text or "quadro" in text or "firepro" in text:
        return 3
    if match := re.search(r"gtx\s*(\d{3,4})", text):
        return 3 if int(match.group(1)) >= 1600 else 2 if int(match.group(1)) >= 1000 else 1
    if "gtx" in text or re.search(r"radeon\s+rx\b", text):
        return 2
    if "geforce" in text:
        return 1
    return 0


def score_laptop(purpose: str, laptop: Laptop) -> tuple[float, list[str]] | None:
    ram = capacity_gb(laptop.ram)
    storage = capacity_gb(laptop.storage)
    cpu_tier, generation = cpu_strength(laptop.cpu)
    gpu = dedicated_gpu(laptop.gpu)
    gpu_power = gpu_strength(laptop.gpu)
    minimum_ram = 4 if purpose == "study" else 8
    minimum_cpu = 1 if purpose == "study" else 2
    if ram < minimum_ram or storage < 128 or cpu_tier < minimum_cpu:
        return None
    if purpose in {"gaming", "graphics", "rendering"} and gpu_power < 2:
        return None
    if purpose == "content" and gpu_power < 1:
        return None
    if purpose in {"study", "office"} and gpu:
        return None
    if purpose == "rendering" and ram < 16:
        return None
    if purpose in {"programming", "engineering"} and (cpu_tier < 3 or storage < 256):
        return None

    # Suitability is based on recorded hardware only. Price decides display
    # order, never whether a cheap but adequate study laptop is hidden.
    score = 45 + cpu_tier * 4 + min(ram, 32) / 4 + min(storage, 1024) / 512
    score += min(generation, 14) * 1.5
    score += 4 if "ssd" in (laptop.storage or "").lower() else 0
    if purpose in {"gaming", "graphics", "rendering", "engineering", "content"}:
        score += gpu_power * 4
    if purpose in {"study", "office", "trading"} and not gpu:
        score += 2
    score = min(99, round(score))

    reasons = [f"CPU: {laptop.cpu or 'نامشخص'}", f"رم: {laptop.ram}", f"حافظه: {laptop.storage}"]
    if purpose in {"gaming", "graphics", "rendering", "engineering", "content"}:
        reasons.append(f"گرافیک: {laptop.gpu or 'ثبت نشده'}")
    if purpose == "content" and gpu_power == 1:
        reasons.append("مناسب تدوین سبک؛ برای پروژهٔ سنگین‌تر مدل قوی‌تر انتخاب کنید")
    return score, reasons


async def recommend_laptops(session: AsyncSession, purpose: str, limit: int = 20) -> list[dict]:
    available = func.sum(BranchInventory.quantity - BranchInventory.reserved_count)
    stmt = (
        select(Laptop, LaptopBrand.name, available)
        .join(LaptopBrand, Laptop.brand_id == LaptopBrand.id)
        .join(BranchInventory, BranchInventory.laptop_id == Laptop.id)
        .join(Branch, Branch.id == BranchInventory.branch_id)
        .where(Laptop.status == "active", Laptop.price >= 5_000_000, Branch.is_active.is_(True),
               BranchInventory.quantity > BranchInventory.reserved_count)
        .group_by(Laptop.id, LaptopBrand.name)
    )
    ranked = []
    for laptop, brand, quantity in (await session.execute(stmt)).all():
        label = f"{brand} {laptop.model}".lower()
        if any(word in label for word in ("iphone", "monitor", "all in one", "mini pc", "case &")):
            continue
        evaluated = score_laptop(purpose, laptop)
        if evaluated is None:
            continue
        score, reasons = evaluated
        ranked.append({"id": laptop.id, "brand": brand, "model": laptop.model,
                       "price": laptop.price, "quantity": quantity, "score": score,
                       "reasons": reasons})
    ranked.sort(key=lambda item: (item["price"], item["id"]))
    if len(ranked) <= limit:
        selected = ranked
    else:
        # Show affordable, middle and higher-price options, selecting the best
        # hardware fit within each price band before sorting by price.
        bands = [ranked[:len(ranked) // 3],
                 ranked[len(ranked) // 3:2 * len(ranked) // 3],
                 ranked[2 * len(ranked) // 3:]]
        quotas = [limit // 3 + (1 if index < limit % 3 else 0) for index in range(3)]
        selected = []
        for index, (band, quota) in enumerate(zip(bands, quotas)):
            order = (lambda item: (item["price"], -item["score"])) if index == 0 else (lambda item: (-item["score"], item["price"]))
            selected.extend(sorted(band, key=order)[:quota])
        selected.sort(key=lambda item: (item["price"], item["id"]))
    return selected[:limit]

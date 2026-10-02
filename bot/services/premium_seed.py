"""One-time snapshot observed in the supplier bot on 2026-10-02.

Run manually after verifying supplier prices and stock. This is not a live sync.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from sqlalchemy import select

from database.models import PremiumPlan
from database.session import AsyncSessionLocal, create_db

SOURCE_SNAPSHOT = (
    ("hami-chatgpt-plus-ready-1m", "chat", "ChatGPT Plus · یک‌ماهه اختصاصی",
     3_399_000, 1, "اکانت اختصاصی آماده تحویل؛ ایمیل، رمز و 2FA. اعتبار Codex طبق پلن منبع."),
    ("hami-gemini-pro-18m", "chat", "Gemini Pro · هجده‌ماهه",
     799_000, 125, "لینک فعال‌سازی روی حساب شخصی؛ طبق توضیح منبع، فعال‌سازی پس از تحویل محدودیت زمانی دارد."),
    ("hami-multi-api-20usd", "api", "اعتبار API چندمدلی · ۲۰ دلار",
     999_000, 7, "دسترسی چندمدلی برای n8n و IDE؛ نوع سرویس و شرایط تحویل پیش از فعال‌سازی بررسی شود."),
    ("hami-multi-api-10usd", "api", "اعتبار API چندمدلی · ۱۰ دلار",
     499_000, 9, "دسترسی چندمدلی برای n8n، VS Code و ابزارهای سازگار؛ نوع سرویس پیش از تحویل بررسی شود."),
    ("hami-chatgpt-plus-personal-1m", "chat", "ChatGPT Plus · یک‌ماهه روی حساب شخصی",
     4_799_000, 1, "فعال‌سازی روی حساب شخصی با هماهنگی پشتیبانی؛ اطلاعات ورود را فقط در مسیر خصوصی امن دریافت کنید."),
    ("hami-claude-pro-personal-1m", "chat", "Claude Pro · یک‌ماهه روی حساب شخصی",
     4_899_000, 1, "فعال‌سازی روی حساب شخصی با هماهنگی پشتیبانی؛ اطلاعات ورود را فقط در مسیر خصوصی امن دریافت کنید."),
    ("hami-deepseek-pending", "chat", "DeepSeek · در انتظار قیمت و موجودی",
     0, 0, "در فهرست فعلی بات مرجع، پلن قابل خرید تأییدشده‌ای نمایش داده نشد."),
    ("hami-leonardo-pending", "creative", "Leonardo AI · در انتظار موجودی",
     0, 0, "در وب‌سایت مرجع فروخته‌شده نشان داده شده و در فهرست فعلی بات مرجع موجود نبود."),
    ("hami-capcut-pending", "editing", "CapCut Pro · در انتظار قیمت و موجودی",
     0, 0, "در فهرست فعلی بات مرجع، پلن قابل خرید تأییدشده‌ای نمایش داده نشد."),
    ("hami-video-tools-pending", "creative", "Seedance، Veo و Omni · در انتظار قیمت و موجودی",
     0, 0, "برای این ابزارها در فهرست فعلی بات مرجع قیمت و موجودی تأییدشده‌ای ثبت نشد."),
)


async def seed() -> int:
    await create_db()
    async with AsyncSessionLocal() as session:
        now = datetime.utcnow()
        for sku, category, title, price, stock, description in SOURCE_SNAPSHOT:
            plan = await session.scalar(select(PremiumPlan).where(PremiumPlan.source_sku == sku))
            if plan is None:
                plan = PremiumPlan(source_sku=sku, category=category, title=title,
                                   description=description, source_price_toman=price,
                                   stock=stock, checked_at=now)
                session.add(plan)
            # Re-running this one-time seed never overwrites a plan managed by the owner.
        await session.commit()
    return len(SOURCE_SNAPSHOT)


if __name__ == "__main__":
    print(asyncio.run(seed()))

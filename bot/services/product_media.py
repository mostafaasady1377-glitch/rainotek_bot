"""Telegram photo storage is independent of spreadsheet write credentials."""
from datetime import datetime
from sqlalchemy import select
from database.models import Laptop, LaptopStaffOverride, ProductImage, StaffActivity
from bot.services.stock_lock import stock_lock


async def save_product_photo(session, laptop_id, file_id, actor_id):
    if not isinstance(file_id, str) or not file_id or len(file_id) > 255:
        raise ValueError('شناسهٔ عکس نامعتبر است.')
    async with stock_lock:
        try:
            laptop = await session.get(Laptop, laptop_id)
            if not laptop or laptop.status != 'active':
                raise ValueError('محصول پیدا نشد.')
            override = await session.get(LaptopStaffOverride, laptop_id)
            if not override:
                override = LaptopStaffOverride(laptop_id=laptop_id, edited_by=actor_id)
                session.add(override)
            override.image_file_id = file_id
            override.edited_by = actor_id
            override.edited_at = datetime.utcnow()
            laptop.image_url = 'tgfile:' + file_id
            images = list((await session.scalars(select(ProductImage).where(ProductImage.laptop_id == laptop_id))).all())
            selected = None
            for image in images:
                image.is_primary = image.telegram_file_id == file_id
                if image.is_primary:
                    selected = image
            if not selected:
                session.add(ProductImage(laptop_id=laptop_id, telegram_file_id=file_id, image_url='tgfile:' + file_id, is_primary=True, display_order=0))
            session.add(StaffActivity(actor_telegram_id=actor_id, action='product_edit', target_id=laptop_id, detail='photo'))
            await session.commit()
        except BaseException:
            await session.rollback()
            raise

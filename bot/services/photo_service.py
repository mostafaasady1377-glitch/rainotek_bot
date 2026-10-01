from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

import aiohttp
from aiogram import Bot
from aiogram.types import InputMediaPhoto, URLInputFile
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Laptop, ProductImage

URL_PATTERN = re.compile(r"^https?://[a-zA-Z0-9_\-\.]+(:\d+)?(/.*)?$", re.IGNORECASE)


class ProductPhotoService:
    """
    مدیریت تصاویر محصولات: اعتبارسنجی لینک، پشتیبانی از چند تصویر،
    کش کردن شناسه فایل تلگرام (file_id) برای جلوگیری از دانلود مجدد،
    و مدیریت خطای لینک‌های ناموجود بدون توقف نمایش کاتالوگ.
    """

    @staticmethod
    def is_valid_url(url: str | None) -> bool:
        if not url or not isinstance(url, str):
            return False
        clean_url = url.strip()
        if not clean_url.startswith(("http://", "https://")):
            return False
        parsed = urlparse(clean_url)
        return bool(parsed.netloc and URL_PATTERN.match(clean_url))

    @staticmethod
    async def verify_image_reachable(url: str, timeout_seconds: float = 3.0) -> bool:
        """بررسی سریع و سبک در دسترس بودن تصویر با درخواست HEAD"""
        if not ProductPhotoService.is_valid_url(url):
            return False
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout_seconds)) as session:
                async with session.head(url, allow_redirects=True) as resp:
                    if resp.status < 400:
                        content_type = resp.headers.get("Content-Type", "")
                        return "image" in content_type or resp.status == 200
                    return False
        except Exception as exc:
            logger.debug(f"بررسی لینک عکس با خطا مواجه شد {url}: {exc}")
            return False

    @staticmethod
    async def add_or_update_image(
        session: AsyncSession,
        laptop_id: int,
        image_url: str,
        display_order: int = 0,
        is_primary: bool = False,
        caption: str | None = None,
    ) -> Optional[ProductImage]:
        if not ProductPhotoService.is_valid_url(image_url):
            return None

        clean_url = image_url.strip()
        existing = await session.scalar(
            select(ProductImage).where(
                ProductImage.laptop_id == laptop_id,
                ProductImage.image_url == clean_url,
            )
        )
        if existing is None:
            existing = ProductImage(
                laptop_id=laptop_id,
                image_url=clean_url,
                display_order=display_order,
                is_primary=is_primary,
                caption=caption,
            )
            session.add(existing)
        else:
            existing.display_order = display_order
            existing.is_primary = is_primary
            if caption:
                existing.caption = caption

        await session.commit()
        return existing

    @staticmethod
    async def get_laptop_images(session: AsyncSession, laptop_id: int) -> list[ProductImage]:
        result = await session.scalars(
            select(ProductImage)
            .where(ProductImage.laptop_id == laptop_id)
            .order_by(ProductImage.is_primary.desc(), ProductImage.display_order.asc(), ProductImage.id.asc())
        )
        images = list(result.all())
        # اگر در جدول ProductImage تصویری نبود ولی در ستون image_url لپ‌تاپ لینکی بود:
        if not images:
            laptop = await session.get(Laptop, laptop_id)
            if laptop and laptop.image_url and ProductPhotoService.is_valid_url(laptop.image_url):
                image = await ProductPhotoService.add_or_update_image(
                    session, laptop_id, laptop.image_url, is_primary=True
                )
                if image:
                    images.append(image)
        return images

    @staticmethod
    async def cache_file_id(session: AsyncSession, image_id: int, file_id: str) -> None:
        image = await session.get(ProductImage, image_id)
        if image and file_id:
            image.telegram_file_id = file_id
            await session.commit()

    @staticmethod
    async def send_product_photos(
        bot: Bot,
        chat_id: int,
        images: list[ProductImage],
        caption: str,
        session: AsyncSession,
    ) -> bool:
        """
        ارسال عکس یا آلبوم عکس با استفاده از telegram_file_id کش شده در اولویت،
        یا لینک مستقیم عکس، و کش کردن خودکار file_id حاصل برای درخواست‌های آینده.
        در صورت خطای دسترسی به عکس، خطا هندل شده و به برنامه صدمه نمی‌زند.
        """
        if not images:
            return False

        try:
            if len(images) == 1:
                img = images[0]
                photo_source = img.telegram_file_id if img.telegram_file_id else URLInputFile(img.image_url)
                sent_msg = await bot.send_photo(
                    chat_id=chat_id,
                    photo=photo_source,
                    caption=caption[:1024],
                )
                if sent_msg.photo and not img.telegram_file_id:
                    # بزرگترین سایز ارسال شده
                    new_file_id = sent_msg.photo[-1].file_id
                    await ProductPhotoService.cache_file_id(session, img.id, new_file_id)
                return True
            else:
                media_group: list[InputMediaPhoto] = []
                for idx, img in enumerate(images[:5]):  # حداکثر ۵ تصویر برای سرعت و کیفیت
                    source = img.telegram_file_id if img.telegram_file_id else URLInputFile(img.image_url)
                    media_caption = caption[:1024] if idx == 0 else None
                    media_group.append(InputMediaPhoto(media=source, caption=media_caption))

                sent_msgs = await bot.send_media_group(chat_id=chat_id, media=media_group)
                for idx, msg in enumerate(sent_msgs):
                    if idx < len(images) and msg.photo and not images[idx].telegram_file_id:
                        await ProductPhotoService.cache_file_id(session, images[idx].id, msg.photo[-1].file_id)
                return True

        except Exception as exc:
            logger.warning(f"ارسال تصویر به کاربر {chat_id} با خطا مواجه شد: {exc}")
            return False

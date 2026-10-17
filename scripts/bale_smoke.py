"""Explicit live smoke to the locally verified Bale owner only.

Run: python -m scripts.bale_smoke
Does not poll updates, create orders, change products or print credentials.
"""
import asyncio
import argparse
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import select
from aiogram import Bot
from aiogram.types import Message
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.config import get_settings
from bot.services.bale_transport import PlatformSession
from bot.services.platform_identity import bale_identity
from bot.handlers.role_panels import show_account_menu
from bot.handlers.catalog_browser import show_laptop_card
from bot.handlers.online_support import installment_model
from database.session import AsyncSessionLocal
from database.models import User, Laptop, ProductImage


async def main():
    cfg = get_settings()
    if len(cfg.BALE_ADMIN_IDS) != 1:
        raise RuntimeError('Smoke requires exactly one verified Bale owner')
    admin_id = bale_identity(cfg.BALE_ADMIN_IDS[0])
    cfg.ADMIN_TELEGRAM_IDS = list(dict.fromkeys(cfg.ADMIN_TELEGRAM_IDS + [admin_id]))
    tg_session = PlatformSession('telegram', cfg.BOT_TOKEN, cfg.BALE_BOT_TOKEN, proxy=cfg.NETWORK_PROXY_URL or None)
    bale_session = PlatformSession('bale', cfg.BALE_BOT_TOKEN, cfg.BOT_TOKEN)
    telegram = Bot(cfg.BOT_TOKEN, session=tg_session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    bale = Bot(cfg.BALE_BOT_TOKEN, session=bale_session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    tg_session.peer_bot = bale
    bale_session.peer_bot = telegram
    try:
        async with AsyncSessionLocal() as session:
            user = await session.scalar(select(User).where(User.telegram_id == admin_id, User.role == 'admin', User.is_active.is_(True)))
            if not user:
                raise RuntimeError('Verified admin missing')
            product_id = await session.scalar(select(Laptop.id).join(ProductImage).where(Laptop.status == 'active', Laptop.price > 0, ProductImage.image_url.like('tgfile:%')).limit(1))
            if product_id is None:
                product_id = await session.scalar(select(Laptop.id).where(Laptop.status == 'active', Laptop.price > 0).order_by(Laptop.id).limit(1))
        message = Message.model_validate({'message_id': 1, 'date': datetime.now(), 'chat': {'id': admin_id, 'type': 'private'}, 'from': {'id': admin_id, 'is_bot': False, 'first_name': user.first_name or user.full_name or 'مدیر', 'username': user.username}}, context={'bot': bale})
        await show_account_menu(message, user)
        print('live_bale_admin_menu_sent=True')
        if product_id:
            await show_laptop_card(message, product_id)
            print('live_bale_product_card_sent=True')
            await installment_model(SimpleNamespace(data=f'installment:model:{product_id}', answer=AsyncMock(), message=message))
            print('live_bale_installment_quote_sent=True')
    except Exception as error:
        print('live_test_failed=' + type(error).__name__)
        raise SystemExit(1) from None
    finally:
        await telegram.session.close()
        await bale.session.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--direct-network', action='store_true')
    if parser.parse_args().direct_network:
        get_settings().NETWORK_PROXY_URL = ''
    asyncio.run(main())

"""Durable sequential codes R1, R2...; never reuse codes for other experts."""
import re
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from database.models import ReferralLink, User
from database.session import AsyncSessionLocal
from bot.services.stock_lock import stock_lock


def referral_code_id(payload):
    match = re.fullmatch(r'R([1-9][0-9]{0,18})', payload)
    return int(match.group(1)) if match else 0


async def get_referral_code(user):
    async with stock_lock:
        async with AsyncSessionLocal() as session:
            expert = await session.get(User, user.id)
            if not expert or not expert.is_active or expert.role not in {'seller', 'branch_manager'}:
                raise ValueError('این حساب کارشناس یا مدیر فروش فعال نیست.')
            link = await session.scalar(select(ReferralLink).where(ReferralLink.expert_user_id == expert.id))
            if link is None:
                link = ReferralLink(expert_user_id=expert.id)
                session.add(link)
                try:
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                    link = await session.scalar(select(ReferralLink).where(ReferralLink.expert_user_id == expert.id))
                    if link is None:
                        raise
            return f'R{link.id}'

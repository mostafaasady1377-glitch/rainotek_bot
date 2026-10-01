"""Persisted support queue for the future staff panels; no notifications yet."""
from sqlalchemy import select, or_
from sqlalchemy.exc import IntegrityError

from database.models import SupportRequest


async def open_support_request(session, telegram_id):
    key = str(telegram_id)
    existing = await session.scalar(select(SupportRequest).where(SupportRequest.active_key == key))
    if existing:
        return existing
    request = SupportRequest(customer_telegram_id=telegram_id, active_key=key)
    session.add(request)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await session.scalar(select(SupportRequest).where(SupportRequest.active_key == key))
        if existing is None:
            raise
        return existing
    return request


async def staff_support_queue(session, actor):
    """Managers see their branch/unrouted queue; agents see assigned requests."""
    if not actor or not actor.is_active:
        raise PermissionError("Inactive or missing staff identity")
    query = select(SupportRequest).where(SupportRequest.status.in_(("waiting", "assigned")))
    if actor.role in ("admin", "SUPER_ADMIN"):
        pass
    elif actor.role in ("branch_manager", "BRANCH_MANAGER") and actor.managed_branch_id:
        query = query.where(or_(SupportRequest.branch_id == actor.managed_branch_id, SupportRequest.branch_id.is_(None)))
    elif actor.role in ("seller", "SALES_AGENT"):
        query = query.where(SupportRequest.assigned_user_id == actor.id)
    else:
        raise PermissionError("Staff access required")
    return list((await session.scalars(query.order_by(SupportRequest.created_at, SupportRequest.id))).all())

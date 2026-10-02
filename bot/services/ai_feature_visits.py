"""Record customer interest in AI services without storing chat contents."""

from datetime import datetime

from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from database.models import AiFeatureVisit
from database.session import AsyncSessionLocal


async def record_ai_visit(telegram_id: int, feature: str) -> None:
    if feature not in {"ai_menu", "smart_search", "vpn_menu", "ai_api_catalog", "ai_development"}:
        raise ValueError("Unknown AI feature")
    now = datetime.utcnow()
    async with AsyncSessionLocal() as session:
        stmt = sqlite_insert(AiFeatureVisit).values(
            telegram_id=telegram_id, feature=feature,
            first_seen_at=now, last_seen_at=now, interaction_count=1,
        )
        await session.execute(stmt.on_conflict_do_update(
            index_elements=[AiFeatureVisit.telegram_id, AiFeatureVisit.feature],
            set_={
                "last_seen_at": now,
                "interaction_count": AiFeatureVisit.interaction_count + 1,
            },
        ))
        await session.commit()

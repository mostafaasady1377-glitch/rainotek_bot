from database.models import StaffActivity


def can_manage_editor(actor, target):
    if not actor or not target or actor.is_active is False or target.role not in {'seller', 'branch_manager'}:
        return False
    if actor.role == 'admin':
        return True
    return bool(actor.role == 'branch_manager' and actor.managed_branch_id and
                actor.managed_branch_id == target.managed_branch_id and target.role == 'seller')


async def set_edit_permission(session, actor, target, allowed):
    if not can_manage_editor(actor, target):
        raise PermissionError('تغییر دسترسی این پرسنل مجاز نیست.')
    target.product_edit_allowed = allowed
    session.add(StaffActivity(actor_telegram_id=actor.telegram_id, action='edit_permission', target_id=target.id, detail='allow' if allowed else 'deny'))
    await session.commit()

"""Disjoint Bale identities in the shared, legacy telegram_id columns."""
BALE_ID_BASE = 10**16
BALE_FILE_PREFIX = 'balefile:'


def bale_identity(value: int) -> int:
    value = int(value)
    encoded = 2 * value if value >= 0 else -2 * value + 1
    return -(BALE_ID_BASE + encoded)


def is_bale_identity(value) -> bool:
    return isinstance(value, int) and value <= -BALE_ID_BASE


def native_identity(value: int) -> int:
    if not is_bale_identity(value):
        return value
    encoded = -value - BALE_ID_BASE
    return encoded // 2 if encoded % 2 == 0 else -(encoded // 2)

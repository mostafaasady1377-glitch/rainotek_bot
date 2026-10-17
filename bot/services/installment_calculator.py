"""Store-defined flat-interest quote, not an amortizing loan calculation."""
from decimal import Decimal, ROUND_HALF_UP


def installment_quote(price: int):
    if isinstance(price, bool) or not isinstance(price, int) or price <= 0:
        raise ValueError('A positive integer price in tomans is required')
    principal = Decimal(price)
    monthly_interest = principal * Decimal('0.025')
    yearly_interest = monthly_interest * 12
    total = principal + yearly_interest
    return dict(price=principal, monthly_interest=monthly_interest,
                yearly_interest=yearly_interest, total=total, monthly_payment=total / 12)


def toman_amount(value):
    return f"{Decimal(value).quantize(Decimal('1'), rounding=ROUND_HALF_UP):,} تومان"

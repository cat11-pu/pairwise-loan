"""loan: 分期还款计划内核（等额本息与等额本金）。"""

from .core import (
    METHOD_ANNUITY,
    METHOD_PRINCIPAL,
    RATE_SCALE,
    Installment,
    LoanError,
    Plan,
    annuity_payment,
    build_schedule,
    interest_for,
    overdue_penalty,
    round_half_up,
)

__all__ = [
    "RATE_SCALE",
    "METHOD_ANNUITY",
    "METHOD_PRINCIPAL",
    "LoanError",
    "Installment",
    "Plan",
    "round_half_up",
    "interest_for",
    "annuity_payment",
    "build_schedule",
    "overdue_penalty",
]

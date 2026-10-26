from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from enum import StrEnum


class TypeOfTransaction(StrEnum):
    INCOME = "income"
    EXPENSE = "expense"


@dataclass(frozen=True)
class TransactionDraft:
    """A transaction extracted from a user message, not yet saved in fintracker"""

    type_of_transaction: TypeOfTransaction
    amount: Decimal
    description: str
    registration_date: date
    category: str | None

    def with_category(self, category: str) -> "TransactionDraft":
        return replace(self, category=category)


def format_brl(amount: Decimal) -> str:
    """Decimal("5500") -> "R$ 5.500,00", the format fintracker's API expects"""
    formatted = f"{amount:,.2f}"
    return f"R$ {formatted.replace(',', 'v').replace('.', ',').replace('v', '.')}"


def format_brazilian_date(reference: date) -> str:
    return reference.strftime("%d/%m/%Y")

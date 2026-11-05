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
    # Only meaningful for expenses: pending ones are saved as not_paid
    is_paid: bool = True
    due_date: date | None = None

    def with_category(self, category: str) -> "TransactionDraft":
        return replace(self, category=category)


class ExpenseStatus(StrEnum):
    NOT_PAID = "not_paid"
    PAYING = "paying"
    ALREADY_PAID = "already_paid"


PENDING_EXPENSE_STATUSES = (ExpenseStatus.NOT_PAID, ExpenseStatus.PAYING)


@dataclass(frozen=True)
class SavedTransaction:
    """A transaction as fintracker returns it"""

    transaction_id: int
    type_of_transaction: TypeOfTransaction
    description: str
    amount: Decimal
    status: str
    registration_date: date
    due_date: date | None
    category_id: int | None


def format_brl(amount: Decimal) -> str:
    """Decimal("5500") -> "R$ 5.500,00", the format fintracker's API expects"""
    formatted = f"{amount:,.2f}"
    return f"R$ {formatted.replace(',', 'v').replace('.', ',').replace('v', '.')}"


def parse_brl(text: str) -> Decimal:
    """ "R$ 5.500,00" -> Decimal("5500.00")"""
    digits = text.replace("R$", "").strip().replace(".", "").replace(",", ".")
    return Decimal(digits)


def format_brazilian_date(reference: date) -> str:
    return reference.strftime("%d/%m/%Y")


def parse_brazilian_date(text: str) -> date | None:
    """ "26/10/2026" -> date(2026, 10, 26); fintracker sends "" when there's no date"""
    if not text:
        return None
    day, month, year = (int(part) for part in text.split("/"))
    return date(year, month, day)

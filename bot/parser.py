import logging
from datetime import date
from decimal import Decimal
from typing import Literal

from anthropic import AsyncAnthropic
from pydantic import BaseModel, Field

from bot.domain import TransactionDraft, TypeOfTransaction

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You extract financial transactions from short Brazilian Portuguese messages that a person \
sends to their personal finance bot, such as "Gastei 20 reais com lanche" or \
"Recebi R$ 5.500 de salário do mês".

Rules:
- is_transaction: true only when the message reports money that was spent or received. \
Greetings, questions and anything else are false, with every other field null.
- type_of_transaction: "expense" for money spent or paid (gastei, paguei, comprei...), \
"income" for money received (recebi, ganhei, caiu o salário...).
- amount_in_cents: the amount as an integer number of cents. Brazilian notation uses "." for \
thousands and "," for decimals: "R$ 5.500" is 550000, "R$ 12,90" is 1290, "vinte reais" is 2000.
- description: a short title for what the money was for, in Portuguese, starting with a capital \
letter and without the amount, verbs or payment method. Examples: "Lanche", "Salário", \
"Conta de luz".
- category: the single name from the available categories that clearly fits, copied exactly as \
written in the list. Use null when none clearly fits; the person will be asked to choose.
- registration_date: the date the transaction happened, as YYYY-MM-DD. Resolve relative dates \
("ontem", "sexta passada") from today's date. Use null when the message doesn't mention a date.
- is_paid: only for expenses (null for income). false when the message says the expense still \
has to be paid: "tenho que pagar", "a pagar", "vence dia 10", "venceu e não paguei", "boleto \
pendente", "conta pra pagar". true when it was already paid or when the message doesn't say.
- due_date: only for expenses, the due date ("vence", "vencimento", "venceu") as YYYY-MM-DD. A day \
without a month ("dia 10") follows the verb tense: for "vence dia 10" it is the next day 10 from \
today (this month or the next), for "venceu dia 10" the last day 10 up to today. Use null when \
the message doesn't mention a due date.
"""


class TransactionExtraction(BaseModel):
    is_transaction: bool
    type_of_transaction: Literal["income", "expense"] | None
    amount_in_cents: int | None = Field(description="Amount in cents, e.g. 2000 for R$ 20,00")
    description: str | None
    category: str | None
    registration_date: str | None = Field(description="YYYY-MM-DD")
    is_paid: bool | None = None
    due_date: str | None = Field(default=None, description="YYYY-MM-DD")


class ParserRefusedError(Exception):
    pass


class TransactionParser:
    def __init__(self, client: AsyncAnthropic, model: str) -> None:
        self.client = client
        self.model = model

    async def parse(self, text: str, categories: list[str], today: date) -> TransactionDraft | None:
        """Returns None when the message doesn't describe a transaction"""
        categories_list = "\n".join(f"- {name}" for name in categories) or "(nenhuma)"
        response = await self.client.messages.parse(
            model=self.model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Today is {today.isoformat()}.\n\n"
                        f"Available categories:\n{categories_list}\n\n"
                        f"Message:\n{text}"
                    ),
                }
            ],
            output_format=TransactionExtraction,
            output_config={"effort": "low"},
            # On a safety refusal, let the API retry on its recommended fallback model
            extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
            extra_body={"fallbacks": "default"},
        )

        if response.stop_reason == "refusal":
            raise ParserRefusedError(f"Model refused to parse message: {text!r}")

        extraction = response.parsed_output
        if extraction is None:
            logger.warning("No structured output for message %r: %s", text, response.stop_reason)
            return None

        return to_draft(extraction=extraction, categories=categories, today=today)


def to_draft(
    extraction: TransactionExtraction, categories: list[str], today: date
) -> TransactionDraft | None:
    if (
        not extraction.is_transaction
        or extraction.type_of_transaction is None
        or not extraction.amount_in_cents
        or extraction.amount_in_cents <= 0
        or not extraction.description
    ):
        return None

    type_of_transaction = TypeOfTransaction(extraction.type_of_transaction)
    registration_date = _parse_date(extraction.registration_date) or today
    is_paid = True
    due_date = None
    if type_of_transaction == TypeOfTransaction.EXPENSE:
        is_paid = extraction.is_paid is not False
        due_date = _parse_date(extraction.due_date)
        # fintracker rejects a due date before the registration date. A pending bill that is
        # already overdue is registered on its due date; a paid one keeps the payment date.
        if due_date and due_date < registration_date:
            if is_paid:
                due_date = registration_date
            else:
                registration_date = due_date

    return TransactionDraft(
        type_of_transaction=type_of_transaction,
        amount=Decimal(extraction.amount_in_cents) / 100,
        description=extraction.description.strip(),
        registration_date=registration_date,
        category=_match_category(extraction.category, categories),
        is_paid=is_paid,
        due_date=due_date,
    )


def _match_category(category: str | None, categories: list[str]) -> str | None:
    """Only trust a category that really exists in fintracker (case-insensitive)"""
    if not category:
        return None
    by_lower_name = {name.lower(): name for name in categories}
    return by_lower_name.get(category.strip().lower())


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None

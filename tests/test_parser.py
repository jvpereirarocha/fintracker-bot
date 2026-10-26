from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from bot.domain import TypeOfTransaction
from bot.parser import (
    ParserRefusedError,
    TransactionExtraction,
    TransactionParser,
    to_draft,
)

TODAY = date(2026, 10, 26)
CATEGORIES = ["Alimentação", "Salário", "Moradia"]


def extraction(**overrides: Any) -> TransactionExtraction:
    fields: dict[str, Any] = {
        "is_transaction": True,
        "type_of_transaction": "expense",
        "amount_in_cents": 2000,
        "description": "Lanche",
        "category": "Alimentação",
        "registration_date": None,
    }
    return TransactionExtraction(**{**fields, **overrides})


def test_converts_cents_to_decimal_and_defaults_date_to_today():
    draft = to_draft(extraction(), CATEGORIES, TODAY)

    assert draft is not None
    assert draft.amount == Decimal("20")
    assert draft.type_of_transaction == TypeOfTransaction.EXPENSE
    assert draft.registration_date == TODAY
    assert draft.category == "Alimentação"


def test_keeps_date_from_message():
    draft = to_draft(extraction(registration_date="2026-10-25"), CATEGORIES, TODAY)

    assert draft is not None
    assert draft.registration_date == date(2026, 10, 25)


@pytest.mark.parametrize(
    "category, expected",
    [("salário", "Salário"), ("Lazer", None), (None, None)],
)
def test_category_must_exist_in_fintracker(category, expected):
    draft = to_draft(extraction(category=category), CATEGORIES, TODAY)

    assert draft is not None
    assert draft.category == expected


@pytest.mark.parametrize(
    "overrides",
    [
        {"is_transaction": False},
        {"type_of_transaction": None},
        {"amount_in_cents": None},
        {"amount_in_cents": 0},
        {"description": ""},
    ],
)
def test_incomplete_extractions_are_not_transactions(overrides):
    assert to_draft(extraction(**overrides), CATEGORIES, TODAY) is None


class FakeMessages:
    def __init__(self, response: SimpleNamespace) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def parse(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return self.response


def fake_client(response: SimpleNamespace) -> Any:
    return SimpleNamespace(messages=FakeMessages(response))


async def test_parse_sends_message_categories_and_today_to_the_model():
    client = fake_client(SimpleNamespace(stop_reason="end_turn", parsed_output=extraction()))
    parser = TransactionParser(client=client, model="claude-opus-5-5")

    draft = await parser.parse("Gastei 20 reais com lanche", CATEGORIES, TODAY)

    assert draft is not None
    call = client.messages.calls[0]
    assert call["output_format"] is TransactionExtraction
    prompt = call["messages"][0]["content"]
    assert "Gastei 20 reais com lanche" in prompt
    assert "2026-10-26" in prompt
    assert "- Moradia" in prompt


async def test_parse_raises_on_refusal():
    client = fake_client(SimpleNamespace(stop_reason="refusal", parsed_output=None))
    parser = TransactionParser(client=client, model="claude-opus-5-5")

    with pytest.raises(ParserRefusedError):
        await parser.parse("...", CATEGORIES, TODAY)

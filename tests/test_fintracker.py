import json
from datetime import date
from decimal import Decimal

import httpx
import pytest

from bot.domain import TransactionDraft, TypeOfTransaction, format_brl
from bot.fintracker import (
    DuplicateTransactionError,
    FintrackerClient,
    FintrackerError,
    build_transaction_payload,
)

EXPENSE = TransactionDraft(
    type_of_transaction=TypeOfTransaction.EXPENSE,
    amount=Decimal("20"),
    description="Lanche",
    registration_date=date(2026, 10, 26),
    category="Alimentação",
)


@pytest.mark.parametrize(
    "amount, expected",
    [
        (Decimal("20"), "R$ 20,00"),
        (Decimal("0.5"), "R$ 0,50"),
        (Decimal("5500"), "R$ 5.500,00"),
        (Decimal("1234567.89"), "R$ 1.234.567,89"),
    ],
)
def test_format_brl(amount, expected):
    assert format_brl(amount) == expected


def test_expense_payload_uses_registration_date_as_due_date_and_already_paid():
    assert build_transaction_payload(EXPENSE) == {
        "description": "Lanche",
        "amount": "R$ 20,00",
        "typeOfTransaction": "expense",
        "registrationDate": "26/10/2026",
        "dueDate": "26/10/2026",
        "category": "Alimentação",
        "status": "already_paid",
    }


def test_income_payload_has_no_due_date_and_is_received():
    income = TransactionDraft(
        type_of_transaction=TypeOfTransaction.INCOME,
        amount=Decimal("5500"),
        description="Salário",
        registration_date=date(2026, 10, 5),
        category="Salário",
    )

    payload = build_transaction_payload(income)

    assert payload["dueDate"] is None
    assert payload["status"] == "received"
    assert payload["amount"] == "R$ 5.500,00"


def test_payload_requires_category():
    with pytest.raises(ValueError):
        build_transaction_payload(TransactionDraft(**{**EXPENSE.__dict__, "category": None}))


def stored(id_, description, status, due_date, type_="expense", category_id=1, amount="R$ 150,90"):
    """A transaction in the shape fintracker's API returns it"""
    return {
        "id": id_,
        "description": description,
        "amount": amount,
        "typeOfTransaction": type_,
        "status": status,
        "registrationDate": "01/10/2026",
        "dueDate": due_date,
        "categoryId": category_id,
    }


class FakeFintracker:
    """Minimal in-memory stand-in for the fintracker API"""

    def __init__(self) -> None:
        self.valid_token = "token-1"
        self.logins = 0
        self.created: list[dict] = []
        self.conflict = False
        self.updates: list[tuple[int, dict]] = []
        self.latest_params: dict[str, str] = {}
        self.transactions = {
            1: stored(1, "Luz", "not_paid", "10/11/2026"),
            2: stored(2, "Internet", "paying", "05/11/2026", category_id=2),
            3: stored(3, "Lanche", "already_paid", "01/10/2026"),
            4: stored(4, "Salário", "received", "", type_="income", category_id=3),
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/users/login":
            self.logins += 1
            return httpx.Response(
                200, json={"accessToken": self.valid_token, "tokenType": "Bearer"}
            )

        if request.headers.get("Authorization") != f"Bearer {self.valid_token}":
            return httpx.Response(401, json={"detail": "Token is expired"})

        if request.url.path == "/api/v1/categories/":
            page = int(request.url.params["page"])
            pages = {1: ([(1, "Moradia"), (2, "Transporte")], 2), 2: ([(3, "Salário")], None)}
            categories, next_page = pages[page]
            results = [{"categoryId": id_, "name": name} for id_, name in categories]
            return httpx.Response(200, json={"results": results, "next": next_page})

        if request.url.path == "/api/v1/transactions/" and request.method == "GET":
            params = request.url.params
            if params.get("orderBy") == "id":
                self.latest_params = dict(params)
                newest = sorted(self.transactions.values(), key=lambda t: -t["id"])
                limit = int(params["itemsPerPage"])
                return httpx.Response(200, json={"transactions": newest[:limit], "next": 2})

            # One item per page, so pagination is exercised too
            matches = [
                t
                for t in self.transactions.values()
                if t["typeOfTransaction"] == params["typeOfTransaction"]
                and t["status"] == params["status"]
            ]
            page = int(params["page"])
            next_page = page + 1 if page < len(matches) else None
            return httpx.Response(
                200, json={"transactions": matches[page - 1 : page], "next": next_page}
            )

        if request.url.path.startswith("/api/v1/transactions/") and request.method != "POST":
            transaction_id = int(request.url.path.rsplit("/", 1)[1])
            if transaction_id not in self.transactions:
                return httpx.Response(404, json={"detail": "Transaction not found"})
            if request.method == "PUT":
                body = json.loads(request.content)
                self.updates.append((transaction_id, body))
                self.transactions[transaction_id]["status"] = body["status"]
            return httpx.Response(200, json=self.transactions[transaction_id])

        if request.url.path == "/api/v1/transactions/":
            if self.conflict:
                return httpx.Response(409, json={"detail": "Já existe"})
            body = json.loads(request.content)
            if body["category"] == "Inexistente":
                return httpx.Response(404, json={"detail": "Category Inexistente not found"})
            self.created.append(body)
            return httpx.Response(201, json={"id": 1, **body})

        return httpx.Response(404)


@pytest.fixture
def api() -> FakeFintracker:
    return FakeFintracker()


@pytest.fixture
def client(api: FakeFintracker) -> FintrackerClient:
    http_client = httpx.AsyncClient(
        base_url="http://fintracker/api/v1", transport=httpx.MockTransport(api.handler)
    )
    return FintrackerClient(http_client=http_client, username="me", password="secret")


async def test_create_transaction_logs_in_once_and_posts_payload(client, api):
    await client.create_transaction(EXPENSE)
    await client.create_transaction(EXPENSE)

    assert api.logins == 1
    assert api.created == [build_transaction_payload(EXPENSE)] * 2


async def test_expired_token_triggers_new_login_and_retry(client, api):
    await client.create_transaction(EXPENSE)
    api.valid_token = "token-2"

    await client.create_transaction(EXPENSE)

    assert api.logins == 2
    assert len(api.created) == 2


async def test_conflict_raises_duplicate_error(client, api):
    api.conflict = True

    with pytest.raises(DuplicateTransactionError):
        await client.create_transaction(EXPENSE)


async def test_other_errors_carry_api_detail(client):
    with pytest.raises(FintrackerError) as exc_info:
        await client.create_transaction(
            TransactionDraft(**{**EXPENSE.__dict__, "category": "Inexistente"})
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.message == "Category Inexistente not found"


async def test_list_category_names_follows_pagination(client):
    assert await client.list_category_names() == ["Moradia", "Transporte", "Salário"]


async def test_list_pending_expenses_merges_both_statuses_sorted_by_due_date(client):
    expenses = await client.list_pending_expenses()

    assert [(e.transaction_id, e.status) for e in expenses] == [(2, "paying"), (1, "not_paid")]
    assert expenses[1].amount == Decimal("150.90")
    assert expenses[1].due_date == date(2026, 11, 10)


async def test_mark_as_paid_sends_whole_transaction_with_category_name(client, api):
    paid = await client.mark_as_paid(1)

    assert paid.status == "already_paid"
    assert api.updates == [
        (
            1,
            {
                "description": "Luz",
                "amount": "R$ 150,90",
                "typeOfTransaction": "expense",
                "registrationDate": "01/10/2026",
                "dueDate": "10/11/2026",
                "category": "Moradia",
                "status": "already_paid",
            },
        )
    ]


async def test_mark_as_paid_skips_update_when_already_paid(client, api):
    paid = await client.mark_as_paid(3)

    assert paid.status == "already_paid"
    assert api.updates == []


async def test_mark_as_paid_refuses_income(client, api):
    with pytest.raises(FintrackerError):
        await client.mark_as_paid(4)
    assert api.updates == []


async def test_mark_as_paid_unknown_transaction_carries_api_detail(client):
    with pytest.raises(FintrackerError) as exc_info:
        await client.mark_as_paid(99)

    assert exc_info.value.status_code == 404


async def test_list_latest_transactions_asks_for_creation_order_in_one_page(client, api):
    latest = await client.list_latest_transactions(3)

    assert [t.transaction_id for t in latest] == [4, 3, 2]
    assert api.latest_params == {"orderBy": "id", "page": "1", "itemsPerPage": "3"}
    assert latest[0].type_of_transaction == TypeOfTransaction.INCOME

from http import HTTPStatus
from typing import Any

import httpx

from bot.domain import (
    PENDING_EXPENSE_STATUSES,
    ExpenseStatus,
    SavedTransaction,
    TransactionDraft,
    TypeOfTransaction,
    format_brazilian_date,
    format_brl,
    parse_brazilian_date,
    parse_brl,
)

# Defaults agreed for messages that don't say otherwise
EXPENSE_DEFAULT_STATUS = ExpenseStatus.ALREADY_PAID.value
INCOME_DEFAULT_STATUS = "received"
CATEGORIES_PAGE_SIZE = 20  # fintracker's max page size
TRANSACTIONS_PAGE_SIZE = 50


class FintrackerError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class DuplicateTransactionError(FintrackerError):
    pass


def build_transaction_payload(draft: TransactionDraft) -> dict[str, Any]:
    if draft.category is None:
        raise ValueError("A transaction can't be saved without a category")

    registration_date = format_brazilian_date(draft.registration_date)
    is_expense = draft.type_of_transaction == TypeOfTransaction.EXPENSE
    if is_expense:
        due_date: str | None = format_brazilian_date(draft.due_date or draft.registration_date)
        status = EXPENSE_DEFAULT_STATUS if draft.is_paid else ExpenseStatus.NOT_PAID.value
    else:
        due_date, status = None, INCOME_DEFAULT_STATUS

    return {
        "description": draft.description,
        "amount": format_brl(draft.amount),
        "typeOfTransaction": draft.type_of_transaction.value,
        "registrationDate": registration_date,
        "dueDate": due_date,
        "category": draft.category,
        "status": status,
    }


def build_update_payload(
    transaction: SavedTransaction, category: str, status: str
) -> dict[str, Any]:
    """fintracker's PUT replaces the whole transaction, so every field is sent back as is"""
    return {
        "description": transaction.description,
        "amount": format_brl(transaction.amount),
        "typeOfTransaction": transaction.type_of_transaction.value,
        "registrationDate": format_brazilian_date(transaction.registration_date),
        "dueDate": format_brazilian_date(transaction.due_date) if transaction.due_date else None,
        "category": category,
        "status": status,
    }


def parse_transaction(body: dict[str, Any]) -> SavedTransaction:
    registration_date = parse_brazilian_date(body["registrationDate"])
    if registration_date is None:
        raise FintrackerError(f"Transação {body['id']} sem data de registro")

    return SavedTransaction(
        transaction_id=body["id"],
        type_of_transaction=TypeOfTransaction(body["typeOfTransaction"]),
        description=body["description"],
        amount=parse_brl(body["amount"]),
        status=body["status"],
        registration_date=registration_date,
        due_date=parse_brazilian_date(body.get("dueDate") or ""),
        category_id=body.get("categoryId"),
    )


class FintrackerClient:
    """
    Talks to fintracker's REST API with a single fixed account.

    The JWT expires in 45 minutes and there's no refresh token, so we log in
    lazily and log in again whenever the API answers 401.
    """

    def __init__(self, http_client: httpx.AsyncClient, username: str, password: str) -> None:
        self.http_client = http_client
        self.username = username
        self.password = password
        self._token: str | None = None

    async def _login(self) -> str:
        response = await self.http_client.post(
            "/users/login",
            json={"username": self.username, "password": self.password},
        )
        if response.status_code != HTTPStatus.OK:
            raise FintrackerError(
                message=f"Falha ao autenticar no fintracker: {_error_detail(response)}",
                status_code=response.status_code,
            )

        token: str = response.json()["accessToken"]
        self._token = token
        return token

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        token = self._token or await self._login()
        response = await self.http_client.request(
            method, url, headers={"Authorization": f"Bearer {token}"}, **kwargs
        )
        if response.status_code == HTTPStatus.UNAUTHORIZED:
            token = await self._login()
            response = await self.http_client.request(
                method, url, headers={"Authorization": f"Bearer {token}"}, **kwargs
            )
        return response

    async def list_categories(self) -> dict[int, str]:
        """Category id -> name, following fintracker's pagination"""
        categories: dict[int, str] = {}
        page: int | None = 1
        while page is not None:
            response = await self._request(
                "GET",
                "/categories/",
                params={"page": page, "itemsPerPage": CATEGORIES_PAGE_SIZE},
            )
            if response.status_code != HTTPStatus.OK:
                raise FintrackerError(
                    message=f"Falha ao listar categorias: {_error_detail(response)}",
                    status_code=response.status_code,
                )
            body = response.json()
            categories.update(
                {category["categoryId"]: category["name"] for category in body["results"]}
            )
            page = body["next"]

        return categories

    async def list_category_names(self) -> list[str]:
        return list((await self.list_categories()).values())

    async def create_transaction(self, draft: TransactionDraft) -> dict[str, Any]:
        response = await self._request(
            "POST", "/transactions/", json=build_transaction_payload(draft)
        )
        if response.status_code == HTTPStatus.CONFLICT:
            raise DuplicateTransactionError(
                message=_error_detail(response), status_code=response.status_code
            )
        if response.status_code != HTTPStatus.CREATED:
            raise FintrackerError(message=_error_detail(response), status_code=response.status_code)

        created: dict[str, Any] = response.json()
        return created

    async def list_pending_expenses(self) -> list[SavedTransaction]:
        """
        Expenses not paid yet, oldest due date first.

        The API filters by a single status, so each pending status is a separate query.
        """
        expenses: list[SavedTransaction] = []
        for status in PENDING_EXPENSE_STATUSES:
            page: int | None = 1
            while page is not None:
                response = await self._request(
                    "GET",
                    "/transactions/",
                    params={
                        "typeOfTransaction": TypeOfTransaction.EXPENSE.value,
                        "status": status.value,
                        "page": page,
                        "itemsPerPage": TRANSACTIONS_PAGE_SIZE,
                    },
                )
                if response.status_code != HTTPStatus.OK:
                    raise FintrackerError(
                        message=f"Falha ao listar despesas: {_error_detail(response)}",
                        status_code=response.status_code,
                    )
                body = response.json()
                expenses.extend(parse_transaction(item) for item in body["transactions"])
                page = body["next"]

        return sorted(expenses, key=lambda e: (e.due_date or e.registration_date, e.transaction_id))

    async def list_latest_transactions(self, limit: int) -> list[SavedTransaction]:
        """The last transactions created, from the bot or the web app, newest first"""
        response = await self._request(
            "GET",
            "/transactions/",
            params={"orderBy": "id", "page": 1, "itemsPerPage": limit},
        )
        if response.status_code != HTTPStatus.OK:
            raise FintrackerError(
                message=f"Falha ao listar transações: {_error_detail(response)}",
                status_code=response.status_code,
            )
        return [parse_transaction(item) for item in response.json()["transactions"]]

    async def get_transaction(self, transaction_id: int) -> SavedTransaction:
        response = await self._request("GET", f"/transactions/{transaction_id}")
        if response.status_code != HTTPStatus.OK:
            raise FintrackerError(message=_error_detail(response), status_code=response.status_code)
        return parse_transaction(response.json())

    async def mark_as_paid(self, transaction_id: int) -> SavedTransaction:
        """
        Re-reads the transaction before updating it, so changes made elsewhere
        (e.g. in the web app) since the list was shown aren't overwritten.
        """
        transaction = await self.get_transaction(transaction_id)
        if transaction.type_of_transaction != TypeOfTransaction.EXPENSE:
            raise FintrackerError(f"A transação {transaction_id} não é uma despesa")
        if transaction.status == ExpenseStatus.ALREADY_PAID:
            return transaction

        categories = await self.list_categories()
        category = categories.get(transaction.category_id) if transaction.category_id else None
        if category is None:
            raise FintrackerError(f"Não encontrei a categoria da transação {transaction_id}")

        response = await self._request(
            "PUT",
            f"/transactions/{transaction_id}",
            json=build_update_payload(transaction, category, ExpenseStatus.ALREADY_PAID.value),
        )
        if response.status_code != HTTPStatus.OK:
            raise FintrackerError(message=_error_detail(response), status_code=response.status_code)
        return parse_transaction(response.json())


def _error_detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = None
    return str(detail) if detail else f"HTTP {response.status_code}"

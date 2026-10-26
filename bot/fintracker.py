from http import HTTPStatus
from typing import Any

import httpx

from bot.domain import (
    TransactionDraft,
    TypeOfTransaction,
    format_brazilian_date,
    format_brl,
)

# Defaults agreed for messages that don't say otherwise
EXPENSE_DEFAULT_STATUS = "already_paid"
INCOME_DEFAULT_STATUS = "received"
CATEGORIES_PAGE_SIZE = 20  # fintracker's max page size


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

    return {
        "description": draft.description,
        "amount": format_brl(draft.amount),
        "typeOfTransaction": draft.type_of_transaction.value,
        "registrationDate": registration_date,
        "dueDate": registration_date if is_expense else None,
        "category": draft.category,
        "status": EXPENSE_DEFAULT_STATUS if is_expense else INCOME_DEFAULT_STATUS,
    }


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

    async def list_category_names(self) -> list[str]:
        names: list[str] = []
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
            names.extend(category["name"] for category in body["results"])
            page = body["next"]

        return names

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


def _error_detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = None
    return str(detail) if detail else f"HTTP {response.status_code}"

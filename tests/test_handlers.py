from datetime import date
from decimal import Decimal

from bot.domain import SavedTransaction, TypeOfTransaction, parse_brazilian_date, parse_brl
from bot.handlers import MAX_PENDING_LISTED, PAID_CALLBACK_PREFIX, render_latest, render_pending

TODAY = date(2026, 11, 5)


def expense(
    id_: int, description: str, due_date: date | None, amount: str = "10"
) -> SavedTransaction:
    return SavedTransaction(
        transaction_id=id_,
        type_of_transaction=TypeOfTransaction.EXPENSE,
        description=description,
        amount=Decimal(amount),
        status="not_paid",
        registration_date=date(2026, 10, 1),
        due_date=due_date,
        category_id=1,
    )


def test_render_pending_without_expenses():
    text, keyboard = render_pending([], today=TODAY)

    assert "Nenhuma despesa pendente" in text
    assert keyboard is None


def test_render_pending_lists_total_due_labels_and_pay_buttons():
    expenses = [
        expense(7, "Internet", date(2026, 11, 1), "99.90"),
        expense(8, "Luz", TODAY, "150.10"),
        expense(9, "Aluguel", date(2026, 11, 10), "1500"),
        expense(10, "Presente", None, "50"),
    ]

    text, keyboard = render_pending(expenses, today=TODAY)

    assert "Despesas pendentes (4), total R$ 1.800,00" in text
    assert "⚠️ venceu em 01/11/2026" in text
    assert "⏰ vence hoje" in text
    assert "vence em 10/11/2026" in text
    assert "sem vencimento" in text
    assert keyboard is not None
    callbacks = [row[0].callback_data for row in keyboard.inline_keyboard]
    assert callbacks == [f"{PAID_CALLBACK_PREFIX}{id_}" for id_ in (7, 8, 9, 10)]


def test_render_pending_caps_the_list_but_totals_everything():
    expenses = [expense(i, f"Conta {i}", TODAY) for i in range(1, MAX_PENDING_LISTED + 6)]

    text, keyboard = render_pending(expenses, today=TODAY)

    assert keyboard is not None
    assert len(keyboard.inline_keyboard) == MAX_PENDING_LISTED
    assert "... e mais 5" in text
    assert f"total R$ {10 * len(expenses)},00" in text


def test_brazilian_parsers():
    assert parse_brl("R$ 1.234,56") == Decimal("1234.56")
    assert parse_brazilian_date("26/10/2026") == date(2026, 10, 26)
    assert parse_brazilian_date("") is None


def test_render_latest_without_transactions():
    assert render_latest([], categories={}) == "Nenhuma transação cadastrada ainda."


def test_render_latest_shows_sign_date_category_and_status():
    salary = SavedTransaction(
        transaction_id=12,
        type_of_transaction=TypeOfTransaction.INCOME,
        description="Salário",
        amount=Decimal("5500"),
        status="received",
        registration_date=date(2026, 11, 5),
        due_date=None,
        category_id=3,
    )
    snack = expense(11, "Lanche", TODAY, "20")
    orphan = SavedTransaction(**{**expense(10, "Sem categoria", None).__dict__, "category_id": 99})

    text = render_latest([salary, snack, orphan], categories={1: "Alimentação", 3: "Salário"})

    assert text.splitlines() == [
        "Últimas 3 transações:",
        "",
        "1. 🟢 +R$ 5.500,00 Salário",
        "    05/11/2026 · Salário · recebida",
        "2. 🔴 -R$ 20,00 Lanche",
        "    01/10/2026 · Alimentação · não paga",
        "3. 🔴 -R$ 10,00 Sem categoria",
        "    01/10/2026 · não paga",
    ]

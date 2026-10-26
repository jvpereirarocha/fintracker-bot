import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic
import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.domain import TransactionDraft, TypeOfTransaction, format_brazilian_date, format_brl
from bot.fintracker import DuplicateTransactionError, FintrackerClient, FintrackerError
from bot.parser import ParserRefusedError, TransactionParser

logger = logging.getLogger(__name__)

# Keys in context.user_data while the bot waits for a category choice
PENDING_DRAFT = "pending_draft"
PENDING_CATEGORIES = "pending_categories"

CATEGORY_CALLBACK_PREFIX = "category:"
CANCEL_CALLBACK = "cancel"

HELP_TEXT = (
    "Me mande uma mensagem descrevendo a transação e eu registro no fintracker. Exemplos:\n"
    "• Gastei 20 reais com lanche\n"
    "• Recebi R$ 5.500 de salário do mês\n"
    "• Paguei 150,90 de conta de luz ontem"
)


def _fintracker(context: ContextTypes.DEFAULT_TYPE) -> FintrackerClient:
    client: FintrackerClient = context.bot_data["fintracker"]
    return client


def _parser(context: ContextTypes.DEFAULT_TYPE) -> TransactionParser:
    parser: TransactionParser = context.bot_data["parser"]
    return parser


def _timezone(context: ContextTypes.DEFAULT_TYPE) -> ZoneInfo:
    timezone: ZoneInfo = context.bot_data["timezone"]
    return timezone


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(HELP_TEXT)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if message is None or message.text is None:
        return

    status = await message.reply_text("Processando...")
    try:
        categories = await _fintracker(context).list_category_names()
        today = datetime.now(_timezone(context)).date()
        draft = await _parser(context).parse(text=message.text, categories=categories, today=today)
    except (FintrackerError, httpx.HTTPError):
        logger.exception("Failed to load categories from fintracker")
        await status.edit_text("Não consegui falar com o fintracker. Tente novamente mais tarde.")
        return
    except anthropic.APIError:
        logger.exception("Failed to call the Anthropic API")
        await status.edit_text("Não consegui interpretar a mensagem agora. Tente novamente.")
        return
    except ParserRefusedError:
        logger.exception("Parser refused message")
        await status.edit_text("Não consegui interpretar essa mensagem.\n\n" + HELP_TEXT)
        return

    if draft is None:
        await status.edit_text("Não entendi como uma transação.\n\n" + HELP_TEXT)
        return

    if draft.category is None:
        if context.user_data is None or not categories:
            await status.edit_text(
                "Não há categorias cadastradas no fintracker. Cadastre uma e tente novamente."
            )
            return

        context.user_data[PENDING_DRAFT] = draft
        context.user_data[PENDING_CATEGORIES] = categories
        await status.edit_text(
            f"{_summary(draft)}\n\nEm qual categoria eu registro?",
            reply_markup=_categories_keyboard(categories),
        )
        return

    await status.edit_text(await _save(draft, context))


async def handle_category_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or context.user_data is None:
        return
    await query.answer()

    draft: TransactionDraft | None = context.user_data.pop(PENDING_DRAFT, None)
    categories: list[str] = context.user_data.pop(PENDING_CATEGORIES, [])

    if query.data == CANCEL_CALLBACK:
        await query.edit_message_text("Registro cancelado.")
        return

    index = int(query.data.removeprefix(CATEGORY_CALLBACK_PREFIX))
    if draft is None or index >= len(categories):
        await query.edit_message_text("Essa escolha expirou. Mande a mensagem novamente.")
        return

    await query.edit_message_text(await _save(draft.with_category(categories[index]), context))


async def _save(draft: TransactionDraft, context: ContextTypes.DEFAULT_TYPE) -> str:
    try:
        await _fintracker(context).create_transaction(draft)
    except DuplicateTransactionError:
        return (
            f"{_summary(draft)}\n\n"
            "Já existe uma despesa com essa descrição nessa data. "
            "Mande de novo com uma descrição diferente."
        )
    except FintrackerError as error:
        logger.error("fintracker rejected transaction %s: %s", draft, error.message)
        return f"{_summary(draft)}\n\nO fintracker recusou a transação: {error.message}"
    except httpx.HTTPError:
        logger.exception("Failed to reach fintracker")
        return "Não consegui falar com o fintracker. Tente novamente mais tarde."

    return f"Registrado!\n\n{_summary(draft)}"


def _summary(draft: TransactionDraft) -> str:
    kind = "Despesa" if draft.type_of_transaction == TypeOfTransaction.EXPENSE else "Receita"
    lines = [
        f"{kind}: {draft.description}",
        f"Valor: {format_brl(draft.amount)}",
        f"Data: {format_brazilian_date(draft.registration_date)}",
    ]
    if draft.category:
        lines.append(f"Categoria: {draft.category}")
    return "\n".join(lines)


def _categories_keyboard(categories: list[str]) -> InlineKeyboardMarkup:
    # callback_data is limited to 64 bytes, so buttons carry the index, not the name
    buttons = [
        InlineKeyboardButton(name, callback_data=f"{CATEGORY_CALLBACK_PREFIX}{index}")
        for index, name in enumerate(categories)
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("Cancelar", callback_data=CANCEL_CALLBACK)])
    return InlineKeyboardMarkup(rows)

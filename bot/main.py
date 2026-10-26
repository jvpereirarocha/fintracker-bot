import logging
from zoneinfo import ZoneInfo

import httpx
from anthropic import AsyncAnthropic
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from bot.fintracker import FintrackerClient
from bot.handlers import (
    CANCEL_CALLBACK,
    CATEGORY_CALLBACK_PREFIX,
    handle_category_choice,
    handle_text,
    start,
)
from bot.parser import TransactionParser
from bot.settings import Settings

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
# httpx logs every request URL at INFO, which includes the Telegram bot token
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def build_application(settings: Settings) -> Application:  # type: ignore[type-arg]
    http_client = httpx.AsyncClient(base_url=settings.FINTRACKER_API_URL, timeout=15.0)
    anthropic_client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    async def close_clients(_: Application) -> None:  # type: ignore[type-arg]
        await http_client.aclose()
        await anthropic_client.close()

    application = (
        Application.builder()
        .token(settings.TELEGRAM_BOT_TOKEN)
        .post_shutdown(close_clients)
        .build()
    )
    application.bot_data["fintracker"] = FintrackerClient(
        http_client=http_client,
        username=settings.FINTRACKER_USERNAME,
        password=settings.FINTRACKER_PASSWORD,
    )
    application.bot_data["parser"] = TransactionParser(
        client=anthropic_client, model=settings.ANTHROPIC_MODEL
    )
    application.bot_data["timezone"] = ZoneInfo(settings.TIMEZONE)

    # Only the owner of the fintracker account can use the bot; everyone else is ignored
    owner = filters.User(user_id=settings.TELEGRAM_ALLOWED_USER_ID)
    application.add_handler(CommandHandler(["start", "help"], start, filters=owner))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & owner, handle_text))
    application.add_handler(
        CallbackQueryHandler(
            handle_category_choice,
            pattern=rf"^({CATEGORY_CALLBACK_PREFIX}\d+|{CANCEL_CALLBACK})$",
        )
    )
    return application


def main() -> None:
    settings = Settings()
    application = build_application(settings)
    logger.info("Bot started. Polling...")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

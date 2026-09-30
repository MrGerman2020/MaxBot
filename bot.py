import asyncio
import logging
from maxapi import Bot, Dispatcher

from config import MAX_BOT_TOKEN
from database import init_db
from scheduler import start_scheduler
from handlers import start, appeals, votes, charges
from services.uk_delivery import check_email_config, is_email_configured
from services.uk_inbox import check_inbox_config, is_inbox_configured

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


async def main():
    # 1. База данных
    await init_db()
    logger.info("База данных готова")

    # 2. Бот и диспетчер
    bot = Bot(token=MAX_BOT_TOKEN)
    dp = Dispatcher()

    # 3. Роутеры
    dp.include_routers(
        start.router,
        appeals.router,
        votes.router,
        charges.router,
    )

    # 4. Проверка почты: ловим опечатки в хостах до первого обращения
    if is_email_configured():
        for problem in check_email_config():
            logger.warning("Отправка почты: %s", problem)
    else:
        logger.warning(
            "Почта не настроена — обращения будут подготовлены, "
            "но не отправлены. См. docs/НАСТРОЙКА_EMAIL.md"
        )

    if is_inbox_configured():
        for problem in check_inbox_config():
            logger.warning("Приём почты: %s", problem)
    else:
        logger.warning(
            "Приём ответов УК не настроен — письма не будут попадать в MAX. "
            "См. docs/НАСТРОЙКА_ОТВЕТЫ_УК.md"
        )

    # 5. Планировщик (проверка ящика)
    start_scheduler()
    logger.info("Бот запущен (polling)")

    # 6. Polling
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
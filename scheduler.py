"""Фоновые задачи бота.

Единственная задача — проверка почтового ящика: ответы УК приходят на
почту бота, а жителю их нужно показать в мессенджере. Напоминания о поверке
счётчиков и капремонте убраны вместе с подпиской.
"""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from maxapi import Bot

import config
from services.uk_inbox import check_and_notify, is_inbox_configured

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

# Переиспользуется между запусками опроса: создавать Bot на каждый тик
# не нужно, но закрывать его тоже нельзя — он переживёт задачу.
_bot: Bot | None = None


def _get_bot() -> Bot:
    global _bot
    if _bot is None:
        _bot = Bot(token=config.MAX_BOT_TOKEN)
    return _bot


async def check_mailbox() -> None:
    """Один опрос ящика: забрать новые письма и разослать ответы в MAX."""
    if not is_inbox_configured():
        return
    try:
        await check_and_notify(_get_bot())
    except Exception as exc:  # noqa: BLE001 — задача не должна падать
        logger.exception("Ошибка проверки почтового ящика: %s", exc)


def start_scheduler() -> None:
    """Запускает фоновые задачи. Вызывается из bot.py."""
    if not is_inbox_configured():
        from services.uk_inbox import check_inbox_config

        logger.warning(
            "Приём ответов УК выключен — письма не будут пересылаться в MAX. "
            "Причина: %s",
            "; ".join(check_inbox_config()),
        )
        return

    scheduler.add_job(
        check_mailbox,
        IntervalTrigger(seconds=config.IMAP_POLL_SECONDS),
        id="inbox_check",
        replace_existing=True,
        # Не накапливать пропущенные запуски: при возврате бота
        # достаточно одного опроса.
        max_instances=1,
        coalesce=True,
    )
    # Первый опрос сразу при старте, а не через IMAP_POLL_SECONDS:
    # так ответ, пришедший пока бот был выключен, попадёт в MAX без задержки.
    scheduler.add_job(
        check_mailbox,
        "date",
        id="inbox_check_now",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info(
        "Планировщик запущен: проверка ящика каждые %d с",
        config.IMAP_POLL_SECONDS,
    )

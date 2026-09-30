"""Доставка обращений гражданина.

Почему не РИАС ЖКХ
------------------
API РИАС ЖКХ (api.rias-gkh.ru/v2.0) предназначен для УК/РСО: он позволяет
импортировать обращения, которые организация УЖЕ получила по своим каналам
(схема AppealImportForm с полями organization_id / executor_id /
applicant_org_guid). Подать обращение от имени гражданина через него нельзя,
токен выдаётся только зарегистрированной организации.

Это ограничение подтверждено АО «Оператор ГИС ЖКХ» на рабочей встрече
21.01.2026: обращения, созданные вне ГИС ЖКХ (в CRM или мессенджере),
невозможно корректно связать с личным кабинетом жителя без идентификации
через Госуслуги. Подача обращений в мессенджере МАКС возможна только
через мини-приложение «Госуслуги Дом» с авторизацией через Госуслуги.

Поэтому здесь два канала:
  1. email  — письменное обращение в УК (ст. 11 ЖК РФ, 59-ФЗ). Работает
     сразу, ответ приходит на указанный пользователем адрес.
  2. Госуслуги Дом — официальное мини-приложение, юридически «чистый» путь.

Юридические нюансы
------------------
Обращение в электронной форме должно содержать ФИО, адрес, адрес почты для
ответа и подпись заявителя (ст. 7 59-ФЗ). Без идентификации (ЭП или ЛК
ЕСИА) обращение в орган власти не рассматривается; УК, как правило,
принимает, но не обязуется. Поэтому в письмо добавляется блок с ФИО,
адресом и подписью — это максимум, что можно сделать без ЭП.
"""

import logging
from email.message import EmailMessage
from datetime import datetime, timezone

import aiosmtplib

import config

logger = logging.getLogger(__name__)


def _format_date() -> str:
    """Дата обращения по-русски: «15 сентября 2026 г.»."""
    months = [
        "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря",
    ]
    now = datetime.now()
    return f"{now.day} {months[now.month - 1]} {now.year} г."


def is_email_configured() -> bool:
    return bool(config.SMTP_HOST and config.SMTP_USERNAME)


def check_email_config() -> list[str]:
    """Возвращает список проблем в настройках SMTP. Пустой список — всё в порядке."""
    problems: list[str] = []

    if not config.SMTP_HOST or not config.SMTP_USERNAME:
        return ["SMTP не настроен: задайте SMTP_HOST и SMTP_USERNAME в .env"]

    host = config.SMTP_HOST.strip()
    if host != config.SMTP_HOST:
        problems.append(f"В SMTP_HOST пробелы: {config.SMTP_HOST!r}")
    if not host.startswith("smtp."):
        problems.append(
            f"В SMTP_HOST нет префикса smtp.: {host!r}. "
            "Обычно должно быть smtp.mail.ru или smtp.yandex.ru"
        )

    # Домен логина и хоста должны совпадать — иначе почти всегда опечатка.
    username = config.SMTP_USERNAME.strip()
    if "@" in username:
        mail_domain = username.rsplit("@", 1)[1].lower()
        host_domain = host.split(".", 1)[1].lower() if "." in host else ""
        if host_domain and not _domains_match(mail_domain, host_domain):
            problems.append(
                f"Домены не совпадают: логин @{mail_domain}, "
                f"хост {host}. Для Mail.ru ожидается smtp.mail.ru, "
                "для Яндекса smtp.yandex.ru"
            )

    if config.SMTP_PORT == 465 and not config.SMTP_USE_TLS:
        problems.append(
            "Порт 465 требует SMTP_USE_TLS=1, иначе соединение не поднимется"
        )
    if config.SMTP_PORT == 587 and config.SMTP_USE_TLS:
        problems.append(
            "Порт 587 обычно требует SMTP_USE_TLS=0 (TLS включается STARTTLS)"
        )
    if not config.SMTP_PASSWORD:
        problems.append(
            "SMTP_PASSWORD пуст — у большинства почты нужен пароль приложения"
        )
    if not config.SMTP_SENDER_ADDRESS:
        problems.append(
            "Не задан SMTP_SENDER_ADDRESS — без адреса отправителя письмо "
            "невалидно и УК не сможет на него ответить"
        )

    return problems


def _domains_match(mail_domain: str, host_domain: str) -> bool:
    """ mail.ru и m.mail.ru считаем одним доменом."""
    def base(d):
        return d[5:] if d.startswith("smtp.") else d
    return base(mail_domain) == base(host_domain)


def build_appeal_body(appeal_id: int, address: str, category: str, text: str,
                      full_name: str = "", contact_email: str = "") -> str:
    """Текст письма по требованиям ст. 7 59-ФЗ."""
    lines = [
        f"Обращение № {appeal_id}",
        "",
        f"Заявитель: {full_name or '________________________'}",
        f"Адрес объекта: {address}",
    ]
    if contact_email:
        lines.append(f"Электронная почта заявителя: {contact_email}")
    lines += [
        f"Категория: {category}",
        f"Дата: {_format_date()}",
        "",
        text.strip(),
        "",
        "Прошу рассмотреть обращение и направить ответ в установленный срок.",
    ]

    # Ответ удобнее прислать в мессенджер, где заявитель его сразу увидит.
    bot_address = (config.SMTP_SENDER_ADDRESS or "").strip()
    if bot_address:
        lines += [
            "",
            f"Ответ прошу направить на {bot_address} — он поступит "
            "заявителю в мессенджер MAX.",
        ]

    lines += [
        "",
        f"С уважением,\n{full_name or '________________________'}",
    ]
    return "\n".join(lines)


async def send_appeal_by_email(appeal_id: int, address: str, category: str,
                               text: str, uk_email: str,
                               full_name: str = "",
                               contact_email: str = "") -> dict:
    """Отправляет письменное обращение в УК по электронной почте.

    Возвращает словарь: ok, status, detail.
    """
    if not is_email_configured():
        return {
            "ok": False,
            "status": "not_configured",
            "detail": "SMTP не настроен (задайте SMTP_HOST/SMTP_USERNAME в .env)",
        }

    problems = check_email_config()
    if problems:
        return {
            "ok": False,
            "status": "config_error",
            "detail": "; ".join(problems),
        }
    if not uk_email:
        return {
            "ok": False,
            "status": "no_target",
            "detail": "Не указан email управляющей организации",
        }

    body = build_appeal_body(
        appeal_id, address, category, text, full_name, contact_email
    )

    message = EmailMessage()
    # From обязан содержать адрес, иначе письмо невалидно и УК не ответит.
    sender = (config.SMTP_SENDER_ADDRESS or config.SMTP_USERNAME or "").strip()
    if not sender:
        return {
            "ok": False,
            "status": "config_error",
            "detail": "Не задан SMTP_SENDER_ADDRESS — письмо некуда отправлять",
        }
    if config.SMTP_SENDER_NAME:
        message["From"] = f"{config.SMTP_SENDER_NAME} <{sender}>"
    else:
        message["From"] = sender
    # Кнопка «Ответить» у УК должна вести в ящик бота, а не в ящик заявителя.
    message["Reply-To"] = sender
    message["To"] = uk_email
    message["Subject"] = (
        f"Обращение № {appeal_id} ({category.lower()}) — {address}"
    )
    message["Date"] = datetime.now(timezone.utc)
    # УК нажала «Ответить» — по этой ссылке бот найдёт обращение.
    message["Message-ID"] = f"<appeal-{appeal_id}@zhkh-bot.local>"
    message.set_content(body)

    try:
        await aiosmtplib.send(
            message,
            hostname=config.SMTP_HOST,
            port=config.SMTP_PORT,
            username=config.SMTP_USERNAME or None,
            password=config.SMTP_PASSWORD or None,
            use_tls=config.SMTP_USE_TLS,
            timeout=30,
        )
    except aiosmtplib.SMTPException as exc:
        logger.exception("SMTP ошибка при отправке обращения №%s", appeal_id)
        return {"ok": False, "status": "smtp_error", "detail": str(exc)}
    except Exception as exc:
        logger.exception("Неизвестная ошибка отправки обращения №%s", appeal_id)
        return {"ok": False, "status": "error", "detail": str(exc)}

    logger.info("Обращение №%s отправлено на %s", appeal_id, uk_email)
    return {
        "ok": True,
        "status": "sent",
        "detail": f"Отправлено на {uk_email}",
        "body": body,
    }


# Оставлено для совместимости: прежняя точка входа.
# Теперь возвращает инструкцию и ссылку на официальное мини-приложение.
async def send_appeal_to_uk(appeal_id: int, address: str, category: str,
                            text: str) -> dict:
    return {
        "channel": "manual",
        "status": "saved",
        "message": (
            "Обращение сохранено.\n\n"
            "Отправьте его одним из способов:\n"
            f"• мини-приложение «Госуслуги Дом»: {config.GOSUSLUGI_DOM_BOT_URL}\n"
            "• личный кабинет ГИС ЖКХ: dom.gosuslugi.ru\n"
            "• электронная почта управляющей организации\n\n"
            "Срок рассмотрения — до 30 дней."
        ),
    }


__all__ = [
    "send_appeal_by_email",
    "send_appeal_to_uk",
    "build_appeal_body",
    "is_email_configured",
]

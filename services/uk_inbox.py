"""Приём ответов УК из почтового ящика и пересылка их в мессенджер MAX.

Как это работает
---------------
Управляющая организация отвечает на письмо, отправленное ботом. Ответ
приходит в тот же ящик: в письме стоит заголовок ``Reply-To`` с адресом
бота, поэтому кнопка «Ответить» в почтовом клиенте УК ведёт именно
сюда. Бот периодически опрашивает ящик по IMAP, находит новые письма
и пересылает их жителю в MAX.

Привязка письма к обращению
---------------------------
Письмо привязывается к обращению тремя способами, в этом порядке:

1. ``In-Reply-To`` / ``References`` — УК нажала «Ответить», поэтому
   сохранилась ссылка на наш ``Message-ID`` вида ``appeal-42@...``.
2. Номер обращения в теме или в тексте письма — «Обращение № 42».
3. Не подошло — письмо считается неопознанным: попадает в журнал,
   а при заданном ``IMAP_ADMIN_CHAT_ID`` приходит администратору.

Курсор чтения
-------------
Запоминается UID последнего обработанного письма (таблица
``inbox_state``). При первом запуске курсор ставится на текущий максимум,
чтобы бот не начал присылать жителю старые письма из ящика. Чтобы
разобрать уже накопленную почту, задайте ``IMAP_SKIP_EXISTING=0``.

Почему без дополнительных пакетов
---------------------------------
Используется ``imaplib`` из стандартной библиотеки. Блокирующие вызовы
IMAP выполняются в отдельном потоке через ``asyncio.to_thread``, а вся
работа с базой — уже в асинхронной части, обычным ``await``.
"""

import asyncio
import email
import imaplib
import logging
import re
from dataclasses import dataclass, field
from email.header import decode_header, make_header
from email.utils import getaddresses
from email.message import Message
from email.policy import default as default_policy
from html.parser import HTMLParser

import config
from database import (
    find_appeal_owner,
    get_inbox_state,
    save_uk_response,
    set_inbox_state,
)
from keyboards import appeal_actions

logger = logging.getLogger(__name__)

# Номер обращения в теме письма: «Обращение № 42 (сантехника) — ул. Мира, 1».
_APPEAL_NUM_RE = re.compile(
    r"обращени[еяюи]\s*(?:№\s*|N\s*)?(\d{1,9})",
    re.IGNORECASE,
)

# Ссылка на исходное письмо, которую добавляет почтовый клиент УК.
_APPEAL_REF_RE = re.compile(r"appeal-(\d{1,9})@", re.IGNORECASE)

# Признаки автоответов — жителю их пересылать не нужно.
_AUTOREPLY_MARKERS = (
    "auto-submitted",
    "autoreply",
    "out of office",
    "out-of-office",
    "автоответ",
    "автоматический ответ",
    "вы находитесь в отпуске",
)

# Адреса в угловых скобках: <user@example.com>
_TAG_RE = re.compile(r"<[^<>@\s]+@[^<>\s]+>")
_HTML_DROP_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)
_HTML_TAG_RE = re.compile(r"<[^>]+>")

# Строки, с которых начинается цитирование нашего письма.
_QUOTE_START_RE = re.compile(
    r"^(в\s+ответ\s+на\b"
    r"|on\s+.+\s+wrote:"
    r"|отправленное\s+сообщение"
    r"|-\{3,}\s*оригинал"
    r"|сообщение\s+отправлено)",
    re.I,
)


@dataclass
class InboxResponse:
    """Письмо, удачно привязанное к обращению."""
    appeal_id: int
    user_id: int
    address: str
    subject: str
    sender: str
    body: str
    matched_by: str


@dataclass
class InboxResult:
    """Итог одной проверки ящика."""
    responses: list[InboxResponse] = field(default_factory=list)
    unmatched: list[dict] = field(default_factory=list)
    skipped: int = 0
    error: str = ""


# ===== ПРОВЕРКА НАСТРОЕК =====

def is_inbox_configured() -> bool:
    """Готов ли бот читать ящик."""
    if not config.IMAP_ENABLED:
        return False
    return bool(config.IMAP_HOST and config.IMAP_USERNAME and config.IMAP_PASSWORD)


def check_inbox_config() -> list[str]:
    """Проблемы в настройках IMAP. Пустой список — всё в порядке."""
    problems: list[str] = []

    if not config.IMAP_ENABLED:
        return ["IMAP_ENABLED=0 — ответы УК не будут приходить в бот"]

    if not config.IMAP_HOST:
        return ["Не задан IMAP_HOST (imap.mail.ru / imap.yandex.ru)"]
    if not config.IMAP_USERNAME:
        problems.append("Не задан IMAP_USERNAME")
    if not config.IMAP_PASSWORD:
        problems.append(
            "Не задан IMAP_PASSWORD — у большинства почты это пароль приложения"
        )

    host = config.IMAP_HOST.strip()
    if not host.startswith("imap."):
        problems.append(
            f"В IMAP_HOST нет префикса imap.: {host!r}. "
            "Обычно это imap.mail.ru или imap.yandex.ru"
        )

    if "@" in config.IMAP_USERNAME and "." in host:
        mail_domain = config.IMAP_USERNAME.rsplit("@", 1)[1].lower()
        host_domain = host.split(".", 1)[1].lower()
        # Корпоративные почты любят imap.domen.ru при ящике user@domen.ru —
        # это нормально, поэтому проверяем только вхождение домена.
        if not host_domain.startswith(mail_domain):
            problems.append(
                f"Домены не совпадают: ящик @{mail_domain}, хост {host}"
            )

    if config.IMAP_PORT == 993 and not config.IMAP_USE_SSL:
        problems.append("Порт 993 требует IMAP_USE_SSL=1")
    if config.IMAP_PORT != 993 and config.IMAP_USE_SSL:
        problems.append("Сквозное шифрование обычно используется только на 993")

    return problems


# ===== РАЗБОР ПИСЕМ =====

def _decode_header(value) -> str:
    """Декодирует MIME-заголовок в читаемую строку."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001 — заголовок может быть любым
        return str(value)


class _TextExtractor(HTMLParser):
    """Вытаскивает текст из HTML-письма — УК часто отвечает именно HTML."""

    def __init__(self):
        super().__init__()
        self.chunks: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("br", "p", "div", "tr", "li"):
            self.chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag in ("p", "div", "tr", "li"):
            self.chunks.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.chunks.append(data)


def _html_to_text(html: str) -> str:
    """HTML в читаемый текст."""
    html = _HTML_DROP_RE.sub(" ", html)
    parser = _TextExtractor()
    try:
        parser.feed(html)
        text = "".join(parser.chunks)
    except Exception:  # noqa: BLE001 — битый HTML
        text = _HTML_TAG_RE.sub(" ", html)
    return re.sub(r"[ \t]+", " ", text).strip()


def _part_text(part: Message):
    """Текст одной части письма. None — если это не текст."""
    if part.is_multipart():
        return None
    ctype = part.get_content_type()
    if ctype not in ("text/plain", "text/html"):
        return None
    try:
        content = part.get_content()
    except Exception:  # noqa: BLE001 — кривая кодировка
        payload = part.get_payload(decode=True) or b""
        charset = part.get_content_charset() or "utf-8"
        try:
            content = payload.decode(charset, "replace")
        except LookupError:
            content = payload.decode("utf-8", "replace")
    if not isinstance(content, str):
        content = str(content)
    if ctype == "text/html":
        return _html_to_text(content)
    return content.strip()


def message_to_text(msg: Message) -> str:
    """Читаемый текст письма: сначала plain, при его отсутствии — html."""
    plain: list[str] = []
    html: list[str] = []
    for part in msg.walk():
        text = _part_text(part)
        if not text:
            continue
        if part.get_content_type() == "text/plain":
            plain.append(text)
        else:
            html.append(text)

    joined = "\n".join(plain).strip()
    if joined:
        return joined
    return "\n".join(html).strip()


def _strip_quotes(body: str) -> str:
    """Отбрасывает цитирование — нужен ответ УК, а не наш текст."""
    lines: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith(">") or stripped.startswith("|"):
            continue
        if _QUOTE_START_RE.match(stripped):
            break
        lines.append(line)
    result = "\n".join(lines).strip()
    return result or body.strip()


def _clean_body(text: str, limit: int = 3500) -> str:
    """Текст письма без цитат и с ограничением длины для MAX."""
    text = _strip_quotes(text)
    if len(text) > limit:
        text = text[:limit] + "\n…(сообщение обрезано)"
    return text


def extract_appeal_number(subject: str, body: str,
                          msg: Message | None = None) -> tuple[int | None, str]:
    """Ищет номер обращения. Возвращает (номер, способ) либо (None, '')."""
    if msg is not None:
        for header in ("In-Reply-To", "References", "Message-ID"):
            match = _APPEAL_REF_RE.search(_decode_header(msg.get(header)))
            if match:
                return int(match.group(1)), f"ссылка в {header}"

    for source, text in (("теме", subject), ("тексте", body)):
        match = _APPEAL_NUM_RE.search(text)
        if match:
            return int(match.group(1)), f"номер в {source}"

    return None, ""


def is_own_message(msg: Message) -> bool:
    """Письмо, отправленное самим ботом (например, при «Ответить всем»).

    Сравнение по адресу, а не по всей строке From: клиент может добавить
    отображаемое имя — «ЖКХ-бот <bot@mail.ru>».
    """
    own = (config.SMTP_SENDER_ADDRESS or "").strip().lower()
    if not own:
        return False

    # getaddresses() разбирает и «Имя <адрес>», и голый адрес.
    for _name, address in getaddresses([msg.get("From", "")]):
        if address.strip().lower() == own:
            return True

    # Запасной вариант: адрес мог остаться в нестандартном виде.
    for addr in _TAG_RE.findall(msg.get("From", "")):
        if addr.strip("<> ").lower() == own:
            return True
    return False


def is_autoreply(msg: Message) -> bool:
    """Автоответ почтовой системы — жителю его пересылать не нужно."""
    if msg.get("Auto-Submitted"):
        return True
    if str(msg.get("Precedence", "")).lower() in ("bulk", "auto_reply", "junk"):
        return True
    haystack = " ".join(
        _decode_header(msg.get(h, "")) for h in ("Subject", "From")
    ).lower()
    return any(marker in haystack for marker in _AUTOREPLY_MARKERS)


# ===== ЧТЕНИЕ ЯЩИКА (блокирующий код, выполняется в потоке) =====

def _decode_fetched(data) -> bytes:
    """Из ответа UID FETCH достаёт сырое письмо."""
    if not data:
        raise ValueError("Пустой ответ сервера")
    for part in data:
        if isinstance(part, tuple) and len(part) >= 2 and part[1]:
            return part[1]
    raise ValueError("Письмо не найдено в ответе сервера")


def _connect() -> imaplib.IMAP4:
    """Открывает соединение с ящиком и авторизуется.

    Без login() сервер остаётся в состоянии NONAUTH, и любая команда —
    даже SELECT — отклоняется: «command SELECT illegal in state NONAUTH».
    """
    if config.IMAP_USE_SSL:
        client = imaplib.IMAP4_SSL(
            config.IMAP_HOST, config.IMAP_PORT, timeout=30
        )
    else:
        client = imaplib.IMAP4(config.IMAP_HOST, config.IMAP_PORT, timeout=30)
        client.starttls()

    try:
        typ, _ = client.login(config.IMAP_USERNAME, config.IMAP_PASSWORD)
    except imaplib.IMAP4.error as exc:
        # Почта отвечает по-разному, поэтому причина спрятана в тексте.
        raise RuntimeError(
            f"Не удалось войти в ящик {config.IMAP_USERNAME} на "
            f"{config.IMAP_HOST}: {exc}. Проверьте пароль приложения и "
            f"включён ли доступ по IMAP"
        ) from exc
    if typ != "OK":
        raise RuntimeError(f"Вход в ящик не выполнен ({typ})")

    return client


def _fetch_since_blocking(last_uid: int,
                          skip_existing: bool) -> tuple[list[tuple[int, bytes]], int]:
    """Читает письма с UID больше last_uid. Блокирующий вызов.

    Возвращает ((uid, сырые байты), максимальный UID в ящике).
    """
    client = _connect()
    try:
        typ, _ = client.select(config.IMAP_MAILBOX)
        if typ != "OK":
            raise RuntimeError(f"Не удалось открыть папку {config.IMAP_MAILBOX}")

        typ, data = client.uid("SEARCH", None, "ALL")
        if typ != "OK":
            raise RuntimeError("Поиск UID не выполнен")

        uids = [int(x) for x in (data[0] or b"").split() if x.strip().isdigit()]
        max_uid = max(uids) if uids else last_uid

        # Первый запуск: пропускаем всё, что уже лежит в ящике.
        if skip_existing and last_uid == 0:
            logger.info("Первый запуск чтения ящика: курсор = UID %s", max_uid)
            return [], max_uid

        letters: list[tuple[int, bytes]] = []
        for uid in sorted(u for u in uids if u > last_uid):
            typ, fetched = client.uid("FETCH", str(uid), "(RFC822)")
            if typ != "OK":
                logger.warning("Не удалось забрать письмо UID=%s", uid)
                continue
            try:
                letters.append((uid, _decode_fetched(fetched)))
            except ValueError as exc:
                logger.warning("Письмо UID=%s не разобрано: %s", uid, exc)
            # Отмечаем прочитанным, чтобы письмо не вернулось после сбоя.
            try:
                client.uid("STORE", str(uid), "+FLAGS", "\\Seen")
            except Exception as exc:  # noqa: BLE001 — не критично
                logger.warning("Не отмечено прочитанным UID=%s: %s", uid, exc)

        return letters, max_uid
    finally:
        try:
            client.logout()
        except Exception:  # noqa: BLE001 — сервер мог уже закрыть сессию
            pass


# ===== СБОРКА ОТВЕТОВ =====

async def collect_new_responses(skip_existing: bool = True) -> InboxResult:
    """Читает ящик и раскладывает письма по обращениям. Ответы сохраняет."""
    result = InboxResult()

    if not is_inbox_configured():
        problems = check_inbox_config()
        result.error = "; ".join(problems) or "IMAP не настроен"
        return result

    state = await get_inbox_state()
    last_uid = int(state.get("last_uid") or 0)

    try:
        letters, max_uid = await asyncio.to_thread(
            _fetch_since_blocking, last_uid, skip_existing
        )
    except Exception as exc:  # noqa: BLE001 — сеть, TLS, авторизация
        logger.exception("Не удалось прочитать почтовый ящик")
        result.error = str(exc)
        await set_inbox_state(last_uid, error=str(exc))
        return result

    for uid, raw in letters:
        try:
            msg = email.message_from_bytes(raw, policy=default_policy)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Письмо UID=%s не разобрано: %s", uid, exc)
            result.skipped += 1
            continue

        if is_own_message(msg):
            result.skipped += 1
            continue
        if is_autoreply(msg):
            result.skipped += 1
            continue

        subject = _decode_header(msg.get("Subject"))
        sender = _decode_header(msg.get("From"))
        body = _clean_body(message_to_text(msg))
        appeal_id, matched_by = extract_appeal_number(subject, body, msg)

        if appeal_id is None:
            result.unmatched.append({
                "uid": uid, "subject": subject or "(без темы)",
                "sender": sender, "body": body,
            })
            continue

        owner = await find_appeal_owner(appeal_id)
        if not owner or owner[0] is None:
            result.unmatched.append({
                "uid": uid, "subject": subject, "sender": sender,
                "body": body, "appeal_id": appeal_id,
                "reason": "обращение не найдено в базе",
            })
            continue

        user_id, address = owner[0], owner[1]
        text = body or "(письмо без текстовой части)"
        await save_uk_response(appeal_id, text, status="answered")

        result.responses.append(InboxResponse(
            appeal_id=appeal_id,
            user_id=user_id,
            address=address or "—",
            subject=subject or f"Ответ по обращению № {appeal_id}",
            sender=sender or "—",
            body=text,
            matched_by=matched_by,
        ))
        logger.info("Ответ УК по обращению №%s (%s)", appeal_id, matched_by)

    new_uid = max([last_uid, *[uid for uid, _ in letters], max_uid])
    await set_inbox_state(
        new_uid,
        error="",
        processed=int(state.get("processed") or 0) + len(result.responses),
    )
    return result


# ===== ПЕРЕСЫЛКА В МЕССЕНДЖЕР =====

def format_response(response: InboxResponse) -> str:
    """Текст сообщения жителю с ответом УК."""
    subject = f"\n📌 Тема письма: {response.subject}\n" if response.subject else "\n"
    return (
        f"📬 *Ответ по обращению №{response.appeal_id}*\n\n"
        f"📍 {response.address}\n"
        f"🏢 {response.sender}\n"
        f"{subject}\n"
        f"{response.body}\n\n"
        "━━━━━━━━━━━━━━━━\n"
        "Это ответ, полученный на почтовый ящик бота. "
        "Если нужно что-то ещё — подайте новое обращение."
    )


def format_unmatched(item: dict) -> str:
    """Сводка по письму, которое не удалось привязать к обращению."""
    reason = item.get("reason")
    head = f"Тема: {item.get('subject') or '(без темы)'}"
    if reason:
        head += f"\nПричина: {reason}"
    elif item.get("appeal_id"):
        head += f"\nНомер обращения: {item['appeal_id']}"
    body = (item.get("body") or "")[:500]
    tail = f"\n\n{body}" if body else ""
    return f"✉️ *Нераспознанное письмо*\n\n{head}\n\nОт: {item.get('sender')}{tail}"


async def notify(bot, result: InboxResult) -> int:
    """Отправляет найденные ответы в MAX. Возвращает число доставленных."""
    delivered = 0

    for response in result.responses:
        try:
            await bot.send_message(
                chat_id=response.user_id,
                text=format_response(response),
                attachments=[appeal_actions(response.appeal_id)],
            )
            delivered += 1
        except Exception as exc:  # noqa: BLE001 — чат мог быть удалён
            logger.exception("Не удалось отправить ответ жителю %s: %s",
                             response.user_id, exc)

    if result.unmatched and config.IMAP_ADMIN_CHAT_ID:
        try:
            await bot.send_message(
                chat_id=int(config.IMAP_ADMIN_CHAT_ID),
                text=(
                    f"✉️ Писем без привязки к обращению: "
                    f"{len(result.unmatched)}\n\n"
                    + "\n\n".join(format_unmatched(i)
                                  for i in result.unmatched[:5])
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Не удалось отправить сводку администратору: %s", exc)
    elif result.unmatched:
        logger.warning(
            "Писем без привязки к обращению: %d (задайте IMAP_ADMIN_CHAT_ID, "
            "чтобы получать их в мессенджере)",
            len(result.unmatched),
        )

    return delivered


async def check_and_notify(bot, skip_existing: bool | None = None) -> InboxResult:
    """Полный цикл: прочитать ящик, сохранить ответы, разослать в MAX."""
    if skip_existing is None:
        skip_existing = config.IMAP_SKIP_EXISTING
    result = await collect_new_responses(skip_existing=skip_existing)
    if result.error:
        logger.warning("Проверка почты не удалась: %s", result.error)
        return result

    if result.responses:
        await notify(bot, result)
    logger.info(
        "Проверка почты: ответов %d, нераспознанных %d, пропущено %d",
        len(result.responses), len(result.unmatched), result.skipped,
    )
    return result

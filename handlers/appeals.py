from maxapi import Router, F
from maxapi.types import MessageCallback, MessageCreated
from maxapi.context import MemoryContext
from keyboards import (
    address_kb,
    categories_kb,
    main_menu,
    appeal_actions,
    inbox_check_kb,
    profile_kb,
    replies_kb,
    uk_email_kb,
    home_kb,
)
from database import (
    create_appeal,
    get_user_appeals,
    get_appeal_owned,
    get_profile,
    save_profile,
    set_delivery,
    get_deliveries,
    get_appeals_with_replies,
)
from services.uk_delivery import (
    send_appeal_by_email,
    build_appeal_body,
)
from services.uk_inbox import (
    collect_new_responses,
    check_inbox_config,
    is_inbox_configured,
)
from states import Form
from texts import WELCOME_TEXT
import config
from config import GOSUSLUGI_DOM_BOT_URL, IMAP_POLL_SECONDS
import logging
import re

router = Router()
logger = logging.getLogger(__name__)


def profile_text(profile: dict) -> str:
    """Единый текст экрана «Мои данные» — из меню и после правки."""
    return (
        "⚙️ *Мои данные*\n\n"
        f"👤 ФИО: {profile.get('full_name') or '— не указано'}\n"
        f"🏠 Адрес дома: {profile.get('address') or '— не указан'}\n"
        f"📧 Мой email: {profile.get('email') or '— не указан'}\n"
        f"🏢 Email УК: {profile.get('uk_email') or '— не указан'}\n\n"
        "Эти данные подставляются в обращение.\n"
        "ФИО и адрес обязательны по ст. 7 59-ФЗ."
    )


CATEGORY_NAMES = {
    "plumbing": "Сантехника",
    "electric": "Электрика",
    "heating": "Отопление",
    "cleaning": "Уборка",
    "capital": "Капремонт",
    "other": "Прочее",
}

STATUS_ICONS = {
    "new": "🆕",
    "in_progress": "⏳",
    "answered": "📬",
    "done": "✅",
    "rejected": "❌",
}

DELIVERY_ICONS = {
    "draft": "📝",
    "sent": "✅",
    "not_configured": "⚠️",
    "config_error": "⚠️",
    "no_target": "⚠️",
    "smtp_error": "❌",
    "error": "❌",
}

DELIVERY_LABELS = {
    "draft": "черновик, не отправлено",
    "sent": "отправлено на email УК",
    "not_configured": "почта не настроена",
    "config_error": "ошибка в настройках почты",
    "no_target": "не указан email УК",
    "smtp_error": "ошибка SMTP",
    "error": "ошибка отправки",
}

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-zА-Яа-я]{2,}$")


def valid_email(value: str) -> bool:
    return bool(EMAIL_RE.match(value.strip()))


@router.message_callback()
async def handle_callback(event: MessageCallback, context: MemoryContext):
    payload = event.callback.payload or ""
    logger.info(f"CALLBACK: {payload!r}")

    # ===== ВОЗВРАТ В ГЛАВНОЕ МЕНЮ =====
    # Сбрасывает FSM: иначе пользователь остался бы в состоянии ввода адреса.
    if payload == "menu:home":
        await context.clear()
        await event.message.answer(
            WELCOME_TEXT,
            attachments=[main_menu()],
        )
        await event.ack(notification="Главное меню")
        return

    # ===== МОИ ДАННЫЕ =====
    if payload == "menu:profile":
        chat_id, user_id = event.get_ids()
        profile = await get_profile(user_id) or {}
        await event.message.answer(
            profile_text(profile), attachments=[profile_kb(user_id)]
        )
        await event.ack(notification="Мои данные")
        return

    if payload.startswith("prof:"):
        chat_id, user_id = event.get_ids()
        field = payload.split(":")[1]

        if field == "name":
            await context.set_state(Form.editing_name)
            await event.message.answer(
                "Введите новое ФИО:",
                attachments=[home_kb()],
            )
        elif field == "address":
            await context.set_state(Form.editing_address)
            await event.message.answer(
                "Введите адрес дома — так обращение можно однозначно "
                "идентифицировать.\n\nПример: ул. Ленина, д. 5, кв. 12\n"
                "Или «удалить», чтобы убрать его.",
                attachments=[home_kb()],
            )
        elif field == "email":
            await context.set_state(Form.editing_email)
            await event.message.answer(
                "Введите email для ответов от УК.\n"
                "Или «удалить», чтобы убрать его.",
                attachments=[home_kb()],
            )
        elif field == "uk_email":
            await context.set_state(Form.editing_uk_email)
            await event.message.answer(
                "Введите email управляющей организации.\n"
                "Или «удалить», чтобы убрать его.",
                attachments=[home_kb()],
            )
        await event.ack(notification="Введите значение")
        return

    # ===== ОТВЕТЫ УК =====
    if payload == "menu:replies":
        chat_id, user_id = event.get_ids()
        rows = await get_appeals_with_replies(user_id)

        if not rows:
            text = (
                "📬 *Ответы управляющей организации*\n\n"
                "Ответов пока нет. Они придут сюда автоматически, как только "
                "УК напишет на почтовый ящик бота.\n\n"
                f"Проверяю ящик каждые {IMAP_POLL_SECONDS} с."
            )
        else:
            text = f"📬 *Ответы управляющей организации* ({len(rows)}):\n\n"
            for appeal_id, address, category, response, when in rows:
                preview = " ".join((response or "").split())
                if len(preview) > 300:
                    preview = preview[:300] + "…"
                text += f"✉️ *Обращение №{appeal_id}* — {category}\n"
                text += f"📍 {address}\n"
                text += f"🕐 {when}\n"
                text += f"{preview}\n"
                text += "━━━━━━━━━━━━━━━\n"

        await event.message.answer(text, attachments=[replies_kb()])
        await event.ack(notification="Ответы УК")
        return

    # ===== РУЧНАЯ ПРОВЕРКА ПОЧТЫ =====
    if payload == "inbox:check":
        _, user_id = event.get_ids()
        await event.ack(notification="Проверяю…")

        if not is_inbox_configured():
            await event.message.answer(
                "⚠️ Приём писем не настроен на сервере.\n"
                f"Причина: {'; '.join(check_inbox_config())}\n\n"
                "Задайте IMAP_HOST и IMAP_PASSWORD в .env, "
                "подробности — docs/НАСТРОЙКА_ОТВЕТЫ_УК.md",
                attachments=[inbox_check_kb()],
            )
            return

        # Ответы в MAX рассылает планировщик: ручная проверка только
        # забирает письма и сообщает итог, иначе житель получит дубли.
        result = await collect_new_responses(
            skip_existing=config.IMAP_SKIP_EXISTING
        )

        if result.error:
            await event.message.answer(
                f"⚠️ Не удалось проверить почту: {result.error}",
                attachments=[inbox_check_kb()],
            )
            return

        mine = [r for r in result.responses if r.user_id == user_id]
        if mine:
            titles = ", ".join(f"№{r.appeal_id}" for r in mine[:5])
            note = (
                f"📬 Ответов по вашим обращениям: {len(mine)} ({titles}).\n\n"
                "Они уже сохранены в разделе «Ответы УК» — откройте его "
                "кнопкой ниже."
            )
        elif result.responses or result.unmatched:
            note = "Новых ответов на ваши обращения нет."
        else:
            note = (
                "Новых писем нет. Ответ появится здесь автоматически, "
                f"как только УК ответит (проверка каждые "
                f"{IMAP_POLL_SECONDS} с)."
            )

        await event.message.answer(note, attachments=[inbox_check_kb()])
        return

    # ===== ГЛАВНОЕ МЕНЮ =====
    if payload == "menu:appeal":
        chat_id, user_id = event.get_ids()
        profile = await get_profile(user_id) or {}
        known = []
        if profile.get("full_name"):
            known.append(f"ФИО: {profile['full_name']}")
        if profile.get("email"):
            known.append(f"Ваш email: {profile['email']}")
        if profile.get("uk_email"):
            known.append(f"Email УК: {profile['uk_email']}")

        await context.set_state(Form.waiting_address)

        prefix = "\n".join(known) + "\n\n" if known else ""

        # Адрес из профиля можно не вводить заново — но проверить стоит:
        # обращение может быть по другому объекту.
        saved_address = (profile.get("address") or "").strip()
        if saved_address:
            await event.message.answer(
                f"{prefix}В профиле сохранён адрес:\n"
                f"*{saved_address}*\n\n"
                "Используйте его или напишите другой:",
                attachments=[address_kb(saved_address)],
            )
        else:
            await event.message.answer(
                f"{prefix}Укажите адрес дома:\n\n"
                "Пример: ул. Ленина, д. 5, кв. 12",
                attachments=[home_kb()],
            )
        await event.ack(notification="Ок")

    # ===== ВЗЯТЬ АДРЕС ИЗ ПРОФИЛЯ =====
    elif payload == "use_saved_address":
        _, user_id = event.get_ids()
        profile = await get_profile(user_id) or {}
        saved_address = (profile.get("address") or "").strip()

        if not saved_address:
            await event.message.answer(
                "В профиле нет сохранённого адреса. Напишите его текстом.\n\n"
                "Пример: ул. Ленина, д. 5, кв. 12",
                attachments=[home_kb()],
            )
            await event.ack(notification="Адрес не найден")
            return

        await context.update_data(address=saved_address)
        await context.set_state(Form.waiting_category)
        await event.message.answer(
            f"✅ Использую адрес: {saved_address}\n\n"
            "Выберите категорию проблемы:",
            attachments=[categories_kb()],
        )
        await event.ack(notification="Адрес")

    elif payload == "menu:my_appeals":
        chat_id, user_id = event.get_ids()
        rows = await get_user_appeals(user_id)

        if not rows:
            await event.message.answer(
                "У вас пока нет обращений.",
                attachments=[home_kb()],
            )
            await event.ack(notification="Пусто")
            return

        text = "📋 *Ваши обращения:*\n\n"
        for r in rows:
            appeal_id, address, category, appeal_text, status, uk_response, created, updated = r
            icon = STATUS_ICONS.get(status, "❓")

            deliveries = await get_deliveries(appeal_id)
            if deliveries:
                last = deliveries[-1]
                send_icon = DELIVERY_ICONS.get(last[2], "•")
                send_label = DELIVERY_LABELS.get(last[2], last[2])
                send_line = f"{send_icon} Отправка: {send_label}"
                if last[1]:
                    send_line += f" ({last[1]})"
            else:
                send_line = "📨 Отправка: не выполнялась"

            text += f"{icon} *Обращение №{appeal_id}*\n"
            text += f"📍 Адрес: {address}\n"
            text += f"📁 Категория: {category}\n"
            text += f"📝 Текст: {appeal_text[:100]}{'...' if len(appeal_text) > 100 else ''}\n"
            text += f"📌 Статус: {status}\n"
            text += f"{send_line}\n"
            text += f"🕐 Создано: {created}\n"
            if updated and updated != created:
                text += f"🔄 Обновлено: {updated}\n"
            if uk_response:
                text += f"💬 Ответ УК: {uk_response[:200]}\n"
            text += "━━━━━━━━━━━━━━━\n"

        await event.message.answer(text, attachments=[home_kb()])
        await event.ack(notification=f"Найдено: {len(rows)}")

    # ===== ПРОВЕРКА ПРОТОКОЛА =====
    elif payload == "menu:vote":
        await context.set_state(Form.waiting_protocol)
        await event.message.answer(
            "🗳 *Проверка протокола общего собрания*\n\n"
            "Отправьте фото протокола — распознаю текст "
            "и попробую найти подписантов.",
            attachments=[home_kb()],
        )
        await event.ack(notification="Жду фото протокола")

    # ===== НАЧИСЛЕНИЯ И КАПРЕМОНТ =====
    elif payload == "menu:charges":
        await context.set_state(Form.waiting_receipt)
        await event.message.answer(
            "💰 *Начисления и капремонт*\n\n"
            "Отправьте фото квитанции — разберу построчно.",
            attachments=[home_kb()],
        )
        await event.ack(notification="Жду фото квитанции")

    # ===== КАТЕГОРИИ ОБРАЩЕНИЙ =====
    elif payload.startswith("cat:"):
        cat = payload.split(":")[1]
        await context.update_data(category=cat)
        await context.set_state(Form.waiting_text)
        await event.message.answer(
            f"Категория: {CATEGORY_NAMES.get(cat, cat)}\n\nОпишите проблему:",
            attachments=[home_kb()],
        )
        await event.ack(notification="Категория выбрана")

    # ===== ДЕТАЛЬНАЯ КАРТОЧКА ОБРАЩЕНИЯ =====
    elif payload.startswith("refresh:"):
        appeal_id = int(payload.split(":")[1])
        _, user_id = event.get_ids()
        row = await get_appeal_owned(appeal_id, user_id)
        if row:
            appeal_id, address, category, appeal_text, status, uk_response, created, updated = row
            # Статус «получен ответ» ставит почтовый модуль; в списке
            # обращений он выделен иконкой, здесь — подписью.
            status_text = {
                "new": "новое",
                "in_progress": "в работе",
                "answered": "получен ответ",
                "done": "выполнено",
                "rejected": "отклонено",
            }.get(status, status)

            text = (
                f"📄 *Обращение №{appeal_id}*\n\n"
                f"📍 *Адрес:* {address}\n"
                f"📁 *Категория:* {category}\n"
                f"📝 *Текст:*\n{appeal_text}\n\n"
                f"📌 *Статус:* {status_text}\n"
                f"🕐 *Создано:* {created}\n"
            )
            if updated and updated != created:
                text += f"🔄 *Обновлено:* {updated}\n"
            if uk_response:
                text += f"\n💬 *Ответ УК:*\n{uk_response}\n"

            history = await get_deliveries(appeal_id)
            if history:
                text += "\n📨 *История отправки:*\n"
                for channel, target, dstatus, detail, when in history:
                    icon = DELIVERY_ICONS.get(dstatus, "•")
                    label = DELIVERY_LABELS.get(dstatus, dstatus)
                    where = f" → {target}" if target else ""
                    text += f"{icon} {label}{where} ({when})\n"
                    if detail and dstatus not in ("sent",):
                        text += f"   _{detail[:120]}_\n"

            await event.message.answer(
                text, attachments=[appeal_actions(appeal_id)]
            )
        else:
            await event.message.answer(
                "Обращение не найдено.",
                attachments=[home_kb()],
            )
        await event.ack(notification="Готово")

    # ===== ПОДТВЕРЖДЕНИЕ / ОСПАРИВАНИЕ ПРОТОКОЛА =====
    # Обрабатывается в handlers/votes.py — здесь только переход в меню.
    elif payload.startswith("vote:confirm:"):
        await event.message.answer(
            "✅ Принято. Ваша подпись в протоколе подтверждена.",
            attachments=[home_kb()],
        )
        await event.ack(notification="Подтверждено")

    elif payload.startswith("vote:dispute:"):
        await event.message.answer(
            "❌ Зафиксировано оспаривание.\n\n"
            "Что делать:\n"
            "1. Запросите в УК оригинал протокола и реестр подписей.\n"
            "2. Подайте заявление в прокуратуру.\n"
            "3. Иск в суд о признании решения недействительным.",
            attachments=[home_kb()],
        )
        await event.ack(notification="Оспорено")

    # ===== ЖАЛОБА В ГЖИ =====
    elif payload.startswith("gji:"):
        appeal_id = int(payload.split(":")[1])
        await event.message.answer(
            f"⚠️ Жалоба по обращению №{appeal_id} зафиксирована.\n\n"
            "Направьте жалобу в ГЖИ через Госуслуги.Дом.",
            attachments=[home_kb()],
        )
        await event.ack(notification="Жалоба принята")

    # ===== ВЗЯТЬ EMAIL УК ИЗ ПРОФИЛЯ =====
    elif payload == "use_saved_uk_email":
        _, user_id = event.get_ids()
        profile = await get_profile(user_id) or {}
        saved_uk_email = (profile.get("uk_email") or "").strip()

        if not saved_uk_email:
            await event.message.answer(
                "В профиле нет сохранённого email УК. Введите его, пожалуйста.\n"
                "Где взять: сайт или квитанция вашей управляющей "
                "организации.",
                attachments=[home_kb()],
            )
            await event.ack(notification="Адрес не найден")
            return

        data = await context.get_data()
        if not data.get("appeal_id"):
            # Нажали вне подачи обращения — нечего отправлять.
            await event.message.answer(
                "Активного обращения нет. Подайте обращение заново — "
                "«📝 Подать обращение» в главном меню.",
                attachments=[home_kb()],
            )
            await event.ack(notification="Нет обращения")
            return

        await context.update_data(uk_email=saved_uk_email)
        await event.ack(notification="Отправляю…")
        await _deliver_from_context(event, context, user_id, saved_uk_email)
        return

    # ===== ПОВТОРНАЯ ОТПРАВКА =====
    elif payload.startswith("resend:"):
        appeal_id = int(payload.split(":")[1])
        _, user_id = event.get_ids()
        row = await get_appeal_owned(appeal_id, user_id)
        if not row:
            await event.message.answer(
                "Обращение не найдено.",
                attachments=[home_kb()],
            )
            await event.ack(notification="Ошибка")
            return

        _, address, category, appeal_text, _, _, _, _ = row
        profile = await get_profile(user_id) or {}

        # Данные могли измениться в профиле — используем актуальные.
        full_name = profile.get("full_name") or ""
        reply_email = profile.get("email") or ""
        uk_email = profile.get("uk_email")

        if not uk_email:
            await context.update_data(
                appeal_id=appeal_id,
                address=address,
                category=category,
                text=appeal_text,
                full_name=full_name,
                reply_email=reply_email,
            )
            await context.set_state(Form.waiting_uk_email)
            await event.message.answer(
                "В профиле нет email УК. Введите его для отправки "
                f"обращения №{appeal_id}:",
                attachments=[home_kb()],
            )
            await event.ack(notification="Нужен email УК")
            return

        result = await send_appeal_by_email(
            appeal_id=appeal_id,
            address=address,
            category=category,
            text=appeal_text,
            uk_email=uk_email,
            full_name=full_name,
            contact_email=reply_email,
        )
        await set_delivery(
            appeal_id=appeal_id,
            channel="email",
            status=result["status"],
            target=uk_email,
            detail=result.get("detail", ""),
        )

        if result["ok"]:
            await event.message.answer(
                f"✅ *Обращение №{appeal_id} отправлено повторно*\n\n📬 {uk_email}",
                attachments=[appeal_actions(appeal_id)],
            )
        else:
            await event.message.answer(
                f"⚠️ Не удалось отправить: {result['detail']}",
                attachments=[appeal_actions(appeal_id)],
            )
        await event.ack(notification="Готово")
        return

    # ===== НЕИЗВЕСТНЫЙ PAYLOAD =====
    else:
        await event.ack(notification=f"Неизвестный payload: {payload}")


# ===== FSM: РЕДАКТИРОВАНИЕ ПРОФИЛЯ =====
async def _show_profile_after_edit(event: MessageCreated, user_id: int, note: str):
    """Показывает обновлённый профиль после правки."""
    profile = await get_profile(user_id) or {}
    await event.message.answer(
        f"{note}\n\n{profile_text(profile)}",
        attachments=[profile_kb(user_id)],
    )


@router.message_created(Form.editing_name, F.message.body.text)
async def edit_name(event: MessageCreated, context: MemoryContext):
    full_name = event.message.body.text.strip()
    chat_id, user_id = event.get_ids()

    if len(full_name) < 3:
        await event.message.answer(
            "Укажите ФИО целиком — хотя бы имя и фамилию.",
            attachments=[home_kb()],
        )
        return

    await save_profile(user_id, full_name=full_name)
    await context.clear()
    await _show_profile_after_edit(event, user_id, "✅ ФИО сохранено.")


@router.message_created(Form.editing_address, F.message.body.text)
async def edit_address(event: MessageCreated, context: MemoryContext):
    value = " ".join(event.message.body.text.split())
    chat_id, user_id = event.get_ids()

    if value.lower() in ("удалить", "нет", "-"):
        await save_profile(user_id, address="")
        await context.clear()
        await _show_profile_after_edit(event, user_id, "✅ Адрес дома удалён.")
        return

    if len(value) < 5:
        await event.message.answer(
            "Слишком коротко. Укажите улицу, дом и квартиру.\n"
            "Пример: ул. Ленина, д. 5, кв. 12",
            attachments=[home_kb()],
        )
        return

    await save_profile(user_id, address=value)
    await context.clear()
    await _show_profile_after_edit(
        event, user_id, f"✅ Адрес сохранён: {value}"
    )


@router.message_created(Form.editing_email, F.message.body.text)
async def edit_email(event: MessageCreated, context: MemoryContext):
    value = event.message.body.text.strip()
    chat_id, user_id = event.get_ids()

    if value.lower() in ("удалить", "нет", "-"):
        await save_profile(user_id, email="")
        await context.clear()
        await _show_profile_after_edit(event, user_id, "✅ Email для ответов удалён.")
        return

    if not valid_email(value):
        await event.message.answer(
            "Похоже, это не email. Пример: ivanov@example.ru",
            attachments=[home_kb()],
        )
        return

    await save_profile(user_id, email=value)
    await context.clear()
    await _show_profile_after_edit(event, user_id, "✅ Email для ответов обновлён.")


@router.message_created(Form.editing_uk_email, F.message.body.text)
async def edit_uk_email(event: MessageCreated, context: MemoryContext):
    value = event.message.body.text.strip()
    chat_id, user_id = event.get_ids()

    if value.lower() in ("удалить", "нет", "-"):
        await save_profile(user_id, uk_email="")
        await context.clear()
        await _show_profile_after_edit(event, user_id, "✅ Email УК удалён.")
        return

    if not valid_email(value):
        await event.message.answer(
            "Похоже, это не email. Пример: uk@example.ru\n"
            "Где взять: сайт или квитанция вашей управляющей организации.",
            attachments=[home_kb()],
        )
        return

    await save_profile(user_id, uk_email=value)
    await context.clear()
    await _show_profile_after_edit(
        event, user_id,
        f"✅ Email УК обновлён: {value}",
    )


# ===== FSM: ВВОД АДРЕСА =====
@router.message_created(Form.waiting_address, F.message.body.text)
async def process_address(event: MessageCreated, context: MemoryContext):
    address = " ".join(event.message.body.text.split())
    chat_id, user_id = event.get_ids()
    await context.update_data(address=address)
    # Адрес запоминаем в профиле, чтобы не вводить его каждый раз.
    await save_profile(user_id, address=address)
    await context.set_state(Form.waiting_category)
    await event.message.answer(
        "Выберите категорию проблемы:",
        attachments=[categories_kb()],
    )


# ===== FSM: ВВОД ТЕКСТА ОБРАЩЕНИЯ =====
@router.message_created(Form.waiting_text, F.message.body.text)
async def process_text(event: MessageCreated, context: MemoryContext):
    """Текст собран. Сохраняем обращение и уточняем реквизиты отправителя."""
    data = await context.get_data()
    chat_id, user_id = event.get_ids()
    body = event.message.body.text.strip()

    appeal_id = await create_appeal(
        user_id=user_id,
        address=data.get("address", "Не указан"),
        category=CATEGORY_NAMES.get(data.get("category"), "Прочее"),
        text=body,
    )
    await context.update_data(appeal_id=appeal_id, text=body)

    # Реквизиты читаем из базы, а не из контекста: пользователь мог
    # изменить их через «Мои данные» уже после начала подачи.
    profile = await get_profile(user_id) or {}

    if profile.get("full_name"):
        saved_uk_email = (profile.get("uk_email") or "").strip()
        if saved_uk_email:
            note = (
                f"✅ Обращение №{appeal_id} сохранено.\n\n"
                f"В профиле сохранён email УК:\n*{saved_uk_email}*\n\n"
                "Отправлю на него или укажите другой:"
            )
            await event.message.answer(
                note, attachments=[uk_email_kb(saved_uk_email)]
            )
        else:
            await event.message.answer(
                f"✅ Обращение №{appeal_id} сохранено.\n\n"
                "Введите email управляющей организации для отправки.\n"
                "Где взять: сайт или квитанция вашей УК.",
                attachments=[home_kb()],
            )
        await context.set_state(Form.waiting_uk_email)
    else:
        await event.message.answer(
            f"✅ Обращение №{appeal_id} сохранено.\n\n"
            "Для отправки нужны ФИО — так обращение можно идентифицировать "
            "(ст. 7 59-ФЗ).\n\nВведите ФИО:",
            attachments=[home_kb()],
        )
        await context.set_state(Form.waiting_full_name)


# ===== FSM: ФИО =====
@router.message_created(Form.waiting_full_name, F.message.body.text)
async def process_full_name(event: MessageCreated, context: MemoryContext):
    full_name = event.message.body.text.strip()
    data = await context.get_data()
    chat_id, user_id = event.get_ids()

    if len(full_name) < 3:
        await event.message.answer(
            "Укажите ФИО целиком — хотя бы имя и фамилию.",
            attachments=[home_kb()],
        )
        return

    await save_profile(user_id, full_name=full_name)
    await context.update_data(full_name=full_name)

    await event.message.answer(
        "Теперь укажите email, на который придёт ответ УК:",
        attachments=[home_kb()],
    )
    await context.set_state(Form.waiting_reply_email)


# ===== FSM: EMAIL ДЛЯ ОТВЕТА =====
@router.message_created(Form.waiting_reply_email, F.message.body.text)
async def process_reply_email(event: MessageCreated, context: MemoryContext):
    value = event.message.body.text.strip()
    _, user_id = event.get_ids()

    if value.lower() in ("пропустить", "нет", "-"):
        reply_email = None
        prefix = (
            "Без email ответа не будет — УК позвонит или ответит почтой.\n"
        )
    elif not valid_email(value):
        await event.message.answer(
            "Похоже, это не email. Пример: ivanov@example.ru\n"
            "Или напишите «пропустить».",
            attachments=[home_kb()],
        )
        return
    else:
        reply_email = value
        await save_profile(user_id, email=reply_email)
        prefix = ""

    # Дальше в любом случае спрашиваем email УК. Если он уже сохранён,
    # предлагаем кнопку, чтобы не набирать его заново.
    profile = await get_profile(user_id) or {}
    saved_uk_email = (profile.get("uk_email") or "").strip()

    if saved_uk_email:
        await event.message.answer(
            f"{prefix}В профиле сохранён email УК:\n*{saved_uk_email}*\n\n"
            "Отправлю на него или укажите другой:",
            attachments=[uk_email_kb(saved_uk_email)],
        )
    else:
        await event.message.answer(
            f"{prefix}Введите email управляющей организации:",
            attachments=[home_kb()],
        )

    await context.update_data(reply_email=reply_email)
    await context.set_state(Form.waiting_uk_email)


# ===== FSM: EMAIL УК + ОТПРАВКА =====
def _category_name(value: str) -> str:
    """В контексте категория может быть ключом («plumbing») или уже
    названием («Сантехника») — второй случай бывает при повторной отправке.
    """
    if not value:
        return "Прочее"
    return CATEGORY_NAMES.get(value, value)


async def _deliver_from_context(event: MessageCreated, context: MemoryContext,
                                user_id: int, uk_email: str) -> None:
    """Отправляет собранное обращение и показывает результат.

    Общая часть для ввода email УК текстом и для кнопки
    «Использовать сохранённый email УК».
    """
    data = await context.get_data()

    appeal_id = data.get("appeal_id")
    address = data.get("address", "Не указан")
    category = _category_name(data.get("category"))
    text = data.get("text", "")
    full_name = data.get("full_name", "")
    reply_email = data.get("reply_email")

    result = await send_appeal_by_email(
        appeal_id=appeal_id,
        address=address,
        category=category,
        text=text,
        uk_email=uk_email,
        full_name=full_name,
        contact_email=reply_email or "",
    )

    await set_delivery(
        appeal_id=appeal_id,
        channel="email",
        status=result["status"],
        target=uk_email,
        detail=result.get("detail", ""),
    )

    if result["ok"]:
        await context.clear()
        await event.message.answer(
            f"✅ *Обращение №{appeal_id} отправлено*\n\n"
            f"📬 {uk_email}\n"
            f"👤 {full_name}\n"
            f"📍 {address}\n\n"
            f"Ответ смотрите в разделе Ответы УК, "
            "иначе УК свяжется по указанным данным.\n\n"
            "Срок рассмотрения — до 30 дней (ст. 12 59-ФЗ).",
            attachments=[appeal_actions(appeal_id)],
        )
        return

    # Не отправилось — показываем текст письма, чтобы отправить вручную.
    body = build_appeal_body(
        appeal_id, address, category, text, full_name, reply_email or ""
    )
    await context.clear()
    await event.message.answer(
        f"⚠️ *Не удалось отправить: {result['detail']}*\n\n"
        "Отправьте обращение одним из способов:\n\n"
        f"🏛 Мини-приложение «Госуслуги Дом»:\n{GOSUSLUGI_DOM_BOT_URL}\n"
        "🌐 Личный кабинет: dom.gosuslugi.ru\n"
        f"✉️ Написать на {uk_email}\n\n"
        "Текст обращения для копирования:\n"
        f"```\n{body}\n```",
        attachments=[appeal_actions(appeal_id)],
    )


@router.message_created(Form.waiting_uk_email, F.message.body.text)
async def process_uk_email(event: MessageCreated, context: MemoryContext):
    uk_email = event.message.body.text.strip()
    _, user_id = event.get_ids()

    if uk_email.lower() in ("сохранить", "из профиля"):
        profile = await get_profile(user_id) or {}
        uk_email = profile.get("uk_email")
        if not uk_email:
            await event.message.answer(
                "В профиле нет email УК. Введите его, пожалуйста.",
                attachments=[home_kb()],
            )
            return

    if not valid_email(uk_email):
        await event.message.answer(
            "Похоже, это не email. Пример: uk@example.ru\n"
            "Где взять: сайт или квитанция вашей управляющей организации.",
            attachments=[home_kb()],
        )
        return

    await save_profile(user_id, uk_email=uk_email)
    await context.update_data(uk_email=uk_email)
    await _deliver_from_context(event, context, user_id, uk_email)
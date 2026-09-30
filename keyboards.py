from maxapi.utils.inline_keyboard import InlineKeyboardBuilder
from maxapi.types import CallbackButton, LinkButton
from config import GOSUSLUGI_DOM_BOT_URL

HOME_PAYLOAD = "menu:home"


def home_button() -> CallbackButton:
    """Кнопка возврата в главное меню. Добавляется в любую клавиатуру."""
    return CallbackButton(text="🏠 Главная", payload=HOME_PAYLOAD)


def home_kb():
    """Клавиатура только с кнопкой «Главная» — для сообщений без выбора."""
    builder = InlineKeyboardBuilder()
    builder.row(home_button())
    return builder.as_markup()


def _finish(builder: InlineKeyboardBuilder):
    """Собирает клавиатуру и гарантирует наличие кнопки «Главная»."""
    markup = builder.as_markup()
    rows = markup.payload.buttons
    already = any(
        getattr(b, "payload", None) == HOME_PAYLOAD for row in rows for b in row
    )
    if not already:
        # Пустой хвостовой ряд заменяем, а не добавляем после него.
        if rows and not rows[-1]:
            rows[-1:] = [[home_button()]]
        else:
            rows.append([home_button()])
    return markup


def main_menu():
    builder = InlineKeyboardBuilder()
    builder.row(CallbackButton(text="📝 Подать обращение", payload="menu:appeal"))
    builder.row(CallbackButton(text="📋 Мои обращения", payload="menu:my_appeals"))
    builder.row(CallbackButton(text="📬 Ответы УК", payload="menu:replies"))
    builder.row(CallbackButton(text="⚙️ Мои данные", payload="menu:profile"))
    builder.row(CallbackButton(text="🗳 Проверить протокол", payload="menu:vote"))
    builder.row(CallbackButton(text="💰 Начисления и капремонт", payload="menu:charges"))
    return _finish(builder)


def categories_kb():
    builder = InlineKeyboardBuilder()
    builder.row(CallbackButton(text="🚰 Сантехника", payload="cat:plumbing"))
    builder.row(CallbackButton(text="⚡ Электрика", payload="cat:electric"))
    builder.row(CallbackButton(text="🔥 Отопление", payload="cat:heating"))
    builder.row(CallbackButton(text="🧹 Уборка", payload="cat:cleaning"))
    builder.row(CallbackButton(text="🏗 Капремонт", payload="cat:capital"))
    builder.row(CallbackButton(text="📄 Прочее", payload="cat:other"))
    return _finish(builder)


def appeal_actions(appeal_id: int):
    builder = InlineKeyboardBuilder()
    builder.row(CallbackButton(text="🔄 Обновить статус", payload=f"refresh:{appeal_id}"))
    builder.row(CallbackButton(text="✉️ Отправить заново",
                               payload=f"resend:{appeal_id}"))
    builder.row(LinkButton(text="🏛 Отправить через «Госуслуги Дом»",
                           url=f"{GOSUSLUGI_DOM_BOT_URL}?start_press"))
    builder.row(LinkButton(text="🌐 Личный кабинет ГИС ЖКХ",
                           url="https://dom.gosuslugi.ru"))
    builder.row(CallbackButton(text="⚠️ Жалоба в ГЖИ", payload=f"gji:{appeal_id}"))
    return _finish(builder)


def confirm_vote_kb(protocol_id: int):
    builder = InlineKeyboardBuilder()
    builder.row(CallbackButton(text="✅ Подтвердить", payload=f"vote:confirm:{protocol_id}"))
    builder.row(CallbackButton(text="❌ Оспорить", payload=f"vote:dispute:{protocol_id}"))
    return _finish(builder)


def address_kb(saved_address: str = ""):
    """Ввод адреса, когда в профиле уже есть сохранённый.

    Кнопка подставляет его вместо повторного набора текстом. Другой адрес
    вводится текстом — он тут же сохраняется в профиль.
    """
    builder = InlineKeyboardBuilder()
    builder.row(CallbackButton(
        text="🏠 Использовать сохранённый адрес",
        payload="use_saved_address",
    ))
    return _finish(builder)


def uk_email_kb(saved_uk_email: str = ""):
    """Ввод email УК, когда в профиле уже есть сохранённый.

    Кнопка отправляет обращение на него сразу, без повторного набора.
    """
    builder = InlineKeyboardBuilder()
    builder.row(CallbackButton(
        text="🏢 Использовать сохранённый email УК",
        payload="use_saved_uk_email",
    ))
    return _finish(builder)


def inbox_check_kb():
    """Результат ручной проверки ящика.

    Можно повторить проверку или сразу открыть раздел с ответами.
    """
    builder = InlineKeyboardBuilder()
    builder.row(CallbackButton(text="🔍 Проверить почту сейчас",
                               payload="inbox:check"))
    builder.row(CallbackButton(text="📬 Ответы УК", payload="menu:replies"))
    return _finish(builder)


def profile_kb(user_id: int = 0):
    """Экран «Мои данные» с возможностью изменить реквизиты."""
    builder = InlineKeyboardBuilder()
    builder.row(CallbackButton(text="✏️ Изменить ФИО", payload="prof:name"))
    builder.row(CallbackButton(text="🏠 Изменить адрес дома", payload="prof:address"))
    builder.row(CallbackButton(text="📧 Изменить мой email", payload="prof:email"))
    builder.row(CallbackButton(text="🏢 Изменить email УК", payload="prof:uk_email"))
    return _finish(builder)


def replies_kb():
    """Ответы УК: список писем и ручная проверка ящика."""
    builder = InlineKeyboardBuilder()
    builder.row(CallbackButton(text="🔍 Проверить почту сейчас", payload="inbox:check"))
    return _finish(builder)

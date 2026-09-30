from maxapi.context import State, StatesGroup


class Form(StatesGroup):
    # Подача обращения
    waiting_address = State()
    waiting_category = State()
    waiting_text = State()
    # Реквизиты отправителя, обязательные для обращения по 59-ФЗ
    waiting_full_name = State()
    waiting_reply_email = State()
    waiting_uk_email = State()
    # Редактирование профиля
    editing_name = State()
    editing_address = State()
    editing_email = State()
    editing_uk_email = State()
    # Проверка протокола
    waiting_protocol = State()
    # Проверка квитанции
    waiting_receipt = State()

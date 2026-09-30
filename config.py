import os
from dotenv import load_dotenv

load_dotenv()

MAX_BOT_TOKEN = os.getenv("MAX_BOT_TOKEN")

# Путь к файлу базы. По умолчанию — рядом с bot.py, чтобы обычный запуск
# через venv продолжал работать без настроек. В Docker путь задаётся через
# переменную окружения, чтобы база лежала на примонтированном томе.
DB_PATH = os.getenv("DB_PATH", "zhkh.db")

# Исполняемый файл Tesseract. Пусто — искать в PATH. Нужен, если OCR
# установлен нестандартно (в Windows по умолчанию путь не в PATH).
TESSERACT_CMD = os.getenv("TESSERACT_CMD", "")

# РИАС ЖКХ. Оставлено для чтения реестров (договоры, лицевые счета, ОЖФ).
# ВАЖНО: /appeals в этом API — импорт обращений, уже полученных организацией.
# Подать обращение гражданина через него нельзя.
# См. docs/ЖКХ_API.md
RIAS_API_BASE = os.getenv("RIAS_API_BASE", "https://api.rias-gkh.ru/v2.0")
RIAS_API_TOKEN = os.getenv("RIAS_API_TOKEN", "")

# ===== ДОСТАВКА ОБРАЩЕНИЙ =====

# Официальное мини-приложение «Госуслуги Дом» в мессенджере MAX.
GOSUSLUGI_DOM_BOT_URL = os.getenv(
    "GOSUSLUGI_DOM_BOT_URL",
    "https://max.ru/gosuslugi_dom_bot",
)

# Почтовый канал: отправка письменного обращения в УК (ст. 11  ЖК РФ, 59-ФЗ).
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "1") == "1"
SMTP_SENDER_NAME = os.getenv("SMTP_SENDER_NAME", "ЖКХ-бот")

# Реальный почтовый адрес отправителя. Без него письмо невалидно: в From
# должно быть «Имя <адрес>», иначе УК не сможет на него ответить.
# По умолчанию — тот же ящик, что и для входа по SMTP.
SMTP_SENDER_ADDRESS = os.getenv("SMTP_SENDER_ADDRESS", "") or SMTP_USERNAME

# ===== ПРИЁМ ОТВЕТОВ УК =====
# Бот забирает письма из своего ящика и пересылает их в мессенджер MAX.
IMAP_ENABLED = os.getenv("IMAP_ENABLED", "1") == "1"
IMAP_HOST = os.getenv("IMAP_HOST", "")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
IMAP_USERNAME = os.getenv("IMAP_USERNAME", "") or SMTP_USERNAME
IMAP_PASSWORD = os.getenv("IMAP_PASSWORD", "") or SMTP_PASSWORD
# 993 — сквозное шифрование (IMAP4_SSL). 143 — обычный IMAP со STARTTLS.
IMAP_USE_SSL = os.getenv("IMAP_USE_SSL", "1") == "1"
IMAP_MAILBOX = os.getenv("IMAP_MAILBOX", "INBOX")
# Как часто проверять ящик, секунд. APScheduler не любит интервалы меньше 60.
IMAP_POLL_SECONDS = max(60, int(os.getenv("IMAP_POLL_SECONDS", "300")))
# При первом запуске курсор ставится на текущий максимум UID, и уже
# накопленные в ящике письма пропускаются — иначе жителю прилетели бы
# старые письма. Поставьте 0, если нужно разобрать уже лежащую почту.
IMAP_SKIP_EXISTING = os.getenv("IMAP_SKIP_EXISTING", "1") == "1"

# Кому присылать письма, которые не удалось привязать к обращению.
# Пусто — такие письма только записываются в журнал.
IMAP_ADMIN_CHAT_ID = os.getenv("IMAP_ADMIN_CHAT_ID", "")

# Требуется ли ЭП-подпись в теле письма. Без неё обращение в УК
# юридически ничтожно по ст. 7 59-ФЗ (нужна идентификация заявителя),
# но по факту принимается. См. docs/ЖКХ_API.md, раздел «Юридические нюансы».
APPEAL_REQUIRE_SIGNATURE = os.getenv("APPEAL_REQUIRE_SIGNATURE", "0") == "1"

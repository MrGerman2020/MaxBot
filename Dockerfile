# Образ чат-бота ЖКХ в мессенджере MAX.
#
# Собирается из python:3.13-slim. Внутри — только бот: он работает на
# long polling к API MAX и не поднимает ни одного HTTP-сервера, поэтому
# в образе нет ни EXPOSE, ни uvicorn/gunicorn.

FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DB_PATH=/app/data/zhkh.db \
    TESSERACT_CMD=/usr/bin/tesseract \
    TZ=Europe/Moscow

# tesseract + русский языковой пакет — нужен для разбора фото протокола
# и квитанции. tzdata — чтобы отметки времени в логах были по Москве.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-rus \
        tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Зависимости отдельным слоем: код меняется часто, пакеты — нет.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Непривилегированный пользователь: боту не нужны права root.
RUN useradd --create-home --uid 10001 botuser \
 && mkdir -p /app/data \
 && chown -R botuser:botuser /app

USER botuser

# Бот не слушает портов. Проверка живости идёт по файлу базы:
# init_db() создаёт его при старте, поэтому «файла нет» = «бот не поднялся».
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import os,sqlite3,sys; p=os.environ['DB_PATH']; sys.exit(0 if os.path.exists(p) and sqlite3.connect(p).execute('PRAGMA quick_check').fetchone()[0]=='ok' else 1)"

CMD ["python", "bot.py"]

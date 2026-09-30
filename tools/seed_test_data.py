"""Подложить тестовое обращение напрямую в базу.

Нужен, когда проверять приём ответа УК не хочется проходить всю подачу
заново. Работает и на Windows, и в Linux, и внутри контейнера:

    python tools/seed_test_data.py --user-id 123456789
    docker compose exec bot python tools/seed_test_data.py --user-id 123456789

Скрипт только добавляет и удаляет СВОИ строки (их текст помечен словом
TEST-MARKER). Существующие обращения жителей не трогаются.
"""

import argparse
import os
import sqlite3
import sys

# На Windows консоль по умолчанию cp1251, и эмодзи в выводе роняют скрипт
# с UnicodeEncodeError. Переключаем вывод на UTF-8; если терминал всё равно
# не умеет показывать символ, errors="replace" печатает «?» вместо падения.
for _stream in ("stdout", "stderr"):
    _s = getattr(sys, _stream, None)
    if _s is not None and hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

# Путь к базе берём из окружения — так же, как это делает config.py,
# чтобы скрипт находил файл и на хосте, и внутри контейнера.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
from config import DB_PATH  # noqa: E402

TEST_MARKER = "TEST-MARKER"
TEST_TEXT = f"{TEST_MARKER} тестовое обращение"


def resolve_db_path(explicit):
    """Находит файл базы.

    В контейнере DB_PATH абсолютный (/app/data/zhkh.db) и вопросов нет.
    При запуске из venv он относительный («zhkh.db») и считается от
    текущего каталога — а скрипт могут вызвать не из корня проекта.
    Поэтому при неудаче пробуем корень проекта.
    """
    if explicit:
        return explicit
    candidate = DB_PATH
    if os.path.exists(candidate):
        return candidate
    if not os.path.isabs(candidate):
        from_root = os.path.join(PROJECT_ROOT, candidate)
        if os.path.exists(from_root):
            return from_root
    return candidate


def parse_args():
    p = argparse.ArgumentParser(
        description="Создать тестовое обращение в базе бота.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "После создания отправьте письмо на --uk-email с темой\n"
            "'Обращение № <номер>'. Бот подберёт обращение по номеру\n"
            "в теме и вернёт ответ в чат."
        ),
    )
    p.add_argument(
        "--user-id", type=int,
        help="Ваш user_id в MAX — он же привязывает ответ к обращению"
             " (не нужен с --clean)",
    )
    p.add_argument("--address", default="ул. Мира, д. 1, кв. 5",
                   help="адрес объекта (по умолчанию: %(default)s)")
    p.add_argument("--category", default="Сантехника",
                   help="категория обращения (по умолчанию: %(default)s)")
    p.add_argument("--full-name", default="Иванов Иван Иванович",
                   help="ФИО заявителя (по умолчанию: %(default)s)")
    p.add_argument("--contact-email", default="ivanov@example.com",
                   help="почта заявителя (по умолчанию: %(default)s)")
    p.add_argument("--uk-email",
                   help="email УК — именно на него нужно ответить"
                        " (не нужен с --clean)")
    p.add_argument("--reset", action="store_true",
                   help="перед созданием удалить ранее созданные тестовые строки")
    p.add_argument("--clean", action="store_true",
                   help="только удалить тестовые строки, ничего не создавать")
    p.add_argument("--db", default=None,
                   help="путь к файлу базы (по умолчанию берётся из DB_PATH)")
    args = p.parse_args()
    if not args.clean:
        if not args.uk_email:
            p.error("--uk-email обязателен (или используйте --clean)")
        if args.user_id is None:
            p.error("--user-id обязателен (или используйте --clean)")
    return args


def purge_test_rows(db):
    """Удаляет только строки этого скрипта. Чужие данные не трогает."""
    cur = db.execute(
        "DELETE FROM deliveries WHERE appeal_id IN "
        "(SELECT id FROM appeals WHERE text = ?)",
        (TEST_TEXT,),
    )
    removed_deliveries = cur.rowcount
    cur = db.execute("DELETE FROM appeals WHERE text = ?", (TEST_TEXT,))
    removed = cur.rowcount
    db.commit()
    return removed, removed_deliveries


def main():
    args = parse_args()
    db_path = resolve_db_path(args.db)

    if not os.path.exists(db_path):
        sys.exit(
            f"Файл базы не найден: {db_path}\n"
            "Запустите бота хотя бы раз — он создаст базу при старте."
        )

    db = sqlite3.connect(db_path)
    try:
        if args.reset or args.clean:
            removed, removed_deliveries = purge_test_rows(db)
            print(f"Удалено тестовых обращений: {removed}, "
                  f"записей об отправке: {removed_deliveries}")

        if args.clean:
            print("Режим --clean: ничего не создано.")
            return

        db.execute(
            """
            INSERT INTO appeals
                (user_id, address, category, text, status, full_name,
                 contact_email, uk_email, delivery_channel,
                 delivery_status, delivery_detail)
            VALUES (?, ?, ?, ?, 'new', ?, ?, ?, 'email', 'sent', ?)
            """,
            (
                args.user_id,
                args.address,
                args.category,
                TEST_TEXT,
                args.full_name,
                args.contact_email,
                args.uk_email,
                "подложено скриптом tools/seed_test_data.py",
            ),
        )
        appeal_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.commit()
    finally:
        db.close()

    print()
    print(f"Создано обращение №{appeal_id}")
    print(f"  user_id:  {args.user_id}")
    print(f"  адрес:    {args.address}")
    print(f"  категория:{args.category}")
    print(f"  email УК: {args.uk_email}")
    print()
    print("Что делать дальше:")
    print(f"  1. Отправьте письмо на {args.uk_email}")
    print(f'     с темой "Обращение № {appeal_id}"')
    print("  2. В MAX нажмите «🔍 Проверить почту сейчас»")
    print('  3. Бот вернёт "📬 Ответ по обращению '
          f'№{appeal_id}" в чат')
    print()
    print("Убрать тестовые данные:")
    print(f"  {sys.executable} {sys.argv[0]} --clean")


if __name__ == "__main__":
    main()

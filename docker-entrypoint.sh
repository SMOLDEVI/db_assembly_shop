#!/bin/bash
set -e

# Инициализация БД, если отсутствует
if [ ! -f "database.db" ]; then
    echo "database.db не найден. Запуск init_db.py..."
    python init_db.py
    echo "База данных инициализирована."
fi

# Создание папки для сгенерированных документов
mkdir -p generated_docs

exec "$@"

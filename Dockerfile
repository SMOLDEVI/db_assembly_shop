# Использование легковесного официального образа Python
# Использование легковесного официального образа Python
FROM python:3.10-slim

# Установка рабочей директории в контейнере
WORKDIR /app

# Установка зависимостей проекта
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование исходного кода проекта
COPY . .

# Создание папки для сгенерированных документов
RUN mkdir -p generated_docs

# Порт, на котором работает приложение Flask
EXPOSE 5000

# Входная точка: инициализация БД при необходимости
COPY docker-entrypoint.sh /
RUN chmod +x /docker-entrypoint.sh
ENTRYPOINT ["/docker-entrypoint.sh"]

# Запуск веб-сервера
CMD ["python", "app.py"]

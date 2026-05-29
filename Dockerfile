# Использование легковесного официального образа Python
FROM python:3.10-slim

# Установка рабочей директории в контейнере
WORKDIR /app

# Установка зависимостей проекта
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование исходного кода проекта
COPY . .

# Сброс и создание базы данных при сборке
RUN python init_db.py

# Порт, на котором работает приложение Flask
EXPOSE 5000

# Запуск веб-сервера
CMD ["python", "app.py"]

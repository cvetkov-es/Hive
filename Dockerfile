# Улей в контейнере — для стенда. Один процесс отдаёт и API, и собранный
# интерфейс (web/dist закоммичен, Node не нужен). Без Docker работает python3 run.py.
#
#   docker build -t hive .
#   docker run --rm -p 8000:8000 hive      # http://127.0.0.1:8000
#
# Стенд в интернете — через docker-compose.yml: там перед Ульем стоит Caddy
# с сертификатом, а сам контейнер наружу не открыт.
FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

# Зависимости отдельным слоем: правка кода не переустанавливает OR-Tools.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Не от root: сервис открыт в интернет, и ошибка в нём не должна давать прав
# на систему контейнера. Писать процессу нужно только в runs/ и во временную
# папку.
RUN useradd --create-home --uid 10001 hive
COPY . .
RUN mkdir -p runs && chown hive:hive runs
USER hive

WORKDIR /app/backend
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

ENV DJANGO_SETTINGS_MODULE=flash_sale.settings

RUN python manage.py collectstatic --noinput 2>/dev/null || true

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]

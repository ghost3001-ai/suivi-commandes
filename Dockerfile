FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV FLASK_APP=app.py
ENV PYTHONUNBUFFERED=1

EXPOSE 5000

CMD ["sh", "-c", "PORT=${PORT:-5000}; python init_app.py && gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 4 --worker-class sync --timeout 60"]

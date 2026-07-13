FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY config.py .
COPY analysis/ analysis/
COPY backend/ backend/
COPY data/geo/ data/geo/
COPY data/processed/ data/processed/

ENV FLASK_APP=backend.app:app
EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--chdir", "/app", "backend.app:app"]

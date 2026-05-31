# Base Python hyper stable et légère
FROM python:3.11-slim

# On se place dans le dossier de travail
WORKDIR /app

# Copie et installation des dépendances
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copie du reste du code (dont app.py)
COPY . .

# Gunicorn avec timeout long — le cron sync dure 8-15 minutes
CMD gunicorn --bind 0.0.0.0:${PORT:-8080} --workers 1 --timeout 1800 cron_sync:app
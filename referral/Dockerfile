# Base Python hyper stable et légère
FROM python:3.11-slim

# On se place dans le dossier de travail
WORKDIR /app

# Copie et installation des dépendances
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copie du reste du code (dont app.py)
COPY . .

# Commande de démarrage ultra-rapide pour Streamlit sur Cloud Run
CMD streamlit run app.py \
    --server.port=${PORT:-8080} \
    --server.address=0.0.0.0 \
    --server.enableCORS=false \
    --browser.gatherUsageStats=false
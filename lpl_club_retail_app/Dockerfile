# On utilise toujours notre version stable
FROM python:3.11-slim

# On se place dans le dossier de travail
WORKDIR /app

# On copie et installe les requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# On copie notre fichier app.py
COPY . .

# La commande magique pour Streamlit sur Cloud Run :
# On lui dit de ne pas ouvrir de navigateur, de couper les stats, et d'écouter le port de Google
CMD streamlit run app.py \
    --server.port=${PORT:-8080} \
    --server.address=0.0.0.0 \
    --server.enableCORS=false \
    --browser.gatherUsageStats=false
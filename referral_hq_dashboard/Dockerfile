FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Commande optimisée pour Streamlit sur Cloud Run
CMD streamlit run hq_dashboard.py \
    --server.port=${PORT:-8080} \
    --server.address=0.0.0.0 \
    --server.enableCORS=false \
    --browser.gatherUsageStats=false
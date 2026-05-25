FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

# 👇 La modification est ici : on lui donne ton vrai nom de fichier
CMD streamlit run lpl_club_dashboard.py --server.port=8080 --server.address=0.0.0.0 --server.enableCORS=false --browser.gatherUsageStats=false
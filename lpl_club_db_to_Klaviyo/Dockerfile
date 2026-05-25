# On force l'utilisation de la version super stable Python 3.11
FROM python:3.11-slim

# On se place dans le dossier de travail du serveur
WORKDIR /app

# On copie d'abord les requirements
COPY requirements.txt .

# On installe les librairies proprement
RUN pip install --no-cache-dir -r requirements.txt

# On copie le reste de nos fichiers (le main.py)
COPY . .

# On démarre le serveur avec Gunicorn
CMD exec gunicorn --bind :${PORT:-8080} --workers 1 --threads 8 --timeout 0 main:app
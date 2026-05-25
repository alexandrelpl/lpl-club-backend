FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# On lance Flask directement
CMD ["python", "client_portal_shopify.py"]
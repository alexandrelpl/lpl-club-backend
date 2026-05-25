import os
import time
import requests
import logging
from flask import Flask
from google.cloud import bigquery

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Ton API Key privée Klaviyo (à mettre dans Google Cloud)
KLAVIYO_API_KEY = os.environ.get("KLAVIYO_API_KEY")
HEADERS = {
    "Authorization": f"Klaviyo-API-Key {KLAVIYO_API_KEY}",
    "accept": "application/json",
    "revision": "2024-02-15", # Version de l'API Klaviyo
    "content-type": "application/json"
}

app = Flask(__name__)

def get_klaviyo_profile(email):
    """Cherche si le client existe dans Klaviyo"""
    url = f"https://a.klaviyo.com/api/profiles/?filter=equals(email,\"{email}\")"
    response = requests.get(url, headers=HEADERS)
    
    if response.status_code == 429: # Rate limit
        time.sleep(2)
        return get_klaviyo_profile(email)
        
    if response.status_code == 200:
        data = response.json()
        if data.get("data"):
            return data["data"][0]["id"]
    return None

def update_klaviyo_profile(profile_id, is_active, expiry_date):
    """Met à jour un profil existant"""
    url = f"https://a.klaviyo.com/api/profiles/{profile_id}/"
    payload = {
        "data": {
            "type": "profile",
            "id": profile_id,
            "attributes": {
                "properties": {
                    "is_lpl_club": is_active,
                    "lpl_club_expiry_date": str(expiry_date) if expiry_date else None
                }
            }
        }
    }
    response = requests.patch(url, json=payload, headers=HEADERS)
    return response.status_code in [200, 204]

def create_klaviyo_profile(email, is_active, expiry_date):
    """Crée un profil Klaviyo (Ex: Client Retail qui n'a jamais commandé sur le web ni reçu d'email)"""
    url = "https://a.klaviyo.com/api/profiles/"
    payload = {
        "data": {
            "type": "profile",
            "attributes": {
                "email": email,
                "properties": {
                    "is_lpl_club": is_active,
                    "lpl_club_expiry_date": str(expiry_date) if expiry_date else None,
                    "source": "LPL_Club_Retail_Import"
                }
            }
        }
    }
    response = requests.post(url, json=payload, headers=HEADERS)
    return response.status_code == 201

def run_sync():
    logging.info("🚀 Démarrage de la synchronisation BQ -> Klaviyo")
    bq_client = bigquery.Client()

    query = """
    SELECT email, is_lpl_club, CAST(lpl_club_expiry_date AS STRING) AS expiry_date
    FROM `shopify-data-ltv.shopify_data_eu.dim_unified_customers`
    WHERE last_club_order_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 3 DAY)
       OR lpl_club_expiry_date = CURRENT_DATE()
       OR lpl_club_expiry_date = DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY)
    """
    
    results = bq_client.query(query).result()
    count_success, count_created = 0, 0

    for row in results:
        if not row.email: continue
        
        profile_id = get_klaviyo_profile(row.email)
        
        if profile_id:
            if update_klaviyo_profile(profile_id, row.is_lpl_club, row.expiry_date):
                count_success += 1
                logging.info(f"✔️ {row.email} mis à jour dans Klaviyo.")
        else:
            if create_klaviyo_profile(row.email, row.is_lpl_club, row.expiry_date):
                count_created += 1
                logging.info(f"✨ {row.email} créé dans Klaviyo.")

    logging.info(f"🏁 Sync terminée : {count_success} mis à jour, {count_created} créés.")

@app.route("/", methods=["POST", "GET"])
def trigger_sync():
    run_sync()
    return "Sync finished", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
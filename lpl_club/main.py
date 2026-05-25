import os
import time
import requests
import logging
from flask import Flask
from google.cloud import bigquery

# --- Configuration du Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Variables d'environnement ---
SHOPIFY_STORE_DOMAIN = os.environ.get("SHOPIFY_STORE_DOMAIN", "le-petit-lunetier.myshopify.com")
SHOPIFY_ACCESS_TOKEN = os.environ.get("SHOPIFY_ACCESS_TOKEN")
SHOPIFY_API_VERSION = "2025-04"

app = Flask(__name__)

def shopify_graphql(query, variables=None):
    """Appel générique à l'API GraphQL Shopify avec gestion Rate Limit"""
    url = f"https://{SHOPIFY_STORE_DOMAIN}/admin/api/{SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN
    }
    payload = {"query": query}
    if variables:
        payload["variables"] = variables

    response = requests.post(url, json=payload, headers=headers)
    data = response.json()

    if "extensions" in data and "cost" in data["extensions"]:
        throttle_status = data["extensions"]["cost"]["throttleStatus"]
        currently_available = throttle_status["currentlyAvailable"]
        if currently_available < 200:
            logging.warning(f"⚠️ API Shopify ralentie (Points: {currently_available}). Pause de 2s...")
            time.sleep(2)

    if "errors" in data:
        logging.error(f"❌ Erreur GraphQL : {data['errors']}")
        return None

    return data.get("data")

def get_shopify_customer_id(email):
    """Cherche si le client existe déjà"""
    query = """
    query getCustomerByEmail($query: String!) {
      customers(first: 1, query: $query) {
        edges {
          node {
            id
          }
        }
      }
    }
    """
    variables = {"query": f"email:{email}"}
    result = shopify_graphql(query, variables)
    
    if result and result.get("customers", {}).get("edges"):
        return result["customers"]["edges"][0]["node"]["id"]
    return None

def create_shopify_customer(email, is_active, expiry_date):
    """Création proactive d'un client Retail sur Shopify avec ses Metafields VIP"""
    mutation = """
    mutation customerCreate($input: CustomerInput!) {
      customerCreate(input: $input) {
        customer {
          id
        }
        userErrors {
          field
          message
        }
      }
    }
    """
    
    metafields = [
        {
            "namespace": "lpl_club",
            "key": "active",
            "type": "boolean",
            "value": "true" if is_active else "false"
        }
    ]
    
    if is_active and expiry_date:
        metafields.append({
            "namespace": "lpl_club",
            "key": "expiry_date",
            "type": "date",
            "value": str(expiry_date)
        })

    variables = {
        "input": {
            "email": email,
            "tags": ["LPL_Club_Retail_Import"], # Un tag pratique pour toi pour les repérer
            "metafields": metafields
        }
    }

    result = shopify_graphql(mutation, variables)
    
    if result and result.get("customerCreate", {}).get("userErrors"):
        errors = result["customerCreate"]["userErrors"]
        if errors:
            logging.error(f"❌ Erreur création client {email}: {errors}")
            return None

    if result and result.get("customerCreate", {}).get("customer"):
        return result["customerCreate"]["customer"]["id"]
        
    return None

def update_customer_metafields(customer_id, is_active, expiry_date):
    """Met à jour un client existant"""
    mutation = """
    mutation metafieldsSet($metafields: [MetafieldsSetInput!]!) {
      metafieldsSet(metafields: $metafields) {
        metafields {
          id
        }
        userErrors {
          field
          message
        }
      }
    }
    """
    
    metafields = [
        {
            "ownerId": customer_id,
            "namespace": "lpl_club",
            "key": "active",
            "type": "boolean",
            "value": "true" if is_active else "false"
        }
    ]
    
    if is_active and expiry_date:
        metafields.append({
            "ownerId": customer_id,
            "namespace": "lpl_club",
            "key": "expiry_date",
            "type": "date",
            "value": str(expiry_date)
        })

    variables = {"metafields": metafields}
    result = shopify_graphql(mutation, variables)
    
    if result and result.get("metafieldsSet", {}).get("userErrors"):
        errors = result["metafieldsSet"]["userErrors"]
        if errors:
            logging.error(f"❌ Erreur Metafield pour {customer_id}: {errors}")
            return False
    return True

def run_sync():
    """Logique principale"""
    logging.info("🚀 Démarrage de la synchronisation BQ -> Shopify (Omnicanal Proactif)")

    bq_client = bigquery.Client()

    query = """
    SELECT 
        email, 
        is_lpl_club, 
        CAST(lpl_club_expiry_date AS STRING) AS expiry_date
    FROM `shopify-data-ltv.shopify_data_eu.dim_unified_customers`
    WHERE last_club_order_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 3 DAY)
       OR lpl_club_expiry_date = CURRENT_DATE()
       OR lpl_club_expiry_date = DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY)
    """
    
    logging.info("📊 Exécution de la requête BigQuery...")
    query_job = bq_client.query(query)
    results = query_job.result()
    
    count_success = 0
    count_created = 0
    count_errors = 0

    for row in results:
        email = row.email
        is_active = row.is_lpl_club
        expiry_date = row.expiry_date

        if not email:
            continue

        customer_id = get_shopify_customer_id(email)
        
        # --- NOUVEAUTÉ : CRÉATION DU CLIENT S'IL N'EXISTE PAS ---
        if not customer_id:
            logging.info(f"🆕 Client introuvable : Création du profil Retail pour {email}...")
            new_id = create_shopify_customer(email, is_active, expiry_date)
            
            if new_id:
                status_text = "VIP ✅" if is_active else "EXPIRÉ ❌"
                logging.info(f"✨ Nouveau profil créé avec succès : {email} ({status_text} jusqu'au {expiry_date})")
                count_created += 1
            else:
                count_errors += 1
            continue

        # --- MISE À JOUR DU CLIENT S'IL EXISTE DÉJÀ ---
        success = update_customer_metafields(customer_id, is_active, expiry_date)
        
        if success:
            status_text = "VIP ✅" if is_active else "EXPIRÉ ❌"
            logging.info(f"✔️ {email} mis à jour ({status_text} jusqu'au {expiry_date})")
            count_success += 1
        else:
            count_errors += 1

    logging.info(f"🏁 Synchronisation terminée ! {count_success} mis à jour, {count_created} nouveaux profils créés, {count_errors} erreurs.")

@app.route("/", methods=["POST", "GET"])
def trigger_sync():
    run_sync()
    return "Sync finished", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
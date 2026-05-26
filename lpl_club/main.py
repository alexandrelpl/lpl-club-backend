import os
import time
import requests
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask
from google.cloud import bigquery

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

SHOPIFY_STORE_DOMAIN = os.environ.get("SHOPIFY_STORE_DOMAIN", "le-petit-lunetier.myshopify.com")
SHOPIFY_ACCESS_TOKEN = os.environ.get("SHOPIFY_ACCESS_TOKEN")
SHOPIFY_API_VERSION = "2025-04"

app = Flask(__name__)

def shopify_graphql(query, variables=None):
    url = f"https://{SHOPIFY_STORE_DOMAIN}/admin/api/{SHOPIFY_API_VERSION}/graphql.json"
    headers = {"Content-Type": "application/json", "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN}
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    response = requests.post(url, json=payload, headers=headers, timeout=30)
    data = response.json()
    if "extensions" in data and "cost" in data["extensions"]:
        avail = data["extensions"]["cost"]["throttleStatus"]["currentlyAvailable"]
        if avail < 200:
            logging.warning(f"⚠️ API Shopify ralentie (Points: {avail}). Pause 2s...")
            time.sleep(2)
    if "errors" in data:
        logging.error(f"❌ Erreur GraphQL : {data['errors']}")
        return None
    return data.get("data")

def get_shopify_customer_id(email):
    query = """
    query getCustomerByEmail($query: String!) {
      customers(first: 1, query: $query) {
        edges { node { id } }
      }
    }"""
    result = shopify_graphql(query, {"query": f"email:{email}"})
    if result and result.get("customers", {}).get("edges"):
        return result["customers"]["edges"][0]["node"]["id"]
    return None

def create_shopify_customer(email, is_active, expiry_date):
    mutation = """
    mutation customerCreate($input: CustomerInput!) {
      customerCreate(input: $input) {
        customer { id }
        userErrors { field message }
      }
    }"""
    metafields = [{"namespace": "lpl_club", "key": "active", "type": "boolean",
                   "value": "true" if is_active else "false"}]
    if is_active and expiry_date:
        metafields.append({"namespace": "lpl_club", "key": "expiry_date",
                           "type": "date", "value": str(expiry_date)})
    variables = {"input": {"email": email, "tags": ["LPL_Club_Retail_Import"], "metafields": metafields}}
    result = shopify_graphql(mutation, variables)
    if result and result.get("customerCreate", {}).get("userErrors"):
        errors = result["customerCreate"]["userErrors"]
        if errors:
            logging.error(f"❌ Erreur création {email}: {errors}")
            return None
    if result and result.get("customerCreate", {}).get("customer"):
        return result["customerCreate"]["customer"]["id"]
    return None

def update_customer_metafields(customer_id, is_active, expiry_date):
    mutation = """
    mutation metafieldsSet($metafields: [MetafieldsSetInput!]!) {
      metafieldsSet(metafields: $metafields) {
        metafields { id }
        userErrors { field message }
      }
    }"""
    metafields = [{"ownerId": customer_id, "namespace": "lpl_club", "key": "active",
                   "type": "boolean", "value": "true" if is_active else "false"}]
    if is_active and expiry_date:
        metafields.append({"ownerId": customer_id, "namespace": "lpl_club",
                           "key": "expiry_date", "type": "date", "value": str(expiry_date)})
    result = shopify_graphql(mutation, {"metafields": metafields})
    if result and result.get("metafieldsSet", {}).get("userErrors"):
        errors = result["metafieldsSet"]["userErrors"]
        if errors:
            logging.error(f"❌ Erreur Metafield {customer_id}: {errors}")
            return False
    return True

def process_one_customer(email, is_active, expiry_date):
    """Traite un client. Thread-safe."""
    try:
        if not email:
            return "skip"
        customer_id = get_shopify_customer_id(email)
        if not customer_id:
            new_id = create_shopify_customer(email, is_active, expiry_date)
            if new_id:
                label = "VIP ✅" if is_active else "EXPIRÉ ❌"
                logging.info(f"✨ Créé : {email} ({label} jusqu'au {expiry_date})")
                return "created"
            return "error"
        else:
            success = update_customer_metafields(customer_id, is_active, expiry_date)
            if success:
                label = "VIP ✅" if is_active else "EXPIRÉ ❌"
                logging.info(f"✔️ {email} mis à jour ({label} jusqu'au {expiry_date})")
                return "updated"
            return "error"
    except Exception as e:
        logging.error(f"❌ Exception {email}: {e}")
        return "error"

def run_sync():
    logging.info("🚀 Démarrage sync BQ -> Shopify (5 workers parallèles)")
    bq_client = bigquery.Client()
    query = """
    SELECT email, is_lpl_club, CAST(lpl_club_expiry_date AS STRING) AS expiry_date
    FROM `shopify-data-ltv.shopify_data_eu.dim_unified_customers`
    WHERE last_club_order_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 3 DAY)
       OR lpl_club_expiry_date = CURRENT_DATE()
       OR lpl_club_expiry_date = DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY)
    """
    logging.info("📊 Requête BigQuery...")
    rows = list(bq_client.query(query).result())
    logging.info(f"📋 {len(rows)} clients à synchroniser.")
    count_updated = count_created = count_errors = 0
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(process_one_customer, r.email, r.is_lpl_club, r.expiry_date): r.email
            for r in rows
        }
        for future in as_completed(futures):
            res = future.result()
            if res == "created": count_created += 1
            elif res == "updated": count_updated += 1
            elif res == "error": count_errors += 1
    logging.info(f"🏁 Terminé ! {count_updated} mis à jour, {count_created} créés, {count_errors} erreurs.")

@app.route("/", methods=["POST", "GET"])
def trigger_sync():
    run_sync()
    return "Sync finished", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)


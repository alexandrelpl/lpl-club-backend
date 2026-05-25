import os
import random
import string
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import requests
import toml
from flask import Flask, request, jsonify
from google.cloud import bigquery

app = Flask(__name__)
PROJECT_ID = "shopify-data-ltv"

# --- CHARGEMENT DES SECRETS ---
try:
    secrets = toml.load(".streamlit/secrets.toml")
    SHOPIFY_STORE = secrets["shopify"]["store_url"]
    SHOPIFY_TOKEN = secrets["shopify"]["access_token"]
    CRON_SECRET = secrets["maj_base"]["cron_secret"]
    KLAVIYO_API_KEY = secrets["klaviyo"]["api_key"]
except Exception as e:
    print(f"Erreur secrets: {e}", flush=True)
    SHOPIFY_STORE = os.environ.get("SHOPIFY_STORE", "")
    SHOPIFY_TOKEN = os.environ.get("SHOPIFY_TOKEN", "")
    CRON_SECRET = os.environ.get("CRON_SECRET", "LPL_CRON_SUPER_SECRET_2024!")
    KLAVIYO_API_KEY = os.environ.get("KLAVIYO_API_KEY", "")

client = bigquery.Client(project=PROJECT_ID)

def run_shopify_graphql(query, variables=None):
    headers = {"X-Shopify-Access-Token": SHOPIFY_TOKEN, "Content-Type": "application/json"}
    url = f"https://{SHOPIFY_STORE}/admin/api/2025-04/graphql.json"
    res = requests.post(url, json={"query": query, "variables": variables or {}}, headers=headers)
    return res.json() if res.status_code == 200 else None

def generate_code(prefix):
    return f"{prefix}-{''.join(random.choices(string.ascii_uppercase + string.digits, k=4))}"

def push_to_klaviyo(email, referral_code):
    url = "https://a.klaviyo.com/api/profile-import/"
    headers = {
        "Authorization": f"Klaviyo-API-Key {KLAVIYO_API_KEY}",
        "accept": "application/json",
        "revision": "2024-02-15",
        "content-type": "application/json"
    }
    payload = {
        "data": {
            "type": "profile",
            "attributes": {
                "email": email,
                "properties": { "LPL_Referral_Code": referral_code }
            }
        }
    }
    res = requests.post(url, json=payload, headers=headers)
    return res.status_code in [200, 201, 202]

@app.route('/api/sync-klaviyo-codes', methods=['POST'])
def sync_klaviyo_codes():
    if request.headers.get('X-Cron-Secret') != CRON_SECRET:
        return jsonify({"error": "Unauthorized"}), 403

    print("🚀 Début de la synchro Klaviyo...", flush=True)
    
    # REQUÊTE INTELLIGENTE : Prends les acheteurs des 2 derniers jours
    # MAIS exclut ceux dont le code a déjà été prolongé/créé aujourd'hui (expiration > 360 jours)
    query_recent_buyers = f"""
        SELECT t1.email 
        FROM `{PROJECT_ID}.shopify_data_eu.vw_unified_customer_last_order` t1
        LEFT JOIN `{PROJECT_ID}.shopify_data_eu.referral_codes` t2
          ON t1.email = t2.owner_email AND t2.code LIKE 'LPL-%'
        WHERE t1.absolute_last_order_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 2 DAY)
          AND t1.email IS NOT NULL AND t1.email != ''
          AND (t2.expires_at IS NULL OR t2.expires_at < TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL 360 DAY))
        LIMIT 100
    """
    buyers = list(client.query(query_recent_buyers))
    total_buyers = len(buyers)
    
    if total_buyers == 0:
        print("✅ Aucun acheteur récent à traiter.", flush=True)
        return jsonify({"status": "success", "message": "Aucun acheteur récent."}), 200

    print(f"📊 {total_buyers} clients à traiter pour ce lot...", flush=True)
    success_count = 0
    
    for idx, row in enumerate(buyers, 1):
        email = row.email.lower().strip()
        print(f"⏳ [{idx}/{total_buyers}] Traitement de {email}...", flush=True)
        
        q_code = f"SELECT code, shopify_rule_id, expires_at FROM `{PROJECT_ID}.shopify_data_eu.referral_codes` WHERE owner_email = @e AND code LIKE 'LPL-%' LIMIT 1"
        code_rows = list(client.query(q_code, job_config=bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("e", "STRING", email)])))
        
        new_exp_date = datetime.now(ZoneInfo("Europe/Paris")) + timedelta(days=365)
        new_exp_iso = new_exp_date.isoformat()
        final_code = None
        
        if not code_rows:
            final_code = generate_code("LPL")
            mutation = """
            mutation discountCodeBasicCreate($basicCodeDiscount: DiscountCodeBasicInput!) {
              discountCodeBasicCreate(basicCodeDiscount: $basicCodeDiscount) {
                codeDiscountNode { id }
              }
            }
            """
            variables = {
                "basicCodeDiscount": {
                    "title": final_code, "code": final_code, "startsAt": datetime.now().isoformat(), "endsAt": new_exp_iso, "usageLimit": 5,
                    "customerSelection": { "all": True },
                    "customerGets": { "value": { "discountAmount": { "amount": 10.0, "appliesOnEachItem": False } }, "items": { "all": True } },
                    "minimumRequirement": { "subtotal": { "greaterThanOrEqualToSubtotal": 49.0 } },
                    "combinesWith": { "productDiscounts": True, "shippingDiscounts": True }
                }
            }
            res = run_shopify_graphql(mutation, variables)
            rule_id = res.get("data", {}).get("discountCodeBasicCreate", {}).get("codeDiscountNode", {}).get("id") if res else None
            
            if rule_id:
                client.query(f"INSERT INTO `{PROJECT_ID}.shopify_data_eu.referral_codes` (code, owner_email, created_at, expires_at, status, shopify_rule_id, reward_value, usage_count, max_usage) VALUES ('{final_code}', '{email}', CURRENT_TIMESTAMP(), '{new_exp_date.strftime('%Y-%m-%d %H:%M:%S')}', 'ACTIVE', '{rule_id}', 10.0, 0, 5)").result()
        else:
            existing = code_rows[0]
            final_code = existing.code
            rule_id = existing.shopify_rule_id
            
            if rule_id:
                mutation_update = """
                mutation discountCodeBasicUpdate($id: ID!, $basicCodeDiscount: DiscountCodeBasicInput!) {
                  discountCodeBasicUpdate(id: $id, basicCodeDiscount: $basicCodeDiscount) {
                    codeDiscountNode { id }
                  }
                }
                """
                var_update = { "id": rule_id, "basicCodeDiscount": { "endsAt": new_exp_iso } }
                run_shopify_graphql(mutation_update, var_update)
                client.query(f"UPDATE `{PROJECT_ID}.shopify_data_eu.referral_codes` SET expires_at = '{new_exp_date.strftime('%Y-%m-%d %H:%M:%S')}' WHERE code = '{final_code}'").result()

        if final_code:
            if push_to_klaviyo(email, final_code):
                success_count += 1
                
    print(f"🏁 Terminé ! {success_count} profils Klaviyo mis à jour.", flush=True)
    return jsonify({"status": "success", "klaviyo_profiles_updated": success_count}), 200

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
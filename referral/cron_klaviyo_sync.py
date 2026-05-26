import os
import random
import string
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import requests
import toml
from flask import Flask, request, jsonify
from google.cloud import bigquery

app = Flask(__name__)
PROJECT_ID = "shopify-data-ltv"

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

bq_client = bigquery.Client(project=PROJECT_ID)

def run_shopify_graphql(query, variables=None):
    headers = {"X-Shopify-Access-Token": SHOPIFY_TOKEN, "Content-Type": "application/json"}
    url = f"https://{SHOPIFY_STORE}/admin/api/2025-04/graphql.json"
    res = requests.post(url, json={"query": query, "variables": variables or {}}, headers=headers, timeout=30)
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
    payload = {"data": {"type": "profile", "attributes": {
        "email": email, "properties": {"LPL_Referral_Code": referral_code}
    }}}
    res = requests.post(url, json=payload, headers=headers, timeout=15)
    return res.status_code in [200, 201, 202]

def process_buyer(email, existing_code):
    """Crée ou renouvelle le code parrainage + push Klaviyo. Thread-safe, pas d'écriture BQ."""
    new_exp_date = datetime.now(ZoneInfo("Europe/Paris")) + timedelta(days=365)
    new_exp_iso = new_exp_date.isoformat()
    try:
        if not existing_code:
            final_code = generate_code("LPL")
            mutation = """
            mutation discountCodeBasicCreate($basicCodeDiscount: DiscountCodeBasicInput!) {
              discountCodeBasicCreate(basicCodeDiscount: $basicCodeDiscount) {
                codeDiscountNode { id }
              }
            }"""
            variables = {"basicCodeDiscount": {
                "title": final_code, "code": final_code,
                "startsAt": datetime.now().isoformat(), "endsAt": new_exp_iso, "usageLimit": 5,
                "customerSelection": {"all": True},
                "customerGets": {"value": {"discountAmount": {"amount": 10.0, "appliesOnEachItem": False}}, "items": {"all": True}},
                "minimumRequirement": {"subtotal": {"greaterThanOrEqualToSubtotal": 49.0}},
                "combinesWith": {"productDiscounts": True, "shippingDiscounts": True}
            }}
            res = run_shopify_graphql(mutation, variables)
            rule_id = (res or {}).get("data", {}).get("discountCodeBasicCreate", {}).get("codeDiscountNode", {}).get("id")
            klaviyo_ok = push_to_klaviyo(email, final_code) if rule_id else False
            return (email, final_code, True, rule_id, new_exp_date, klaviyo_ok)
        else:
            final_code = existing_code["code"]
            rule_id = existing_code["shopify_rule_id"]
            if rule_id:
                mutation_update = """
                mutation discountCodeBasicUpdate($id: ID!, $basicCodeDiscount: DiscountCodeBasicInput!) {
                  discountCodeBasicUpdate(id: $id, basicCodeDiscount: $basicCodeDiscount) {
                    codeDiscountNode { id }
                  }
                }"""
                run_shopify_graphql(mutation_update, {"id": rule_id, "basicCodeDiscount": {"endsAt": new_exp_iso}})
            klaviyo_ok = push_to_klaviyo(email, final_code)
            return (email, final_code, False, rule_id, new_exp_date, klaviyo_ok)
    except Exception as e:
        print(f"❌ Erreur {email}: {e}", flush=True)
        return (email, None, False, None, new_exp_date, False)

@app.route('/api/sync-klaviyo-codes', methods=['POST'])
def sync_klaviyo_codes():
    if request.headers.get('X-Cron-Secret') != CRON_SECRET:
        return jsonify({"error": "Unauthorized"}), 403

    print("🚀 Début synchro Klaviyo...", flush=True)

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
    buyers = list(bq_client.query(query_recent_buyers))
    if not buyers:
        print("✅ Aucun acheteur récent.", flush=True)
        return jsonify({"status": "success", "message": "Aucun acheteur récent."}), 200

    emails_list = [r.email.lower().strip() for r in buyers]
    print(f"📊 {len(emails_list)} clients à traiter...", flush=True)

    query_codes = f"""
        SELECT owner_email, code, shopify_rule_id
        FROM `{PROJECT_ID}.shopify_data_eu.referral_codes`
        WHERE owner_email IN UNNEST(@emails) AND code LIKE 'LPL-%'
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ArrayQueryParameter("emails", "STRING", emails_list)]
    )
    existing_codes = {
        r.owner_email: {"code": r.code, "shopify_rule_id": r.shopify_rule_id}
        for r in bq_client.query(query_codes, job_config=job_config)
    }
    print(f"🔍 {len(existing_codes)} codes existants trouvés.", flush=True)

    results = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(process_buyer, email, existing_codes.get(email)): email
            for email in emails_list
        }
        for future in as_completed(futures):
            results.append(future.result())

    success_count = 0
    for email, final_code, is_new, rule_id, new_exp_date, klaviyo_ok in results:
        if klaviyo_ok:
            success_count += 1
        exp_str = new_exp_date.strftime('%Y-%m-%d %H:%M:%S')
        if is_new and rule_id and final_code:
            bq_client.query(
                f"INSERT INTO `{PROJECT_ID}.shopify_data_eu.referral_codes` "
                f"(code, owner_email, created_at, expires_at, status, shopify_rule_id, reward_value, usage_count, max_usage) "
                f"VALUES ('{final_code}', '{email}', CURRENT_TIMESTAMP(), '{exp_str}', 'ACTIVE', '{rule_id}', 10.0, 0, 5)"
            ).result()
        elif not is_new and final_code:
            bq_client.query(
                f"UPDATE `{PROJECT_ID}.shopify_data_eu.referral_codes` "
                f"SET expires_at = '{exp_str}' WHERE code = '{final_code}'"
            ).result()

    print(f"🏁 Terminé ! {success_count} profils Klaviyo mis à jour.", flush=True)
    return jsonify({"status": "success", "klaviyo_profiles_updated": success_count}), 200

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

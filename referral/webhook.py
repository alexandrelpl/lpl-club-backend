import os
import requests
import re
import toml
from flask import Flask, request, jsonify
from google.cloud import bigquery

app = Flask(__name__)
PROJECT_ID = "shopify-data-ltv"

try:
    secrets = toml.load(".streamlit/secrets.toml")
    SHOPIFY_STORE = secrets["shopify"]["store_url"]
    SHOPIFY_TOKEN = secrets["shopify"]["access_token"]
    KLAVIYO_API_KEY = secrets["klaviyo"]["api_key"]
except:
    SHOPIFY_STORE = os.environ.get("SHOPIFY_STORE", "")
    SHOPIFY_TOKEN = os.environ.get("SHOPIFY_TOKEN", "")
    KLAVIYO_API_KEY = os.environ.get("KLAVIYO_API_KEY", "")

client = bigquery.Client(project=PROJECT_ID)

def normalize_email(email):
    if not email or '@' not in email: return ""
    email = email.lower().strip()
    local, domain = email.split('@')
    if domain in ['gmail.com', 'googlemail.com']: local = local.split('+')[0].replace('.', '')
    return f"{local}@{domain}"

def normalize_phone(phone):
    if not phone: return ""
    clean = re.sub(r'\D', '', phone)
    if clean.startswith('33') and len(clean) > 9: clean = '0' + clean[2:]
    return clean

def run_shopify_graphql(query, variables=None):
    url = f"https://{SHOPIFY_STORE}/admin/api/2025-04/graphql.json"
    headers = {"X-Shopify-Access-Token": SHOPIFY_TOKEN, "Content-Type": "application/json"}
    response = requests.post(url, json={"query": query, "variables": variables or {}}, headers=headers)
    return response.json() if response.status_code == 200 else None

def get_customer_phone_from_shopify(email):
    if not email: return ""
    try:
        url = f"https://{SHOPIFY_STORE}/admin/api/2025-04/customers/search.json?query=email:{email}&fields=phone,default_address"
        r = requests.get(url, headers={"X-Shopify-Access-Token": SHOPIFY_TOKEN})
        if r.status_code == 200:
            customers = r.json().get("customers", [])
            if customers:
                phone = customers[0].get("phone")
                if not phone and customers[0].get("default_address"): phone = customers[0]["default_address"].get("phone")
                return normalize_phone(phone)
    except Exception as e: pass
    return ""

def delete_shopify_discount(rule_id):
    if not rule_id: return False
    run_shopify_graphql("mutation discountCodeDelete($id: ID!) { discountCodeDelete(id: $id) { deletedCodeDiscountId } }", {"id": rule_id})

def trigger_klaviyo_update(owner_email, current_lpl_usage, max_usage, bq_client):
    try:
        q_spent = f"SELECT SUM(reward_value) as total_spent FROM `{PROJECT_ID}.shopify_data_eu.referral_codes` WHERE owner_email = '{owner_email}' AND code LIKE 'KDO-%' AND status = 'USED'"
        res = list(bq_client.query(q_spent))
        total_spent = res[0].total_spent if res and res[0].total_spent else 0.0
        
        new_balance = (current_lpl_usage * 10.0) - total_spent
        utilisations_restantes = max(0, max_usage - current_lpl_usage)
        
        url = "https://a.klaviyo.com/api/events/"
        headers = { "Authorization": f"Klaviyo-API-Key {KLAVIYO_API_KEY}", "accept": "application/json", "revision": "2024-02-15", "content-type": "application/json" }
        payload = { "data": { "type": "event", "attributes": { "profile": { "data": { "type": "profile", "attributes": { "email": owner_email } } }, "metric": { "data": { "type": "metric", "attributes": { "name": "Cagnotte_Parrainage_Mise_A_Jour" } } }, "properties": { "nouveau_solde": float(new_balance), "utilisations_restantes": int(utilisations_restantes) } } } }
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code in [200, 201, 202, 204]: print(f"✅ Klaviyo notifié E-COM : {owner_email} -> {new_balance}€", flush=True) 
        else: print(f"❌ Erreur Klaviyo API : {response.text}", flush=True)
    except Exception as e: print(f"Erreur critique Klaviyo: {e}", flush=True)

def block_referral_code(code, rule_id, reason):
    print(f"⛔ BLOCAGE DU CODE {code} : {reason}", flush=True)
    q_block = f"UPDATE `{PROJECT_ID}.shopify_data_eu.referral_codes` SET status = 'BLOCKED_FRAUD' WHERE code = @code"
    client.query(q_block, job_config=bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("code", "STRING", code)])).result()
    delete_shopify_discount(rule_id)

@app.route('/shopify-webhook', methods=['POST'])
def handle_webhook():
    data = request.json
    order_id = str(data.get("id", ""))
    customer_email = data.get("email", "").lower()
    raw_phone = data.get("phone") or data.get("billing_address", {}).get("phone") or ""
    customer_phone = normalize_phone(raw_phone)

    discount_codes = []
    if 'discount_applications' in data: discount_codes = [app.get('code', '').upper() for app in data['discount_applications'] if app.get('type') == 'discount_code']
    elif 'discount_codes' in data: discount_codes = [dc.get('code', '').upper() for dc in data['discount_codes']]

    if not discount_codes: return jsonify({"status": "ignored"}), 200

    for code_used in discount_codes:
        if code_used.startswith("LPL-") or code_used.startswith("KDO-"):
            q_exist = f"SELECT 1 FROM `{PROJECT_ID}.shopify_data_eu.referral_redemptions` WHERE order_id = @oid AND referrer_id = @code"
            if list(client.query(q_exist, job_config=bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("oid", "STRING", order_id), bigquery.ScalarQueryParameter("code", "STRING", code_used)]))):
                return jsonify({"status": "ignored", "reason": "Duplicate order"}), 200

            q_code = f"SELECT owner_email, status, reward_value, usage_count, max_usage, shopify_rule_id FROM `{PROJECT_ID}.shopify_data_eu.referral_codes` WHERE code = @code"
            rows = list(client.query(q_code, job_config=bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("code", "STRING", code_used)])))
            if not rows: continue
            row = rows[0]

            if code_used.startswith("LPL-"):
                parrain_email = row.owner_email
                
                q_already_referred = f"SELECT 1 FROM `{PROJECT_ID}.shopify_data_eu.referral_redemptions` WHERE referred_id = @cust_email AND DATE(redemption_date) >= DATE_SUB(CURRENT_DATE(), INTERVAL 3 YEAR)"
                if list(client.query(q_already_referred, job_config=bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("cust_email", "STRING", customer_email)]))):
                    return jsonify({"status": "ignored", "reason": "Customer already referred"}), 200

                is_fraud, fraud_reason = False, ""
                if normalize_email(customer_email) == normalize_email(parrain_email): is_fraud, fraud_reason = True, "Email identique"
                if not is_fraud and customer_phone:
                    parrain_phone = get_customer_phone_from_shopify(parrain_email)
                    if parrain_phone and parrain_phone == customer_phone: is_fraud, fraud_reason = True, "Numéro de téléphone identique"

                if is_fraud:
                    block_referral_code(code_used, row.shopify_rule_id, fraud_reason)
                    return jsonify({"status": "blocked", "reason": fraud_reason}), 200

                new_usage = (row.usage_count or 0) + 1
                max_usage = row.max_usage or 5
                
                client.query(f"UPDATE `{PROJECT_ID}.shopify_data_eu.referral_codes` SET usage_count = {new_usage} WHERE code = '{code_used}'").result()
                client.query(f"INSERT INTO `{PROJECT_ID}.shopify_data_eu.referral_redemptions` (referrer_id, referred_id, amount_rewarded, amount, store_location, redemption_date, order_id) VALUES ('{code_used}', '{customer_email}', 10.0, 10.0, 'SHOPIFY_ONLINE', CURRENT_TIMESTAMP(), '{order_id}')").result()

                if new_usage >= max_usage:
                    client.query(f"UPDATE `{PROJECT_ID}.shopify_data_eu.referral_codes` SET status = 'MAX_REACHED' WHERE code = '{code_used}'").result()
                    delete_shopify_discount(row.shopify_rule_id)

                trigger_klaviyo_update(parrain_email, new_usage, max_usage, client)

            elif code_used.startswith("KDO-"):
                client.query(f"UPDATE `{PROJECT_ID}.shopify_data_eu.referral_codes` SET status = 'USED' WHERE code = '{code_used}'").result()

    return jsonify({"status": "success"}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
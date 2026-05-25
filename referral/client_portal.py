import os
import random
import string
from datetime import datetime, timedelta
import requests
import hmac
import hashlib
import toml
from flask import Flask, request, jsonify
from flask_cors import CORS
from google.cloud import bigquery

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

PROJECT_ID = "shopify-data-ltv"

try:
    secrets = toml.load(".streamlit/secrets.toml")
    SHOPIFY_STORE = secrets["shopify"]["store_url"]
    SHOPIFY_TOKEN = secrets["shopify"]["access_token"]
    SHARED_KEY = secrets["security"]["shared_key"]
except Exception as e:
    SHOPIFY_STORE = os.environ.get("SHOPIFY_STORE", "")
    SHOPIFY_TOKEN = os.environ.get("SHOPIFY_TOKEN", "")
    SHARED_KEY = os.environ.get("SHARED_KEY", "fallback_secret")

client = bigquery.Client(project=PROJECT_ID)

def get_shopify_headers(): return {"X-Shopify-Access-Token": SHOPIFY_TOKEN, "Content-Type": "application/json"}

def run_shopify_graphql(query, variables=None):
    url = f"https://{SHOPIFY_STORE}/admin/api/2025-04/graphql.json"
    response = requests.post(url, json={"query": query, "variables": variables or {}}, headers=get_shopify_headers())
    return response.json() if response.status_code == 200 else None

def create_shopify_discount(code, amount, usage_limit=None):
    mutation = """
    mutation discountCodeBasicCreate($basicCodeDiscount: DiscountCodeBasicInput!) {
      discountCodeBasicCreate(basicCodeDiscount: $basicCodeDiscount) {
        codeDiscountNode { id codeDiscount { ... on DiscountCodeBasic { title codes(first: 1) { nodes { code } } } } }
      }
    }
    """
    variables = {
        "basicCodeDiscount": {
            "title": code, "code": code, "startsAt": datetime.now().isoformat(), "endsAt": (datetime.now() + timedelta(days=365)).isoformat(), "usageLimit": usage_limit,
            "customerSelection": { "all": True }, "customerGets": { "value": { "discountAmount": { "amount": float(amount), "appliesOnEachItem": False } }, "items": { "all": True } },
            "minimumRequirement": { "subtotal": { "greaterThanOrEqualToSubtotal": 49.0 } }, "combinesWith": { "productDiscounts": True, "shippingDiscounts": True }
        }
    }
    res = run_shopify_graphql(mutation, variables)
    try: return res.get("data", {}).get("discountCodeBasicCreate", {})["codeDiscountNode"]["id"]
    except: return None

def delete_shopify_discount(rule_id):
    if not rule_id: return False
    res = run_shopify_graphql("mutation discountCodeDelete($id: ID!) { discountCodeDelete(id: $id) { deletedCodeDiscountId } }", {"id": rule_id})
    return res.get("data", {}).get("discountCodeDelete", {}).get("deletedCodeDiscountId") is not None

def generate_code(prefix): return f"{prefix}-{''.join(random.choices(string.ascii_uppercase + string.digits, k=4))}"

def normalize_email(email):
    if not email or '@' not in email: return ""
    email = email.lower().strip()
    local, domain = email.split('@')
    if domain in ['gmail.com', 'googlemail.com']: local = local.split('+')[0].replace('.', '')
    return f"{local}@{domain}"

# =====================================================================
# ROUTE : PORTAIL CLIENT
# =====================================================================
@app.route('/api/get-referral-data', methods=['POST'])
def get_referral_data():
    data = request.json
    email = data.get('email', '').lower().strip()
    signature = data.get('signature', '')

    expected_sign = hmac.new(SHARED_KEY.encode('utf-8'), email.encode('utf-8'), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_sign, signature): return jsonify({"error": "Unauthorized"}), 403

    all_codes = list(client.query(f"SELECT code, max_usage, shopify_rule_id, expires_at, usage_count, status, reward_value FROM `{PROJECT_ID}.shopify_data_eu.referral_codes` WHERE owner_email = @email ORDER BY created_at DESC", job_config=bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("email", "STRING", email)])))
    
    # IGNORE LES CODES ARCHIVÉS POUR PERMETTRE LE RESET
    lpl_codes = [c for c in all_codes if c.code.startswith('LPL-') and c.status != 'ARCHIVED']
    active_kdo_codes = [c for c in all_codes if c.code.startswith('KDO-') and c.status == 'ACTIVE']
    total_spent = sum([c.reward_value for c in all_codes if c.code.startswith('KDO-') and c.status == 'USED' and c.reward_value])

    if not lpl_codes:
        new_lpl = generate_code("LPL")
        rule_id = create_shopify_discount(new_lpl, 10.0, usage_limit=5)
        exp_date = datetime.now() + timedelta(days=365)
        client.query(f"INSERT INTO `{PROJECT_ID}.shopify_data_eu.referral_codes` (code, owner_email, created_at, expires_at, status, shopify_rule_id, reward_value, usage_count, max_usage) VALUES (@c, @e, CURRENT_TIMESTAMP(), @ex, 'ACTIVE', @r, 10.0, 0, 5)", job_config=bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("c", "STRING", new_lpl), bigquery.ScalarQueryParameter("e", "STRING", email), bigquery.ScalarQueryParameter("ex", "TIMESTAMP", exp_date), bigquery.ScalarQueryParameter("r", "STRING", rule_id)])).result()
        current_code, max_usage, is_expired, exp_str, old_usage_count = new_lpl, 5, False, exp_date.strftime("%d/%m/%Y"), 0
    else:
        row = lpl_codes[0]
        if row.status in ['BLOCKED_FRAUD', 'BLOCKED_PUBLIC']:
            msg = "🚨 Vos codes ont été bloqués suite à une détection sur une plateforme publique." if row.status == 'BLOCKED_PUBLIC' else "🚨 Votre espace a été suspendu pour auto-parrainage."
            return jsonify({"is_eligible": True, "referral_code": {"code": "BLOQUÉ 🛑", "usage_count": 0, "max_usage": 5, "is_expired": True, "expires_at_formatted": "Compte Suspendu"}, "kdo": {"code": "BLOQUÉ 🛑", "balance": 0, "expires_at_formatted": "Compte Suspendu", "is_expired": True, "message": msg}}), 200

        current_code, max_usage, old_usage_count = row.code, row.max_usage or 5, row.usage_count or 0
        is_expired = row.expires_at < datetime.now(row.expires_at.tzinfo) if row.expires_at else False
        exp_str = row.expires_at.strftime("%d/%m/%Y") if row.expires_at else "Non définie"

    usage_res = list(client.query(f"WITH all_usages AS (SELECT LOWER(referred_id) as referral_email FROM `{PROJECT_ID}.shopify_data_eu.referral_redemptions` WHERE referrer_id = @code UNION DISTINCT SELECT LOWER(email) as referral_email FROM `{PROJECT_ID}.shopify_data_eu.custom_transactions_history` WHERE CONTAINS_SUBSTR(discount_code, @code)) SELECT COUNT(DISTINCT referral_email) as total FROM all_usages", job_config=bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("code", "STRING", current_code)])))
    total_usages = usage_res[0].total if usage_res else 0

    if total_usages != old_usage_count: client.query(f"UPDATE `{PROJECT_ID}.shopify_data_eu.referral_codes` SET usage_count = {total_usages} WHERE code = '{current_code}'").result()

    user_code_data = {"code": current_code, "usage_count": total_usages, "max_usage": max_usage, "is_expired": is_expired, "expires_at_formatted": exp_str}
    current_balance = (total_usages * 10.0) - total_spent
    
    final_kdo = {"code": "À DÉBLOQUER 🚀", "balance": current_balance, "expires_at_formatted": "-", "is_expired": False, "message": "🎁 Partagez votre code LPL pour remplir votre cagnotte !"}
    
    if current_balance > 0:
        if not active_kdo_codes:
            new_kdo = generate_code("KDO")
            rule_id = create_shopify_discount(new_kdo, current_balance, usage_limit=1)
            exp_date_kdo = datetime.now() + timedelta(days=365)
            client.query(f"INSERT INTO `{PROJECT_ID}.shopify_data_eu.referral_codes` (code, owner_email, created_at, expires_at, status, shopify_rule_id, reward_value, usage_count, max_usage) VALUES (@code, @email, CURRENT_TIMESTAMP(), @exp, 'ACTIVE', @rule, @val, 0, 1)", job_config=bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("code", "STRING", new_kdo), bigquery.ScalarQueryParameter("email", "STRING", email), bigquery.ScalarQueryParameter("exp", "TIMESTAMP", exp_date_kdo), bigquery.ScalarQueryParameter("rule", "STRING", rule_id), bigquery.ScalarQueryParameter("val", "FLOAT64", float(current_balance))])).result()
            final_kdo = {"code": new_kdo, "balance": current_balance, "expires_at_formatted": exp_date_kdo.strftime("%d/%m/%Y"), "is_expired": False, "message": "Félicitations, voici votre code cadeau !"}
        else:
            old_kdo = active_kdo_codes[0]
            is_kdo_expired = True if old_kdo.expires_at and old_kdo.expires_at < datetime.now(old_kdo.expires_at.tzinfo) else False
            
            if (current_balance > (old_kdo.reward_value or 0.0)) or (is_kdo_expired and current_balance > 0):
                if old_kdo.shopify_rule_id: delete_shopify_discount(old_kdo.shopify_rule_id)
                client.query(f"UPDATE `{PROJECT_ID}.shopify_data_eu.referral_codes` SET status = 'UPGRADED' WHERE code = '{old_kdo.code}'").result()
                new_kdo, rule_id, exp_date_kdo = generate_code("KDO"), create_shopify_discount(new_kdo, current_balance, usage_limit=1), datetime.now() + timedelta(days=365)
                client.query(f"INSERT INTO `{PROJECT_ID}.shopify_data_eu.referral_codes` (code, owner_email, created_at, expires_at, status, shopify_rule_id, reward_value, usage_count, max_usage) VALUES (@code, @email, CURRENT_TIMESTAMP(), @exp, 'ACTIVE', @rule, @val, 0, 1)", job_config=bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("code", "STRING", new_kdo), bigquery.ScalarQueryParameter("email", "STRING", email), bigquery.ScalarQueryParameter("exp", "TIMESTAMP", exp_date_kdo), bigquery.ScalarQueryParameter("rule", "STRING", rule_id), bigquery.ScalarQueryParameter("val", "FLOAT64", float(current_balance))])).result()
                final_kdo = {"code": new_kdo, "balance": current_balance, "expires_at_formatted": exp_date_kdo.strftime("%d/%m/%Y"), "is_expired": False, "message": "Votre cagnotte a été mise à jour !"}
            else:
                final_kdo = {"code": old_kdo.code, "balance": old_kdo.reward_value, "expires_at_formatted": old_kdo.expires_at.strftime("%d/%m/%Y") if old_kdo.expires_at else "-", "is_expired": is_kdo_expired, "message": "Voici votre code cadeau actif."}

    return jsonify({"is_eligible": True, "referral_code": user_code_data, "kdo": final_kdo}), 200

# =====================================================================
# ROUTE : CHECKOUT VALIDATION (TEMPS RÉEL SUR LA PAGE DE PAIEMENT)
# =====================================================================
@app.route('/api/checkout-validate', methods=['POST', 'OPTIONS'])
def checkout_validate():
    if request.method == 'OPTIONS':
        return ('', 204, {'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'POST', 'Access-Control-Allow-Headers': 'Content-Type'})

    data = request.json
    if not data: return jsonify({"is_valid": True}), 200, {"Access-Control-Allow-Origin": "*"}
    email_or_phone, code = data.get('email', ''), data.get('code', '').upper()
    if not email_or_phone or not code.startswith('LPL-'): return jsonify({"is_valid": True}), 200, {"Access-Control-Allow-Origin": "*"}
        
    res = list(client.query(f"SELECT owner_email FROM `{PROJECT_ID}.shopify_data_eu.referral_codes` WHERE code = @c LIMIT 1", job_config=bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("c", "STRING", code)])))
    if res and normalize_email(email_or_phone) == normalize_email(res[0].owner_email):
        return jsonify({"is_valid": False, "error_message": "🛑 Fraude détectée : Vous ne pouvez pas utiliser votre propre code."}), 200, {"Access-Control-Allow-Origin": "*"}
            
    res_client = list(client.query(f"SELECT email FROM `{PROJECT_ID}.shopify_data_eu.vw_unified_customer_last_order` WHERE LOWER(email) = LOWER(@e) AND absolute_last_order_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 3 YEAR) UNION ALL SELECT referred_id as email FROM `{PROJECT_ID}.shopify_data_eu.referral_redemptions` WHERE LOWER(referred_id) = LOWER(@e) AND DATE(redemption_date) >= DATE_SUB(CURRENT_DATE(), INTERVAL 3 YEAR) LIMIT 1", job_config=bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("e", "STRING", email_or_phone)])))
    if res_client: return jsonify({"is_valid": False, "error_message": "🛑 Ce code LPL est strictement réservé aux nouveaux clients."}), 200, {"Access-Control-Allow-Origin": "*"}
        
    return jsonify({"is_valid": True}), 200, {"Access-Control-Allow-Origin": "*"}

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
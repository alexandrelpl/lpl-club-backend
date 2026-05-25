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

# --- CHARGEMENT DES SECRETS ---
try:
    secrets = toml.load(".streamlit/secrets.toml")
    SHOPIFY_STORE = secrets["shopify"]["store_url"]
    SHOPIFY_TOKEN = secrets["shopify"]["access_token"]
    SHARED_KEY = secrets["security"]["shared_key"]
except Exception:
    SHOPIFY_STORE = os.environ.get("SHOPIFY_STORE", "")
    SHOPIFY_TOKEN = os.environ.get("SHOPIFY_TOKEN", "")
    SHARED_KEY = os.environ.get("SHARED_KEY", "fallback_secret")

client = bigquery.Client(project=PROJECT_ID)

def get_shopify_headers():
    return {"X-Shopify-Access-Token": SHOPIFY_TOKEN, "Content-Type": "application/json"}

def run_shopify_graphql(query, variables=None):
    url = f"https://{SHOPIFY_STORE}/admin/api/2025-04/graphql.json"
    # OPTIMISATION : Timeout court pour ne pas bloquer l'interface si Shopify est lent
    try:
        response = requests.post(url, json={"query": query, "variables": variables or {}}, headers=get_shopify_headers(), timeout=5)
        return response.json() if response.status_code == 200 else None
    except requests.exceptions.RequestException:
        return None

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
            "title": code, "code": code, 
            "startsAt": datetime.now().isoformat(), 
            "endsAt": (datetime.now() + timedelta(days=365)).isoformat(), 
            "usageLimit": usage_limit,
            "customerSelection": { "all": True }, 
            "customerGets": { "value": { "discountAmount": { "amount": float(amount), "appliesOnEachItem": False } }, "items": { "all": True } },
            "minimumRequirement": { "subtotal": { "greaterThanOrEqualToSubtotal": 49.0 } }, 
            "combinesWith": { "productDiscounts": True, "shippingDiscounts": True }
        }
    }
    res = run_shopify_graphql(mutation, variables)
    try:
        return res.get("data", {}).get("discountCodeBasicCreate", {})["codeDiscountNode"]["id"]
    except Exception:
        return None

def delete_shopify_discount(rule_id):
    if not rule_id: return False
    res = run_shopify_graphql("mutation discountCodeDelete($id: ID!) { discountCodeDelete(id: $id) { deletedCodeDiscountId } }", {"id": rule_id})
    return res is not None

def generate_code(prefix): 
    return f"{prefix}-{''.join(random.choices(string.ascii_uppercase + string.digits, k=4))}"

def normalize_email(email):
    if not email or '@' not in email: return ""
    email = email.lower().strip()
    local, domain = email.split('@')
    if domain in ['gmail.com', 'googlemail.com']: 
        local = local.split('+')[0].replace('.', '')
    return f"{local}@{domain}"

# =====================================================================
# ROUTE : PORTAIL CLIENT (OPTIMISÉE POUR LA VITESSE)
# =====================================================================
@app.route('/api/get-referral-data', methods=['POST'])
def get_referral_data():
    data = request.json
    email = data.get('email', '').lower().strip()
    signature = data.get('signature', '')

    expected_sign = hmac.new(SHARED_KEY.encode('utf-8'), email.encode('utf-8'), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_sign, signature): 
        return jsonify({"error": "Unauthorized"}), 403

    # OPTIMISATION 1 : Une seule requête pour tout récupérer
    q_all = f"SELECT code, max_usage, shopify_rule_id, expires_at, usage_count, status, reward_value FROM `{PROJECT_ID}.shopify_data_eu.referral_codes` WHERE owner_email = @email ORDER BY created_at DESC"
    all_codes = list(client.query(q_all, job_config=bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("email", "STRING", email)])))
    
    lpl_codes = [c for c in all_codes if c.code.startswith('LPL-') and c.status != 'ARCHIVED']
    active_kdo_codes = [c for c in all_codes if c.code.startswith('KDO-') and c.status == 'ACTIVE']
    total_spent = sum([c.reward_value for c in all_codes if c.code.startswith('KDO-') and c.status == 'USED' and c.reward_value])

    # --- GESTION DU CODE PARRAIN (LPL) ---
    if not lpl_codes:
        new_lpl = generate_code("LPL")
        rule_id = create_shopify_discount(new_lpl, 10.0, usage_limit=5)
        exp_date = datetime.now() + timedelta(days=365)
        
        q_ins = f"INSERT INTO `{PROJECT_ID}.shopify_data_eu.referral_codes` (code, owner_email, created_at, expires_at, status, shopify_rule_id, reward_value, usage_count, max_usage) VALUES (@c, @e, CURRENT_TIMESTAMP(), @ex, 'ACTIVE', @r, 10.0, 0, 5)"
        client.query(q_ins, job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("c", "STRING", new_lpl), 
            bigquery.ScalarQueryParameter("e", "STRING", email), 
            bigquery.ScalarQueryParameter("ex", "TIMESTAMP", exp_date), 
            bigquery.ScalarQueryParameter("r", "STRING", rule_id)
        ])) # Pas de .result() bloquant ici si on s'en fiche d'attendre la fin parfaite
        
        current_code, max_usage, total_usages = new_lpl, 5, 0
        is_expired, exp_str = False, exp_date.strftime("%d/%m/%Y")
    else:
        row = lpl_codes[0]
        
        # 🚨 SÉCURITÉ : BLOCAGE DE L'ESPACE
        if row.status in ['BLOCKED_FRAUD', 'BLOCKED_PUBLIC']:
            msg = "🛑 Vos codes de parrainage et votre cagnotte ont été bloqués après avoir été détectés sur une plateforme de codes promo..." if row.status == 'BLOCKED_PUBLIC' else "🛑 Votre espace a été suspendu suite à la détection d'une activité non autorisée."
            return jsonify({"is_eligible": False, "message": msg}), 200

        current_code = row.code
        max_usage = row.max_usage or 5
        # OPTIMISATION 2 : On fait confiance au champ `usage_count` de la DB, on ne recalcule plus tout l'historique !
        total_usages = row.usage_count or 0 
        is_expired = True if row.expires_at and row.expires_at < datetime.now(row.expires_at.tzinfo) else False
        exp_str = row.expires_at.strftime("%d/%m/%Y") if row.expires_at else "Non définie"

    user_code_data = {
        "code": current_code, 
        "usage_count": total_usages, 
        "max_usage": max_usage, 
        "is_expired": is_expired, 
        "expires_at_formatted": exp_str
    }
    
    # --- GESTION DE LA CAGNOTTE (KDO) ---
    current_balance = (total_usages * 10.0) - total_spent
    final_kdo = {
        "code": "À DÉBLOQUER 🚀", 
        "balance": current_balance, 
        "expires_at_formatted": "-", 
        "is_expired": False, 
        "message": "🎁 Partagez votre code LPL pour remplir votre cagnotte !"
    }
    
    if current_balance > 0:
        if not active_kdo_codes:
            new_kdo = generate_code("KDO")
            rule_id = create_shopify_discount(new_kdo, current_balance, usage_limit=1)
            exp_date_kdo = datetime.now() + timedelta(days=365)
            q_ins_kdo = f"INSERT INTO `{PROJECT_ID}.shopify_data_eu.referral_codes` (code, owner_email, created_at, expires_at, status, shopify_rule_id, reward_value, usage_count, max_usage) VALUES (@code, @email, CURRENT_TIMESTAMP(), @exp, 'ACTIVE', @rule, @val, 0, 1)"
            client.query(q_ins_kdo, job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ScalarQueryParameter("code", "STRING", new_kdo), 
                bigquery.ScalarQueryParameter("email", "STRING", email), 
                bigquery.ScalarQueryParameter("exp", "TIMESTAMP", exp_date_kdo), 
                bigquery.ScalarQueryParameter("rule", "STRING", rule_id), 
                bigquery.ScalarQueryParameter("val", "FLOAT64", float(current_balance))
            ]))
            final_kdo = {"code": new_kdo, "balance": current_balance, "expires_at_formatted": exp_date_kdo.strftime("%d/%m/%Y"), "is_expired": False, "message": "Félicitations, voici votre code cadeau !"}
        else:
            old_kdo = active_kdo_codes[0]
            is_kdo_expired = True if old_kdo.expires_at and old_kdo.expires_at < datetime.now(old_kdo.expires_at.tzinfo) else False
            
            # Mise à jour si la cagnotte a grossi ou si l'ancien code a expiré mais qu'il reste de l'argent
            if (current_balance > (old_kdo.reward_value or 0.0)) or (is_kdo_expired and current_balance > 0):
                if old_kdo.shopify_rule_id: 
                    delete_shopify_discount(old_kdo.shopify_rule_id)
                    
                client.query(f"UPDATE `{PROJECT_ID}.shopify_data_eu.referral_codes` SET status = 'UPGRADED' WHERE code = '{old_kdo.code}'")
                new_kdo = generate_code("KDO")
                rule_id = create_shopify_discount(new_kdo, current_balance, usage_limit=1)
                exp_date_kdo = datetime.now() + timedelta(days=365)
                
                q_ins_kdo = f"INSERT INTO `{PROJECT_ID}.shopify_data_eu.referral_codes` (code, owner_email, created_at, expires_at, status, shopify_rule_id, reward_value, usage_count, max_usage) VALUES (@code, @email, CURRENT_TIMESTAMP(), @exp, 'ACTIVE', @rule, @val, 0, 1)"
                client.query(q_ins_kdo, job_config=bigquery.QueryJobConfig(query_parameters=[
                    bigquery.ScalarQueryParameter("code", "STRING", new_kdo), 
                    bigquery.ScalarQueryParameter("email", "STRING", email), 
                    bigquery.ScalarQueryParameter("exp", "TIMESTAMP", exp_date_kdo), 
                    bigquery.ScalarQueryParameter("rule", "STRING", rule_id), 
                    bigquery.ScalarQueryParameter("val", "FLOAT64", float(current_balance))
                ]))
                final_kdo = {"code": new_kdo, "balance": current_balance, "expires_at_formatted": exp_date_kdo.strftime("%d/%m/%Y"), "is_expired": False, "message": "Votre cagnotte a été mise à jour !"}
            else:
                final_kdo = {"code": old_kdo.code, "balance": old_kdo.reward_value, "expires_at_formatted": old_kdo.expires_at.strftime("%d/%m/%Y") if old_kdo.expires_at else "-", "is_expired": is_kdo_expired, "message": "Voici votre code cadeau actif."}

    return jsonify({
        "is_eligible": True, 
        "referral_code": user_code_data, 
        "kdo": final_kdo
    }), 200

# =====================================================================
# ROUTE : CHECKOUT VALIDATION (TEMPS RÉEL SUR LA PAGE DE PAIEMENT)
# =====================================================================
@app.route('/api/checkout-validate', methods=['POST', 'OPTIONS'])
def checkout_validate():
    if request.method == 'OPTIONS':
        return ('', 204, {'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'POST', 'Access-Control-Allow-Headers': 'Content-Type'})

    data = request.json
    if not data: return jsonify({"is_valid": True}), 200, {"Access-Control-Allow-Origin": "*"}
    email_or_phone = data.get('email', '')
    code = data.get('code', '').upper()
    
    if not email_or_phone or not code.startswith('LPL-'): 
        return jsonify({"is_valid": True}), 200, {"Access-Control-Allow-Origin": "*"}
        
    q_owner = f"SELECT owner_email FROM `{PROJECT_ID}.shopify_data_eu.referral_codes` WHERE code = @c LIMIT 1"
    res = list(client.query(q_owner, job_config=bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("c", "STRING", code)])))
    
    if res and normalize_email(email_or_phone) == normalize_email(res[0].owner_email):
        return jsonify({"is_valid": False, "error_message": "🛑 Fraude détectée : Vous ne pouvez pas utiliser votre propre code."}), 200, {"Access-Control-Allow-Origin": "*"}
            
    # OPTIMISATION : On cible uniquement la vue unifiée pour plus de rapidité au checkout
    q_client = f"SELECT email FROM `{PROJECT_ID}.shopify_data_eu.vw_unified_customer_last_order` WHERE LOWER(email) = LOWER(@e) AND absolute_last_order_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 3 YEAR) LIMIT 1"
    res_client = list(client.query(q_client, job_config=bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("e", "STRING", email_or_phone)])))
    
    if res_client: 
        return jsonify({"is_valid": False, "error_message": "🛑 Ce code LPL est strictement réservé aux nouveaux clients."}), 200, {"Access-Control-Allow-Origin": "*"}
        
    return jsonify({"is_valid": True}), 200, {"Access-Control-Allow-Origin": "*"}

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
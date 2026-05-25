import streamlit as st
from google.cloud import bigquery
from datetime import datetime, date
import requests
import re
import toml

st.set_page_config(page_title="LPL Retail - Validation Caisse", page_icon="💰")
PORTAL_URL = "https://lepetitlunetier.com/account/login?checkout_url=/pages/parrainage"
PROJECT_ID = "shopify-data-ltv"

try:
    secrets = toml.load(".streamlit/secrets.toml")
    SHOPIFY_STORE = secrets["shopify"]["store_url"]
    SHOPIFY_TOKEN = secrets["shopify"]["access_token"]
    KLAVIYO_API_KEY = secrets["klaviyo"]["api_key"]
except Exception as e:
    import os
    SHOPIFY_STORE = os.environ.get("SHOPIFY_STORE", "")
    SHOPIFY_TOKEN = os.environ.get("SHOPIFY_TOKEN", "")
    KLAVIYO_API_KEY = os.environ.get("KLAVIYO_API_KEY", "")

LISTE_BOUTIQUES = ["Choisir une boutique...", "Toulouse", "Lille", "Bordeaux", "Paris Temple", "Lyon", "Rennes", "Nantes", "Angers", "Aix-en-Provence", "Marseille", "Rouen", "Montpellier", "Paris St Antoine", "Nancy", "Strasbourg", "Nîmes", "Paris Abbesses"]

def run_shopify_graphql(query, variables=None):
    if not SHOPIFY_STORE or not SHOPIFY_TOKEN: return None
    url = f"https://{SHOPIFY_STORE}/admin/api/2024-04/graphql.json"
    headers = {"X-Shopify-Access-Token": SHOPIFY_TOKEN, "Content-Type": "application/json"}
    try:
        response = requests.post(url, json={"query": query, "variables": variables or {}}, headers=headers)
        if response.status_code == 200:
            res_data = response.json()
            if isinstance(res_data, dict): return res_data
        return None
    except Exception as e: return None

def delete_shopify_discount(rule_id):
    if not rule_id: return False
    mutation = "mutation discountCodeDelete($id: ID!) { discountCodeDelete(id: $id) { deletedCodeDiscountId } }"
    res = run_shopify_graphql(mutation, {"id": rule_id})
    if res and res.get("data") and res["data"].get("discountCodeDelete"): return res["data"]["discountCodeDelete"].get("deletedCodeDiscountId") is not None
    return False

def update_shopify_discount_limit(rule_id, new_limit):
    if not rule_id or new_limit < 1: return False
    mutation = "mutation discountCodeBasicUpdate($id: ID!, $basicCodeDiscount: DiscountCodeBasicInput!) { discountCodeBasicUpdate(id: $id, basicCodeDiscount: $basicCodeDiscount) { userErrors { message } } }"
    run_shopify_graphql(mutation, {"id": rule_id, "basicCodeDiscount": {"usageLimit": int(new_limit)}})
    return True

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
        if response.status_code in [200, 201, 202, 204]: return True, "OK"
        else: return False, f"Code {response.status_code}: {response.text}"
    except Exception as e: return False, str(e)

raw_boutique = st.query_params.get("boutique", "Choisir une boutique...")
if isinstance(raw_boutique, list): raw_boutique = raw_boutique[0]
boutique_dict = {b.lower(): b for b in LISTE_BOUTIQUES}
default_boutique = boutique_dict.get(raw_boutique.lower().strip(), "Choisir une boutique...")

def normalize_email(email):
    if not email or '@' not in email: return ""
    email = email.lower().strip()
    local, domain = email.split('@')
    local = local.split('+')[0]
    if domain in ['gmail.com', 'googlemail.com']: local = local.replace('.', '')
    return f"{local}@{domain}"

def set_design():
    LOGO_URL = "https://cdn.shopify.com/s/files/1/1169/1934/files/Logo_Bleu_-_Square.png?v=1770911062"
    BG_URL = "https://cdn.shopify.com/s/files/1/1169/1934/files/1125-LPL-DoubleJeu-Homme_Femme-Ella-Ecaille-Ezra-Gris-LB-Remy-Marine-Photo-2_1.jpg?v=1770912269"
    st.markdown(f"""
    <style>
    .stApp {{ background-image: url("{BG_URL}"); background-size: cover; background-position: center; background-attachment: fixed; }}
    .block-container {{ background-color: rgba(255, 255, 255, 0.95); padding: 3rem 2rem !important; border-radius: 15px; box-shadow: 0 10px 40px rgba(0,0,0,0.1); max_width: 650px; margin-top: 2rem; }}
    h1, h2, h3, h4, p, div, span, label {{ color: #1a1a1a !important; font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif !important; }}
    .logo-img {{ display: block; margin-left: auto; margin-right: auto; width: 140px; margin-bottom: 25px; }}
    .stTextInput div[data-baseweb="input"]:focus-within, div[data-baseweb="select"]:focus-within > div {{ border: 2px solid #0056b3 !important; box-shadow: 0 0 0 2px rgba(0, 86, 179, 0.2) !important; }}
    .stTextInput input, div[data-baseweb="select"] > div, ul[data-baseweb="menu"], li[role="option"] {{ color: #1a1a1a !important; background-color: #ffffff !important; border-radius: 6px !important; }}
    div.stButton > button {{ background-color: #1a1a1a !important; color: white !important; border-radius: 8px !important; width: 100% !important; height: 50px !important; font-weight: 600 !important; border: none !important; }}
    div.stButton > button p {{ color: white !important; }}
    div.stButton > button[kind="primary"] {{ background-color: #004494 !important; height: 95px !important; }}
    div.stButton > button[kind="primary"] p {{ font-size: 1.5rem !important; font-weight: 800 !important; color: white !important; }}
    div.stButton > button:disabled {{ background-color: #e0e0e0 !important; border: 1px solid #bdbdbd !important; }}
    div.stButton > button:disabled p {{ color: #9e9e9e !important; }}
    #MainMenu, footer, header {{visibility: hidden;}}
    </style>
    <img src="{LOGO_URL}" class="logo-img">
    """, unsafe_allow_html=True)
set_design()

client = bigquery.Client(project=PROJECT_ID)
if 'processing' not in st.session_state: st.session_state.processing = False
if 'success_msg' not in st.session_state: st.session_state.success_msg = False
if 'klaviyo_err' not in st.session_state: st.session_state.klaviyo_err = None
if 'ready_lpl' not in st.session_state: st.session_state.ready_lpl = False
if 'ready_kdo' not in st.session_state: st.session_state.ready_kdo = False

def start_processing(): st.session_state.processing = True

st.markdown("<h1 style='text-align: center; font-size: 2rem;'>VALIDATION CAISSE</h1>", unsafe_allow_html=True)
boutique_sel = st.selectbox("📍 Boutique actuelle :", LISTE_BOUTIQUES, index=LISTE_BOUTIQUES.index(default_boutique), disabled=st.session_state.processing)
st.divider()

if st.session_state.success_msg:
    st.success("✅ TRANSACTION ENREGISTRÉE AVEC SUCCÈS !")
    if st.session_state.klaviyo_err: st.warning(f"⚠️ La vente est validée, mais l'envoi de l'email à Klaviyo a échoué. Détails techniques : {st.session_state.klaviyo_err}")
    if st.button("Lancer une nouvelle transaction"):
        st.session_state.success_msg = False
        st.session_state.klaviyo_err = None
        st.session_state.ready_lpl = st.session_state.ready_kdo = False
        st.session_state.processing = False
        st.rerun()
    st.stop()

if boutique_sel == "Choisir une boutique...":
    st.warning("⚠️ VEUILLEZ SÉLECTIONNER VOTRE BOUTIQUE POUR COMMENCER.")
    st.stop()

# --- NOUVEAUX ONGLETS (3 au lieu de 2) ---
tab_filleul, tab_kdo, tab_vip = st.tabs(["👥 NOUVEAU FILLEUL", "🎁 CAGNOTTE PARRAIN", "🤑 VIP CLUB LPL"])

# --- ONGLET 1 : FILLEUL ---
with tab_filleul:
    f_email = st.text_input("Email Filleul :", placeholder="client@mail.com", disabled=st.session_state.processing)
    f_code = st.text_input("Code Parrain :", placeholder="LPL-XXXX", disabled=st.session_state.processing)

    if st.button("🔎 VÉRIFIER L'ÉLIGIBILITÉ", key="check_f", disabled=st.session_state.processing):
        st.session_state.ready_lpl = False
        with st.spinner('Vérification en cours...'):
            email_normalized = normalize_email(f_email)
            code_clean = f_code.strip().upper()
            
            q_c = f"SELECT owner_email, usage_count, max_usage, status, expires_at, shopify_rule_id FROM `{PROJECT_ID}.shopify_data_eu.referral_codes` WHERE code = '{code_clean}'"
            res = list(client.query(q_c))
            
            if not res: st.error("❌ CODE INCONNU")
            else:
                c = res[0]
                owner_normalized = normalize_email(c.owner_email)
                
                if email_normalized == owner_normalized:
                    st.error("🛑 FRAUDE DÉTECTÉE : Le client ne peut pas utiliser son propre code de parrainage.")
                else:
                    q_security = f"""
                    WITH matches AS (
                    SELECT email, EDIT_DISTANCE(LOWER(email), '{f_email.lower().strip()}') as dist FROM `{PROJECT_ID}.shopify_data_eu.vw_unified_customer_last_order` WHERE absolute_last_order_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 3 YEAR)
                    UNION ALL
                    SELECT referred_id as email, EDIT_DISTANCE(LOWER(referred_id), '{f_email.lower().strip()}') as dist FROM `{PROJECT_ID}.shopify_data_eu.referral_redemptions` WHERE DATE(redemption_date) >= DATE_SUB(CURRENT_DATE(), INTERVAL 3 YEAR)
                    )
                    SELECT email, dist FROM matches WHERE dist <= 2 ORDER BY dist ASC LIMIT 1
                    """
                    security_results = list(client.query(q_security))
                    is_expired = True if c.expires_at and c.expires_at < datetime.now(c.expires_at.tzinfo) else False

                    # --- CORRECTION DES MESSAGES D'ERREURS ---
                    if c.status == 'ARCHIVED':
                        st.error("❌ REFUSÉ : Ce code est archivé. Le client a généré un nouveau code dans son espace.")
                    elif is_expired:
                        st.error(f"❌ REFUSÉ : Ce code a expiré le {c.expires_at.strftime('%d/%m/%Y')}.")
                    elif c.status in ['BLOCKED', 'BLOCKED_FRAUD']:
                        st.error("🛑 REFUSÉ : Ce code a été bloqué par notre système anti-fraude.")
                    elif c.status == 'BLOCKED_PUBLIC':
                        st.error("❌ REFUSÉ : Ce code a fuité sur internet et a été désactivé.")
                    elif c.status == 'MAX_REACHED' or (c.usage_count or 0) >= (c.max_usage or 5):
                        st.error("❌ LIMITE ATTEINTE (5/5 utilisations).")
                    elif security_results and security_results[0].dist == 0:
                        st.error(f"❌ REFUSÉ : Cet email ({security_results[0].email}) appartient à un client existant (achat récent < 3 ans).")
                    else:
                        if security_results and security_results[0].dist > 0:
                            st.warning(f"⚠️ ATTENTION : L'email ressemble fortement à un client existant : **{security_results[0].email}**")
                            st.info("Vérifiez l'orthographe ou l'identité. Si c'est un vrai nouveau client, vous pouvez valider.")
                        else:
                            st.success("✅ ÉLIGIBLE : Appliquer -10€")
                            
                        st.session_state.ready_lpl = True
                        st.session_state.f_data = { "code": code_clean, "owner": c.owner_email, "usage": (c.usage_count or 0)+1, "rule_id": c.shopify_rule_id, "max_usage": (c.max_usage or 5) }

    if st.session_state.ready_lpl:
        if st.button("✅ VALIDER & BRÛLER L'USAGE", type="primary", on_click=start_processing, disabled=st.session_state.processing):
            with st.spinner('Traitement de la vente...'):
                d = st.session_state.f_data
                client.query(f"INSERT INTO `{PROJECT_ID}.shopify_data_eu.referral_redemptions` (referrer_id, referred_id, amount_rewarded, amount, store_location, redemption_date) VALUES ('{d['code']}', '{f_email}', 10.0, 10.0, '{boutique_sel}', CURRENT_TIMESTAMP())").result()
                client.query(f"UPDATE `{PROJECT_ID}.shopify_data_eu.referral_codes` SET usage_count = {d['usage']} WHERE code = '{d['code']}'").result()
                
                if d['usage'] >= 5:
                    client.query(f"UPDATE `{PROJECT_ID}.shopify_data_eu.referral_codes` SET status = 'MAX_REACHED' WHERE code = '{d['code']}'").result()
                    delete_shopify_discount(d['rule_id'])
                else:
                    q_retail = f"SELECT COUNT(*) as retail_usages FROM `{PROJECT_ID}.shopify_data_eu.referral_redemptions` WHERE referrer_id = '{d['code']}'"
                    retail_usages = list(client.query(q_retail))[0].retail_usages
                    new_shopify_limit = d['max_usage'] - retail_usages
                    update_shopify_discount_limit(d['rule_id'], new_shopify_limit)
                
                is_ok, err_msg = trigger_klaviyo_update(d['owner'], d['usage'], d['max_usage'], client)
                if not is_ok: st.session_state.klaviyo_err = err_msg
                st.session_state.success_msg = True
                st.rerun()

# --- ONGLET 2 : CAGNOTTE KDO ---
with tab_kdo:
    k_code = st.text_input("Code KDO :", placeholder="KDO-XXXX", disabled=st.session_state.processing).strip().upper()
    if st.button("🔎 VÉRIFIER LE BON", key="check_k", disabled=st.session_state.processing):
        st.session_state.ready_kdo = False
        with st.spinner('Vérification du bon...'):
            res_k = list(client.query(f"SELECT status, reward_value, expires_at, shopify_rule_id FROM `{PROJECT_ID}.shopify_data_eu.referral_codes` WHERE code = '{k_code}'"))
            if not res_k or res_k[0].status != 'ACTIVE': st.error("❌ INVALIDE OU DÉJÀ UTILISÉ")
            else:
                k = res_k[0]
                is_expired = True if k.expires_at and k.expires_at < datetime.now(k.expires_at.tzinfo) else False
                if is_expired: st.error(f"❌ REFUSÉ : Ce bon d'achat a expiré le {k.expires_at.strftime('%d/%m/%Y')}.")
                else:
                    st.success(f"✅ VALIDE : Déduire {k.reward_value}€")
                    st.session_state.ready_kdo = True
                    st.session_state.k_data = {"code": k_code, "val": k.reward_value, "rule_id": k.shopify_rule_id}

    if st.session_state.ready_kdo:
        d_k = st.session_state.k_data
        if st.button(f"✅ BRÛLER LE BON ({d_k['val']}€)", type="primary", on_click=start_processing, disabled=st.session_state.processing):
            with st.spinner('Brûlage du bon en cours...'):
                client.query(f"UPDATE `{PROJECT_ID}.shopify_data_eu.referral_codes` SET status = 'USED' WHERE code = '{d_k['code']}'").result()
                client.query(f"INSERT INTO `{PROJECT_ID}.shopify_data_eu.referral_redemptions` (referrer_id, referred_id, amount_rewarded, amount, store_location, redemption_date) VALUES ('{d_k['code']}', 'RETAIL_CLIENT', 0, {d_k['val']}, '{boutique_sel}', CURRENT_TIMESTAMP())").result()
                if d_k.get('rule_id'): delete_shopify_discount(d_k['rule_id'])
                st.session_state.success_msg = True
                st.rerun()

# --- ONGLET 3 : VIP CLUB LPL ---
with tab_vip:
    search_query = st.text_input("Recherche Client :", placeholder="Email ou Numéro de téléphone...", disabled=st.session_state.processing)

    if st.button("🔎 VÉRIFIER LE STATUT", type="primary", key="check_vip", disabled=st.session_state.processing):
        if not search_query:
            st.warning("⚠️ Veuillez entrer un email ou un numéro de téléphone.")
        else:
            with st.spinner('Recherche dans la base de données...'):
                search_val = search_query.strip().lower()
                
                query = f"""
                SELECT email, phone, is_lpl_club, lpl_club_expiry_date
                FROM `{PROJECT_ID}.shopify_data_eu.dim_unified_customers`
                WHERE LOWER(email) = @search_val OR phone = @search_val
                LIMIT 1
                """
                
                job_config = bigquery.QueryJobConfig(
                    query_parameters=[
                        bigquery.ScalarQueryParameter("search_val", "STRING", search_val)
                    ]
                )
                
                results = list(client.query(query, job_config=job_config))
                
                st.markdown("---")
                
                if not results:
                    st.error("❌ **INCONNU** : Aucun client trouvé avec ces informations.")
                else:
                    row = results[0]
                    identifiant = row.email if row.email else row.phone
                    st.markdown(f"<h3 style='text-align: center;'>👤 {identifiant}</h3>", unsafe_allow_html=True)
                    
                    if row.is_lpl_club and row.lpl_club_expiry_date and row.lpl_club_expiry_date >= date.today():
                        st.success("✅ **OUI ! Ce client est membre VIP.**")
                        st.info(f"✨ Avantages valables jusqu'au : **{row.lpl_club_expiry_date.strftime('%d/%m/%Y')}**")
                    else:
                        st.error("❌ **NON.** Ce client n'est pas VIP.")
                        
                        if row.lpl_club_expiry_date and row.lpl_club_expiry_date < date.today():
                            st.warning(f"ℹ️ Ancienne carte expirée depuis le : {row.lpl_club_expiry_date.strftime('%d/%m/%Y')}")
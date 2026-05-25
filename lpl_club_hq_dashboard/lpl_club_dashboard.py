import streamlit as st
from google.cloud import bigquery
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
import pandas as pd
import toml
import random
import smtplib
import altair as alt
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# --- CONFIGURATION ---
st.set_page_config(page_title="LPL Club HQ", page_icon="📈", layout="wide")
PROJECT_ID = "shopify-data-ltv"

try:
    secrets = toml.load(".streamlit/secrets.toml")
    KLAVIYO_API_KEY = secrets["klaviyo"]["api_key"]
    ALLOWED_EMAILS = secrets.get("allowed_emails", [])
    SMTP_USER = secrets["email"]["user"]
    SMTP_PWD = secrets["email"]["password"]
except Exception:
    import os
    KLAVIYO_API_KEY = os.environ.get("KLAVIYO_API_KEY", "")
    ALLOWED_EMAILS = os.environ.get("ALLOWED_EMAILS", "").split(",")
    SMTP_USER = os.environ.get("SMTP_USER", "")
    SMTP_PWD = os.environ.get("SMTP_PWD", "")

# --- FONCTION ENVOI EMAIL OTP ---
def send_otp_email(receiver_email, otp_code):
    try:
        msg = MIMEMultipart()
        msg['From'] = f"HQ LPL Club <{SMTP_USER}>"
        msg['To'] = receiver_email
        msg['Subject'] = "🔒 Votre code d'accès HQ LPL Club"
        
        body = f"""Bonjour,
        
Voici votre code de connexion à usage unique pour accéder au Dashboard HQ LPL Club : 

CODE : {otp_code}

Ce code est confidentiel.
L'équipe Tech."""
        
        msg.attach(MIMEText(body, 'plain'))
        
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(SMTP_USER, SMTP_PWD)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"Erreur SMTP: {e}")
        return False

# --- DESIGN CSS ---
def set_design():
    LOGO_URL = "https://cdn.shopify.com/s/files/1/1169/1934/files/Logo_Bleu_-_Square.png?v=1770911062"
    BG_URL = "https://cdn.shopify.com/s/files/1/1169/1934/files/1125-LPL-DoubleJeu-Homme_Femme-Ella-Ecaille-Ezra-Gris-LB-Remy-Marine-Photo-2_1.jpg?v=1770912269"
    st.markdown(f"""
    <style>
    .stApp {{ background-image: url("{BG_URL}"); background-size: cover; background-position: center; background-attachment: fixed; }}
    .block-container {{ background-color: rgba(255, 255, 255, 0.95); padding: 3rem 2rem !important; border-radius: 15px; box-shadow: 0 10px 40px rgba(0,0,0,0.1); margin-top: 2rem; }}
    
    /* Typographie allégée pour éviter de casser les graphiques */
    h1, h2, h3, h4, p, label, th, td {{ color: #1a1a1a !important; font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif !important; }}
    
    /* Fix spécifique pour l'infobulle (tooltip) du graphique */
    #vg-tooltip-element {{ background-color: #ffffff !important; border: 1px solid #cccccc !important; border-radius: 5px !important; box-shadow: 0 4px 6px rgba(0,0,0,0.1) !important; }}
    #vg-tooltip-element span, #vg-tooltip-element div, #vg-tooltip-element h2 {{ color: #1a1a1a !important; }}
    
    .logo-img {{ display: block; margin-left: auto; margin-right: auto; width: 140px; margin-bottom: 25px; }}
    .stTextInput input, div[data-baseweb="select"] > div, .stTextArea textarea {{ background-color: #ffffff !important; color: #1a1a1a !important; border-radius: 6px !important; border: 1px solid #cccccc !important; }}
    .stTextInput div[data-baseweb="input"]:focus-within, div[data-baseweb="select"]:focus-within > div, .stTextArea textarea:focus {{ border: 2px solid #0056b3 !important; box-shadow: 0 0 0 2px rgba(0, 86, 179, 0.2) !important; }}
    div.stButton > button {{ background-color: #1a1a1a !important; color: white !important; border-radius: 8px !important; width: 100% !important; height: 50px !important; font-weight: 600 !important; border: none !important; }}
    div.stButton > button p {{ color: white !important; }}
    div.stButton > button[kind="primary"] {{ background-color: #004494 !important; height: 60px !important; }}
    div.stButton > button[kind="primary"] p {{ font-size: 1.2rem !important; font-weight: 800 !important; color: white !important; }}
    div[data-testid="stMetricValue"] {{ font-size: 2.2rem !important; color: #004494 !important; font-weight: 800 !important; }}
    #MainMenu, footer, header {{visibility: hidden;}}
    </style>
    """, unsafe_allow_html=True)

set_design()

@st.cache_resource
def get_bq_client():
    return bigquery.Client(project=PROJECT_ID)

client = get_bq_client()

# ==========================================
# AUTHENTIFICATION OTP
# ==========================================
if 'auth_status' not in st.session_state:
    st.session_state.auth_status = 'login' 
if 'otp_code' not in st.session_state:
    st.session_state.otp_code = None

if st.session_state.auth_status != 'authenticated':
    st.markdown("<img src='https://cdn.shopify.com/s/files/1/1169/1934/files/Logo_Bleu_-_Square.png?v=1770911062' class='logo-img'>", unsafe_allow_html=True)
    
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        if st.session_state.auth_status == 'login':
            st.markdown("<h2 style='text-align: center;'>Accès HQ Restreint</h2>", unsafe_allow_html=True)
            email_input = st.text_input("Votre Email autorisé :")
            if st.button("Recevoir le code", type="primary"):
                if email_input.strip().lower() in ALLOWED_EMAILS:
                    st.session_state.otp_code = str(random.randint(100000, 999999))
                    with st.spinner("Envoi de l'email en cours..."):
                        success = send_otp_email(email_input.strip().lower(), st.session_state.otp_code)
                        if success:
                            st.success("Un code à 6 chiffres vient de vous être envoyé par email.")
                            st.session_state.auth_status = 'verify'
                            st.rerun()
                        else:
                            st.error("⚠️ Erreur lors de l'envoi de l'email. Vérifiez les identifiants SMTP.")
                else:
                    st.error("Accès refusé. Cet email n'est pas autorisé.")
                    
        elif st.session_state.auth_status == 'verify':
            st.markdown("<h2 style='text-align: center;'>Vérification OTP</h2>", unsafe_allow_html=True)
            code_input = st.text_input("Code à 6 chiffres reçu par email :")
            if st.button("Valider la connexion", type="primary"):
                if code_input.strip() == st.session_state.otp_code:
                    st.session_state.auth_status = 'authenticated'
                    st.rerun()
                else:
                    st.error("Code incorrect.")
    st.stop() 

# ==========================================
# DASHBOARD PRINCIPAL
# ==========================================
col_title, col_btn = st.columns([3, 1])
with col_title:
    st.markdown("<h1 style='text-align: left; font-size: 2.2rem;'>HQ - LPL CLUB 📈 (v2.1)</h1>", unsafe_allow_html=True)

with col_btn:
    st.write("") 
    if st.button("🔄 Actualiser la BDD", type="primary"):
        with st.spinner("Forçage de l'ETL BigQuery en cours..."):
            q_etl = f"""
            CREATE OR REPLACE TABLE `{PROJECT_ID}.shopify_data_eu.dim_unified_customers` AS
            WITH AllClients AS (
                SELECT LOWER(email) AS email, NULLIF(TRIM(phone), '') AS phone FROM `{PROJECT_ID}.shopify_data_eu.custom_transactions_history` WHERE email IS NOT NULL AND email != ''
                UNION DISTINCT
                SELECT LOWER(customer_email) AS email, NULLIF(TRIM(customer_mobile_phone), '') AS phone FROM `stable-splicer-294813.dwh_datasource_sales.transaction_details_visits` WHERE customer_email IS NOT NULL AND customer_email != ''
                UNION DISTINCT
                SELECT LOWER(email) AS email, NULL AS phone FROM `{PROJECT_ID}.shopify_data_eu.manual_lpl_club_members` WHERE email IS NOT NULL AND email != ''
            ),
            QualifyingOrders AS (
                SELECT LOWER(email) AS email, DATE(order_date) AS qualifying_date, 'WEB' AS source FROM `{PROJECT_ID}.shopify_data_eu.custom_transactions_history` WHERE LOWER(shipping_method) LIKE '%lpl club%' 
                UNION ALL
                SELECT LOWER(customer_email) AS email, CAST(invoice_creation_datetime AS DATE) AS qualifying_date, 'RETAIL' AS source FROM `stable-splicer-294813.dwh_datasource_sales.transaction_details_visits` WHERE UPPER(CAST(lplclub2026 AS STRING)) IN ('TRUE', '1', 'OUI', 'YES')
                UNION ALL
                SELECT LOWER(email) AS email, DATE(added_at) AS qualifying_date, 'MANUEL' AS source FROM `{PROJECT_ID}.shopify_data_eu.manual_lpl_club_members`
            ),
            LatestQualifying AS (
                SELECT email, MAX(qualifying_date) AS last_club_order_date, ARRAY_AGG(source ORDER BY qualifying_date DESC LIMIT 1)[OFFSET(0)] AS latest_source
                FROM QualifyingOrders WHERE email IS NOT NULL GROUP BY email
            )
            SELECT c.email, MAX(c.phone) AS phone, l.last_club_order_date, l.latest_source AS source,
                DATE_ADD(l.last_club_order_date, INTERVAL 1 YEAR) AS lpl_club_expiry_date,
                CASE WHEN l.last_club_order_date IS NOT NULL AND DATE_ADD(l.last_club_order_date, INTERVAL 1 YEAR) >= CURRENT_DATE() THEN TRUE ELSE FALSE END AS is_lpl_club
            FROM AllClients c
            LEFT JOIN LatestQualifying l ON c.email = l.email
            GROUP BY c.email, l.last_club_order_date, l.latest_source;
            """
            try:
                client.query(q_etl).result()
                st.success("✅ Base de données mise à jour avec succès !")
                st.rerun()
            except Exception as e:
                st.error(f"Erreur lors de l'actualisation : {e}")

st.divider()

tab_kpi, tab_add, tab_live = st.tabs(["📊 STATS & KPIS", "➕ AJOUT MANUEL LPL CLUB", "⚡ FLUX LIVE (30 derniers)"])

with tab_kpi:
    with st.spinner("Analyse des performances en cours..."):
        
        # --- REQUÊTE 1 : Actifs et Utilisations ---
        q_kpi_members = f"""
        WITH all_uses AS (
            SELECT LOWER(email) as email FROM `{PROJECT_ID}.shopify_data_eu.custom_transactions_history` WHERE discount_code LIKE '%LPL Club -10%'
            UNION DISTINCT
            SELECT LOWER(customer_email) as email FROM `stable-splicer-294813.dwh_datasource_sales.transaction_details_visits` WHERE article_code LIKE '%LPLCLUB%'
        ),
        active_members AS (
            SELECT LOWER(email) as email FROM `{PROJECT_ID}.shopify_data_eu.dim_unified_customers` WHERE is_lpl_club = TRUE
        )
        SELECT 
            (SELECT COUNT(*) FROM active_members) as total_active,
            (SELECT COUNT(DISTINCT u.email) FROM all_uses u JOIN active_members a ON u.email = a.email) as total_used
        """
        
        # --- REQUÊTE 2 : Taux de Recrutement (Web & Retail) ---
        q_kpi_recrut = f"""
        WITH web_stats AS (
            SELECT 
                COUNT(DISTINCT LOWER(email)) as web_tot_10w,
                COUNT(DISTINCT CASE WHEN DATE(order_date) >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY) THEN LOWER(email) END) as web_tot_7d,
                COUNT(DISTINCT CASE WHEN LOWER(shipping_method) LIKE '%lpl club%' THEN LOWER(email) END) as web_vip_10w,
                COUNT(DISTINCT CASE WHEN LOWER(shipping_method) LIKE '%lpl club%' AND DATE(order_date) >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY) THEN LOWER(email) END) as web_vip_7d
            FROM `{PROJECT_ID}.shopify_data_eu.custom_transactions_history`
            WHERE DATE(order_date) >= DATE_SUB(CURRENT_DATE(), INTERVAL 10 WEEK)
        ),
        retail_stats AS (
            SELECT 
                COUNT(DISTINCT LOWER(customer_email)) as ret_tot_10w,
                COUNT(DISTINCT CASE WHEN CAST(invoice_creation_datetime AS DATE) >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY) THEN LOWER(customer_email) END) as ret_tot_7d,
                COUNT(DISTINCT CASE WHEN UPPER(CAST(lplclub2026 AS STRING)) IN ('TRUE', '1', 'OUI', 'YES') THEN LOWER(customer_email) END) as ret_vip_10w,
                COUNT(DISTINCT CASE WHEN UPPER(CAST(lplclub2026 AS STRING)) IN ('TRUE', '1', 'OUI', 'YES') AND CAST(invoice_creation_datetime AS DATE) >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY) THEN LOWER(customer_email) END) as ret_vip_7d
            FROM `stable-splicer-294813.dwh_datasource_sales.transaction_details_visits`
            WHERE CAST(invoice_creation_datetime AS DATE) >= DATE_SUB(CURRENT_DATE(), INTERVAL 10 WEEK)
        )
        SELECT * FROM web_stats CROSS JOIN retail_stats
        """
        
        try:
            res_members = list(client.query(q_kpi_members))[0]
            res_recrut = list(client.query(q_kpi_recrut))[0]
            
            # Calculs Python sécurisés
            total_active = res_members.total_active
            tx_util = f"{(res_members.total_used / total_active * 100):.1f}%" if total_active > 0 else "0%"
            
            tx_web_10w = f"{(res_recrut.web_vip_10w / res_recrut.web_tot_10w * 100):.1f}%" if res_recrut.web_tot_10w > 0 else "0%"
            tx_web_7d = f"{(res_recrut.web_vip_7d / res_recrut.web_tot_7d * 100):.1f}%" if res_recrut.web_tot_7d > 0 else "0%"
            
            tx_ret_10w = f"{(res_recrut.ret_vip_10w / res_recrut.ret_tot_10w * 100):.1f}%" if res_recrut.ret_tot_10w > 0 else "0%"
            tx_ret_7d = f"{(res_recrut.ret_vip_7d / res_recrut.ret_tot_7d * 100):.1f}%" if res_recrut.ret_tot_7d > 0 else "0%"
            
            # Affichage Grille KPIs
            c1, c2, c3 = st.columns(3)
            c4, c5, c6 = st.columns(3)
            
            c1.metric("Total Membres Actifs", f"{total_active:,}".replace(',', ' '))
            c2.metric("Taux Recrutement ONLINE (10 sem.)", tx_web_10w)
            c3.metric("Taux Recrutement RETAIL (10 sem.)", tx_ret_10w)
            
            c4.metric("Taux d'utilisation global (Avantages)", tx_util)
            c5.metric("Taux Recrutement ONLINE (7 jours)", tx_web_7d)
            c6.metric("Taux Recrutement RETAIL (7 jours)", tx_ret_7d)

        except Exception as e:
            st.error(f"Erreur de calcul des KPIs : {e}")

        st.divider()
        
        # --- GRAPHIQUE 1 : Adhésions par semaine ---
        st.subheader("Adhésions LPL Club par semaine (10 dernières semaines)")
        q_adhesions_10w = f"""
        SELECT 
            CAST(DATE_TRUNC(last_club_order_date, ISOWEEK) AS STRING) AS semaine, 
            source, 
            COUNT(*) as count 
        FROM `{PROJECT_ID}.shopify_data_eu.dim_unified_customers` 
        WHERE is_lpl_club = TRUE 
          AND last_club_order_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 10 WEEK)
        GROUP BY semaine, source
        ORDER BY semaine ASC
        """
        try:
            df_adhesions = client.query(q_adhesions_10w).to_dataframe()
            if not df_adhesions.empty:
                st.bar_chart(df_adhesions, x='semaine', y='count', color='source')
            else:
                st.info("Aucune donnée d'adhésion sur les 10 dernières semaines.")
        except Exception as e:
            st.warning(f"Impossible de charger le graphique : {e}")

        st.divider()

        # --- LIGNE DU BAS : Camembert et Utilisations ---
        col_pie, col_bar = st.columns([1, 2])
        
        with col_pie:
            st.subheader("Origine (10 dern. semaines)")
            q_pie = f"""
            SELECT source, COUNT(*) as count 
            FROM `{PROJECT_ID}.shopify_data_eu.dim_unified_customers` 
            WHERE is_lpl_club = TRUE 
              AND source IS NOT NULL
              AND last_club_order_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 10 WEEK)
            GROUP BY source
            """
            try:
                df_pie = client.query(q_pie).to_dataframe()
                if not df_pie.empty:
                    chart = alt.Chart(df_pie).mark_arc(innerRadius=50).encode(
                        theta=alt.Theta(field="count", type="quantitative"),
                        color=alt.Color(field="source", type="nominal", legend=alt.Legend(title="Source")),
                        tooltip=['source', 'count']
                    ).configure_view(strokeWidth=0)
                    st.altair_chart(chart, use_container_width=True)
                else:
                    st.info("Données insuffisantes pour le graphique.")
            except Exception:
                st.warning("Erreur de chargement.")
        
        with col_bar:
            st.subheader("Utilisations Web vs Boutique par semaine (10 dern. semaines)")
            q_uses_10w = f"""
            WITH web_uses AS (
                SELECT CAST(DATE_TRUNC(DATE(order_date), ISOWEEK) AS STRING) as semaine, 'WEB' as canal, COUNT(*) as uses 
                FROM `{PROJECT_ID}.shopify_data_eu.custom_transactions_history` 
                WHERE discount_code LIKE '%LPL Club -10%' AND DATE(order_date) >= DATE_SUB(CURRENT_DATE(), INTERVAL 10 WEEK)
                GROUP BY semaine
            ),
            retail_uses AS (
                SELECT CAST(DATE_TRUNC(CAST(invoice_creation_datetime AS DATE), ISOWEEK) AS STRING) as semaine, 'BOUTIQUE' as canal, COUNT(*) as uses 
                FROM `stable-splicer-294813.dwh_datasource_sales.transaction_details_visits`
                WHERE article_code LIKE '%LPLCLUB%' AND CAST(invoice_creation_datetime AS DATE) >= DATE_SUB(CURRENT_DATE(), INTERVAL 10 WEEK)
                GROUP BY semaine
            )
            SELECT semaine, canal, SUM(uses) as total_uses
            FROM (SELECT * FROM web_uses UNION ALL SELECT * FROM retail_uses)
            GROUP BY semaine, canal
            ORDER BY semaine ASC
            """
            try:
                df_uses = client.query(q_uses_10w).to_dataframe()
                if not df_uses.empty:
                    st.bar_chart(df_uses, x='semaine', y='total_uses', color='canal')
                else:
                    st.info("Aucune donnée d'utilisation sur les 10 dernières semaines.")
            except Exception as e:
                st.warning(f"Erreur de chargement : {e}")

with tab_add:
    st.subheader("Créer un accès LPL Club manuellement")
    st.markdown("Le client sera ajouté à la base globale et ses avantages seront reconnus partout (Web et Caisse). Pensez à cliquer sur **Actualiser la BDD** en haut à droite après l'ajout.")
    
    with st.form("add_vip_form"):
        col_email, col_author = st.columns(2)
        vip_email = col_email.text_input("Email du client *", placeholder="client@email.com")
        added_by = col_author.text_input("Auteur de l'ajout (Votre nom) *", placeholder="Ex: Alexandre")
        notes = st.text_area("Notes / Motif", placeholder="Ex: Influenceur, Service Client...")
        
        if st.form_submit_button("Valider l'adhésion LPL Club", type="primary"):
            if not vip_email or not added_by:
                st.error("⚠️ L'email et le nom de l'auteur sont obligatoires.")
            else:
                with st.spinner("Ajout en cours..."):
                    email_clean = vip_email.strip().lower()
                    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    expiry_date = (date.today() + relativedelta(years=1)).strftime("%Y-%m-%d")
                    notes_clean = notes.replace("'", "''")
                    
                    q_insert = f"""
                    INSERT INTO `{PROJECT_ID}.shopify_data_eu.manual_lpl_club_members` 
                    (email, added_at, expiry_date, added_by, notes)
                    VALUES ('{email_clean}', TIMESTAMP('{now}'), DATE('{expiry_date}'), '{added_by}', '{notes_clean}')
                    """
                    try:
                        client.query(q_insert).result()
                        st.success(f"✅ {email_clean} a été ajouté avec succès !")
                        st.info(f"Ses avantages LPL Club sont valables jusqu'au : {expiry_date}")
                    except Exception as e:
                        st.error(f"Erreur technique de base de données : {e}")

with tab_live:
    col_l1, col_l2 = st.columns(2)
    with col_l1:
        st.subheader("Derniers ajouts manuels (Adhésions)")
        q_live = f"SELECT email, added_at, added_by FROM `{PROJECT_ID}.shopify_data_eu.manual_lpl_club_members` ORDER BY added_at DESC LIMIT 30"
        try:
            st.dataframe(client.query(q_live).to_dataframe(), use_container_width=True, hide_index=True)
        except Exception:
            st.warning("Aucun ajout récent.")
            
    with col_l2:
        st.subheader("Dernières adhésions globales")
        q_all = f"SELECT email, source, last_club_order_date FROM `{PROJECT_ID}.shopify_data_eu.dim_unified_customers` WHERE is_lpl_club = TRUE ORDER BY last_club_order_date DESC LIMIT 30"
        try:
            st.dataframe(client.query(q_all).to_dataframe(), use_container_width=True, hide_index=True)
        except Exception:
            st.warning("Aucune donnée disponible.")
            
    st.divider()
    
    st.subheader("Dernières utilisations de l'avantage LPL Club")
    st.markdown("Historique basé strictement sur le code promo (-10%) en ligne et l'article VIP en boutique.")
    
    q_live_uses = f"""
    WITH web_uses AS (
        SELECT 
            LOWER(email) AS email, 
            CAST(order_date AS TIMESTAMP) AS date_utilisation, 
            'WEB' AS canal,
            discount_code AS preuve_avantage
        FROM `{PROJECT_ID}.shopify_data_eu.custom_transactions_history` 
        WHERE discount_code LIKE '%LPL Club -10%'
    ),
    retail_uses AS (
        SELECT 
            LOWER(customer_email) AS email, 
            CAST(invoice_creation_datetime AS TIMESTAMP) AS date_utilisation, 
            'BOUTIQUE' AS canal,
            article_code AS preuve_avantage
        FROM `stable-splicer-294813.dwh_datasource_sales.transaction_details_visits`
        WHERE article_code LIKE '%LPLCLUB%'
    )
    SELECT email, date_utilisation, canal, preuve_avantage
    FROM (SELECT * FROM web_uses UNION ALL SELECT * FROM retail_uses)
    ORDER BY date_utilisation DESC
    LIMIT 30
    """
    try:
        df_live_uses = client.query(q_live_uses).to_dataframe()
        if not df_live_uses.empty:
            df_live_uses['date_utilisation'] = pd.to_datetime(df_live_uses['date_utilisation']).dt.strftime('%d/%m/%Y %H:%M')
            st.dataframe(df_live_uses, use_container_width=True, hide_index=True)
        else:
            st.info("Aucune utilisation récente enregistrée.")
    except Exception as e:
        st.warning(f"Impossible de récupérer l'historique des utilisations : {e}")
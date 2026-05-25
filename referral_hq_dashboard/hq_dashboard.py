import streamlit as st
from google.cloud import bigquery
from google.oauth2 import service_account
import random
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import altair as alt

# --- 1. CONFIGURATION ---
st.set_page_config(page_title="HQ Dashboard - Le Petit Lunetier", page_icon="📈", layout="wide", initial_sidebar_state="collapsed")

PROJECT_ID = "shopify-data-ltv"
RETAIL_PROJECT = "stable-splicer-294813"

# --- SÉCURITÉ : EMAILS AUTORISÉS ---
ALLOWED_EMAILS = [
    "info@lepetitlunetier.com", 
    "scf@lepetitlunetier.com", 
    "alexandre@lepetitlunetier.com", 
    "adrien@lepetitlunetier.com", 
    "strategenicsadvisorsllp@gmail.com",
    "lucile@lepetitlunetier.com",
    "tiphaine@lepetitlunetier.com",
]

# --- 2. GESTION DE L'ÉTAT ---
if 'hq_otp_code' not in st.session_state: st.session_state.hq_otp_code = None
if 'hq_authenticated' not in st.session_state: st.session_state.hq_authenticated = False
if 'hq_email' not in st.session_state: st.session_state.hq_email = ""

# --- 3. FONCTIONS UTILITAIRES ---

@st.cache_resource
def get_bq_client():
    """Initialise le client BigQuery natif (utilise le Service Account de Cloud Run)"""
    # Plus de try/except, on force la connexion Cloud Native
    return bigquery.Client(project=PROJECT_ID)

def send_otp(email_to, code):
    try:
        sender_email = st.secrets["email"]["user"]
        sender_password = st.secrets["email"]["password"]
        subject = "Accès HQ Dashboard - Le Petit Lunetier"
        body = f"Bonjour,\n\nVotre code d'accès au Dashboard HQ est : {code}\n\nCe code est valable 10 minutes.\n\nSécurité automatisée."
        msg = MIMEMultipart()
        msg['From'] = f"HQ Security <{sender_email}>"
        msg['To'] = email_to
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, email_to, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        st.error(f"Erreur SMTP: {e}")
        return False

# --- 4. DESIGN PREMIUM CORRIGÉ ---
def set_design():
    LOGO_URL = "https://cdn.shopify.com/s/files/1/1169/1934/files/Logo_Bleu_-_Square.png?v=1770911062"
    st.markdown(
        f"""
        <style>
        .stApp {{ background-color: #f4f6f9; }}
        .block-container {{ background-color: #ffffff; padding: 2rem 3rem !important; border-radius: 12px; box-shadow: 0 8px 30px rgba(0,0,0,0.06); max-width: 1300px; margin-top: 2rem; }}
        h1, h2, h3, h4, p, div, span, label {{ color: #1a1a1a !important; font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif !important; }}
        .logo-img {{ display: block; margin-left: auto; margin-right: auto; width: 100px; margin-bottom: 20px; }}
        
        /* Champs de texte et Placeholders */
        .stTextInput div[data-baseweb="input"] {{ border: 2px solid #e0e0e0 !important; border-radius: 8px !important; background-color: #ffffff !important; }}
        .stTextInput div[data-baseweb="input"]:focus-within {{ border: 2px solid #0056b3 !important; }}
        .stTextInput input {{ color: #1a1a1a !important; background-color: #ffffff !important; padding: 10px 15px !important; -webkit-text-fill-color: #1a1a1a !important; }}
        .stTextInput input::placeholder {{ color: #94a3b8 !important; -webkit-text-fill-color: #94a3b8 !important; opacity: 1 !important; }}
        
        /* L'Oeil du mot de passe */
        button[aria-label="Show password text"], button[aria-label="Hide password text"], button[title="Show password text"] {{ background-color: transparent !important; border: none !important; }}
        button[aria-label="Show password text"] svg, button[aria-label="Hide password text"] svg, button[title="Show password text"] svg {{ fill: #475569 !important; color: #475569 !important; }}
        
        /* La Toolbar des tableaux au survol */
        div[data-testid="stElementToolbar"] {{ background-color: #ffffff !important; border-radius: 6px !important; box-shadow: 0 2px 8px rgba(0,0,0,0.15) !important; }}
        button[kind="elementToolbar"] {{ background-color: transparent !important; color: #1a1a1a !important; }}
        button[kind="elementToolbar"] svg {{ fill: #475569 !important; color: #475569 !important; }}
        
        /* Boutons standards */
        div.stButton > button {{ background-color: #1a1a1a !important; border: none !important; border-radius: 8px !important; transition: all 0.3s ease !important; }}
        div.stButton > button p, div.stButton > button div, div.stButton > button span {{ color: #ffffff !important; font-weight: 600 !important; }}
        div.stButton > button:hover {{ background-color: #333333 !important; transform: translateY(-2px); }}
        div.stButton > button[kind="primary"] {{ background-color: #0056b3 !important; }}
        div.stButton > button[kind="primary"]:hover {{ background-color: #004494 !important; }}
        
        /* Cartes Métriques */
        div[data-testid="metric-container"] {{ background-color: #ffffff; border: 1px solid #e2e8f0; padding: 20px; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.02); transition: transform 0.2s; }}
        div[data-testid="metric-container"]:hover {{ transform: translateY(-2px); box-shadow: 0 6px 12px rgba(0,0,0,0.05); }}
        div[data-testid="metric-container"] > div:nth-child(1) > div {{ color: #64748b !important; font-weight: 600 !important; font-size: 0.9rem !important; text-transform: uppercase; letter-spacing: 0.5px; }} 
        div[data-testid="metric-container"] > div:nth-child(2) > div {{ color: #0056b3 !important; font-weight: 800 !important; font-size: 2rem !important; margin-top: 5px; }} 

        /* Onglets */
        .stTabs [data-baseweb="tab-list"] {{ gap: 20px; border-bottom: 2px solid #e2e8f0; padding-bottom: 0px; margin-bottom: 20px; }}
        .stTabs [data-baseweb="tab"] {{ height: 45px; font-size: 1.05rem !important; font-weight: 600 !important; color: #64748b !important; border: none; background-color: transparent; padding: 0 10px; }}
        .stTabs [aria-selected="true"] {{ color: #0056b3 !important; border-bottom: 3px solid #0056b3 !important; }}

        /* Tableaux */
        table {{ width: 100%; border-collapse: collapse; background-color: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.04); }}
        thead tr {{ background-color: #f8fafc !important; border-bottom: 2px solid #e2e8f0 !important; }}
        th {{ color: #475569 !important; font-weight: 700 !important; padding: 14px 16px !important; text-align: left !important; }}
        td {{ color: #1e293b !important; padding: 12px 16px !important; border-bottom: 1px solid #f1f5f9 !important; }}
        tbody tr:hover {{ background-color: #f1f5f9 !important; }}
        
        /* Boîtes d'infos */
        .info-box {{ background-color: #e0f2fe; border-left: 4px solid #0ea5e9; padding: 15px; border-radius: 4px; margin-bottom: 20px; }}
        .info-box p {{ color: #0369a1 !important; font-size: 0.95rem !important; margin: 0; }}
        
        #MainMenu, footer, header {{visibility: hidden;}}
        </style>
        """, unsafe_allow_html=True
    )
    col_l1, col_l2, col_l3 = st.columns([1,2,1])
    with col_l2:
        st.markdown(f'<img src="{LOGO_URL}" class="logo-img">', unsafe_allow_html=True)

set_design()

# =====================================================================
# ÉTAPE A : AUTHENTIFICATION HQ
# =====================================================================
if not st.session_state.hq_authenticated:
    st.markdown("<h1 style='text-align: center; margin-bottom: 30px;'>🔒 DASHBOARD HQ</h1>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        if st.session_state.hq_otp_code is None:
            email_input = st.text_input("Email collaborateur :", placeholder="prenom@lepetitlunetier.com").strip().lower()
            if st.button("Demander l'accès sécurisé", type="primary"):
                if email_input in ALLOWED_EMAILS:
                    code_otp = str(random.randint(100000, 999999))
                    if send_otp(email_input, code_otp):
                        st.session_state.hq_otp_code = code_otp
                        st.session_state.hq_email = email_input
                        st.success("Code envoyé !")
                        st.rerun()
                else:
                    st.error("Accès refusé. Cette adresse email n'est pas autorisée.")
        else:
            st.info(f"Code envoyé à **{st.session_state.hq_email}**")
            user_otp = st.text_input("Code de sécurité (6 chiffres) :", type="password", placeholder="123456")
            if st.button("Valider la connexion", type="primary"):
                if user_otp.strip() == st.session_state.hq_otp_code:
                    st.session_state.hq_authenticated = True
                    st.rerun()
                else:
                    st.error("Code incorrect.")

# =====================================================================
# ÉTAPE B : LE DASHBOARD HQ
# =====================================================================
if st.session_state.hq_authenticated:
    # --- INITIALISATION DU CLIENT (VIA CACHE) ---
    client = get_bq_client()
    job_config = bigquery.QueryJobConfig(use_query_cache=False)
    
    col_title, col_refresh, col_logout = st.columns([6, 1, 1])
    with col_title:
        st.markdown(f"<h2 style='margin-top: 0;'>Performance Parrainage & Fidélité</h2><p style='color: #64748b !important;'>Connecté en tant que : {st.session_state.hq_email}</p>", unsafe_allow_html=True)
    with col_refresh:
        if st.button("🔄 Actualiser"):
            st.rerun()
    with col_logout:
        if st.button("Déconnexion"):
            st.session_state.hq_authenticated = False
            st.session_state.hq_otp_code = None
            st.rerun()

    st.divider()

    with st.spinner("Analyse des bases de données et rapprochement des transactions..."):
        # --- 1. SANTÉ DES BASES DE DONNÉES ---
        q_health = f"""
            SELECT 
                (SELECT CAST(MAX(order_date) AS STRING) FROM `{PROJECT_ID}.shopify_data_eu.custom_transactions_history`) as last_ecom,
                (SELECT CAST(MAX(invoice_creation_datetime) AS STRING) FROM `{RETAIL_PROJECT}.dwh_datasource_sales.transaction_details_visits`) as last_retail
        """
        try:
            res_health = list(client.query(q_health, job_config=job_config))[0]
            last_ecom_date = datetime.strptime(res_health.last_ecom[:10], "%Y-%m-%d").strftime("%d/%m/%Y") if res_health.last_ecom else "Inconnu"
            last_retail_date = datetime.strptime(res_health.last_retail[:10], "%Y-%m-%d").strftime("%d/%m/%Y") if res_health.last_retail else "Inconnu"
        except Exception:
            last_ecom_date, last_retail_date = "Erreur", "Erreur"

        # --- 2. LOGIQUE DE RECOUPEMENT (ANTI DOUBLE-DÉCOMPTE VIA EXISTS) ---
        q_matching = f"""
            WITH retail_redemptions AS (
                SELECT 
                    referrer_id, 
                    LOWER(referred_id) as identifier, 
                    DATE(redemption_date) as redemp_date, 
                    amount, 
                    store_location,
                    CASE WHEN referrer_id LIKE 'LPL-%' THEN 'Acquisition' ELSE 'Fidélisation' END as code_type
                FROM `{PROJECT_ID}.shopify_data_eu.referral_redemptions`
                WHERE store_location NOT IN ('E-Commerce (Live)', 'SHOPIFY_ONLINE')
            ),
            sales_dates AS (
                SELECT DISTINCT LOWER(email) as email, phone, DATE(order_date) as sale_date FROM `{PROJECT_ID}.shopify_data_eu.custom_transactions_history`
                UNION DISTINCT
                SELECT DISTINCT LOWER(customer_email) as email, customer_mobile_phone as phone, DATE(invoice_creation_datetime) as sale_date FROM `{RETAIL_PROJECT}.dwh_datasource_sales.transaction_details_visits`
            ),
            matched_status AS (
                SELECT 
                    r.referrer_id, r.code_type, r.amount, r.store_location,
                    CASE 
                        WHEN r.code_type = 'Fidélisation' AND r.identifier = 'retail_client' THEN 1
                        WHEN EXISTS (
                            SELECT 1 FROM sales_dates s 
                            WHERE (r.identifier = s.email OR r.identifier = s.phone) 
                            AND s.sale_date BETWEEN r.redemp_date AND DATE_ADD(r.redemp_date, INTERVAL 2 DAY)
                        ) THEN 1 
                        ELSE 0 
                    END as is_matched
                FROM retail_redemptions r
            )
            SELECT 
                code_type,
                SUM(is_matched) as count_valid,
                SUM(CASE WHEN is_matched = 0 THEN 1 ELSE 0 END) as count_orphan,
                SUM(CASE WHEN is_matched = 1 THEN amount ELSE 0 END) as cost_valid
            FROM matched_status
            GROUP BY code_type
        """
        res_matching = list(client.query(q_matching, job_config=job_config))
        
        lpl_valid, lpl_orphan, lpl_cost_retail = 0, 0, 0.0
        kdo_valid, kdo_orphan, kdo_cost_retail = 0, 0, 0.0
        
        for row in res_matching:
            if row.code_type == 'Acquisition':
                lpl_valid = row.count_valid or 0
                lpl_orphan = row.count_orphan or 0
                lpl_cost_retail = row.cost_valid or 0.0
            else:
                kdo_valid = row.count_valid or 0
                kdo_orphan = row.count_orphan or 0
                kdo_cost_retail = row.cost_valid or 0.0

        # --- 3. TRACKING E-COMMERCE UNIFIÉ ---
        ecom_lpl, ecom_kdo, ecom_kdo_cost = 0, 0, 0.0
        try:
            q_ecom = f"""
                SELECT 
                    SUM(CASE WHEN discount_code LIKE '%LPL-%' THEN 1 ELSE 0 END) as ecom_lpl,
                    SUM(CASE WHEN discount_code LIKE '%KDO-%' THEN 1 ELSE 0 END) as ecom_kdo,
                    (
                        SELECT SUM(c.reward_value)
                        FROM `{PROJECT_ID}.shopify_data_eu.custom_transactions_history` t
                        JOIN `{PROJECT_ID}.shopify_data_eu.referral_codes` c
                          ON CONTAINS_SUBSTR(t.discount_code, c.code)
                        WHERE c.code LIKE 'KDO-%'
                    ) as ecom_kdo_cost
                FROM `{PROJECT_ID}.shopify_data_eu.custom_transactions_history`
            """
            res_ecom = list(client.query(q_ecom, job_config=job_config))[0]
            ecom_lpl = res_ecom.ecom_lpl or 0
            ecom_kdo = res_ecom.ecom_kdo or 0
            ecom_kdo_cost = res_ecom.ecom_kdo_cost or 0.0
        except Exception:
            pass 

        # --- 4. ROI LPL GLOBAL (ANTI DOUBLE-DÉCOMPTE DANS LE CA) ---
        q_ca = f"""
            WITH lpl_redemptions AS (
                SELECT DISTINCT LOWER(referred_id) as identifier, MIN(DATE(redemption_date)) as first_redemp_date
                FROM `{PROJECT_ID}.shopify_data_eu.referral_redemptions`
                WHERE referrer_id LIKE 'LPL-%' AND store_location NOT IN ('E-Commerce (Live)', 'SHOPIFY_ONLINE')
                GROUP BY 1
                
                UNION DISTINCT
                
                SELECT DISTINCT LOWER(email) as identifier, MIN(DATE(order_date)) as first_redemp_date
                FROM `{PROJECT_ID}.shopify_data_eu.custom_transactions_history`
                WHERE discount_code LIKE '%LPL-%'
                GROUP BY 1
            ),
            unique_lpl_customers AS (
                SELECT identifier, MIN(first_redemp_date) as first_redemp_date
                FROM lpl_redemptions
                GROUP BY identifier
            ),
            all_sales AS (
                SELECT LOWER(email) as email, phone, DATE(order_date) as sale_date, SUM(net_sales) as net_sales 
                FROM `{PROJECT_ID}.shopify_data_eu.custom_transactions_history`
                GROUP BY 1, 2, 3
                UNION ALL
                SELECT LOWER(customer_email) as email, customer_mobile_phone as phone, DATE(invoice_creation_datetime) as sale_date, SUM(ttc_net_sale_price) as net_sales 
                FROM `{RETAIL_PROJECT}.dwh_datasource_sales.transaction_details_visits`
                GROUP BY 1, 2, 3
            )
            SELECT 
                SUM(CASE WHEN s.sale_date BETWEEN r.first_redemp_date AND DATE_ADD(r.first_redemp_date, INTERVAL 1 DAY) THEN s.net_sales ELSE 0 END) as ca_immediat,
                SUM(s.net_sales) as ca_ltv
            FROM unique_lpl_customers r
            JOIN all_sales s ON (r.identifier = s.email OR r.identifier = s.phone)
            WHERE s.sale_date >= r.first_redemp_date
        """
        try:
            res_ca = list(client.query(q_ca, job_config=job_config))[0]
            ca_immediat = res_ca.ca_immediat or 0.0
            ca_ltv = res_ca.ca_ltv or 0.0
        except Exception:
            ca_immediat, ca_ltv = 0.0, 0.0

        # Calculs Globaux Combinés
        total_lpl_valid = lpl_valid + ecom_lpl
        total_lpl_cost = lpl_cost_retail + (ecom_lpl * 10.0)
        total_kdo_cost = kdo_cost_retail + ecom_kdo_cost
        
        cac = total_lpl_cost / total_lpl_valid if total_lpl_valid > 0 else 0
        roi_ltv = ((ca_ltv - total_lpl_cost) / total_lpl_cost * 100) if total_lpl_cost > 0 else 0

    # --- AFFICHAGE DES TABS ---
    tab_overview, tab_lpl, tab_kdo, tab_stores, tab_live = st.tabs([
        "🌐 Vue d'Ensemble & Data", 
        "👥 Acquisition (Filleuls LPL)", 
        "🎁 Fidélisation (Cagnottes KDO)",
        "🏬 Analyse Boutiques",
        "🕵️‍♂️ Suivi Live (CS)"
    ])

    with tab_overview:
        st.markdown("<br>", unsafe_allow_html=True)
        col_db1, col_db2 = st.columns(2)
        col_db1.info(f"🛒 **Base E-commerce :** Synchronisée jusqu'au {last_ecom_date}")
        col_db2.info(f"🏬 **Base Retail :** Synchronisée jusqu'au {last_retail_date}")
        
        st.markdown("<div class='info-box'><p><b>Comment fonctionne le recoupement ?</b><br>Le système fusionne désormais les ventes web et boutiques avec précision. Les données e-commerce s'appuient directement sur l'historique Shopify, évitant ainsi tout doublon avec les webhooks. Les transactions orphelines ne concernent que les caisses physiques.</p></div>", unsafe_allow_html=True)

        st.markdown("#### 🚨 Focus sur les Transactions Orphelines (Retail uniquement)")
        
        col_o1, col_o2, col_o3 = st.columns(3)
        col_o1.error(f"{lpl_orphan} Filleuls LPL Orphelins")
        col_o2.error(f"{kdo_orphan} Cagnottes KDO Orphelines")
        col_o3.metric("Pertes théoriques évitées", f"{(lpl_orphan + kdo_orphan)*10} €")

    with tab_lpl:
        st.markdown("<br>", unsafe_allow_html=True)
        col_l1, col_l2, col_l3 = st.columns(3)
        col_l1.metric("Filleuls Convertis (Retail)", f"{lpl_valid}")
        col_l2.metric("Filleuls Convertis (E-com)", f"{ecom_lpl}")
        col_l3.metric("Remises totales absorbées", f"{total_lpl_cost:,.0f} €".replace(',', ' '))
        
        st.markdown("#### 📈 R.O.I & Valeur Client (LTV)")
        col_r1, col_r2, col_r3 = st.columns(3)
        col_r1.metric("CA Généré (Vision LTV Globale)", f"{ca_ltv:,.0f} €".replace(',', ' '))
        col_r2.metric("C.A.C Global (Coût d'Acquisition)", f"{cac:.2f} €")
        col_r3.metric("R.O.I. Global", f"{roi_ltv:.1f} %")
        st.caption("Calcul du R.O.I unifié : \n$$ROI=\\frac{CA_{généré}-Coût_{remises}}{Coût_{remises}}\\times100$$")

    with tab_kdo:
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("<div class='info-box'><p><b>Note sur l'E-commerce :</b> Le montant exact de chaque cagnotte KDO dépensée en ligne est automatiquement recalculé en croisant l'historique de la commande avec la base de données des codes générés.</p></div>", unsafe_allow_html=True)
        
        col_k1, col_k2, col_k3 = st.columns(3)
        col_k1.metric("Cagnottes Encaissées (Retail)", f"{kdo_valid}")
        col_k2.metric("Cagnottes Encaissées (E-com)", f"{ecom_kdo}")
        col_k3.metric("Cashback global reversé", f"{total_kdo_cost:,.0f} €".replace(',', ' '))

    with tab_stores:
        st.markdown("<br>", unsafe_allow_html=True)
        q_stores = f"""
            SELECT 
                store_location as Boutique,
                SUM(CASE WHEN referrer_id LIKE 'LPL-%' THEN 1 ELSE 0 END) as Filleuls_LPL_Saisis,
                SUM(CASE WHEN referrer_id LIKE 'KDO-%' THEN 1 ELSE 0 END) as Cagnottes_KDO_Saisies,
                SUM(amount) as Remises_Totales_Saisies
            FROM `{PROJECT_ID}.shopify_data_eu.referral_redemptions`
            WHERE store_location NOT IN ('E-Commerce (Live)', 'SHOPIFY_ONLINE')
            GROUP BY store_location
            ORDER BY Remises_Totales_Saisies DESC
        """
        df_stores = client.query(q_stores, job_config=job_config).to_dataframe()
        
        if not df_stores.empty:
            st.table(df_stores.set_index('Boutique'))
            
            st.markdown("<br>#### Répartition des usages par boutique", unsafe_allow_html=True)
            df_chart = df_stores[['Boutique', 'Filleuls_LPL_Saisis', 'Cagnottes_KDO_Saisies']].melt(id_vars='Boutique', var_name='Type', value_name='Volume')
            
            chart = alt.Chart(df_chart).mark_bar().encode(
                x=alt.X('Boutique:N', axis=alt.Axis(labelAngle=-45, labelColor='#475569', title=None)),
                y=alt.Y('Volume:Q', axis=alt.Axis(labelColor='#475569', gridColor='#f1f5f9')),
                color=alt.Color('Type:N', scale=alt.Scale(domain=['Filleuls_LPL_Saisis', 'Cagnottes_KDO_Saisies'], range=['#0ea5e9', '#f59e0b'])),
                tooltip=['Boutique', 'Type', 'Volume']
            ).properties(background='#ffffff').configure_legend(labelColor='#475569', titleColor='#1a1a1a', orient='top').configure_view(strokeWidth=0)
            
            st.altair_chart(chart, use_container_width=True)
        else:
            st.info("Aucune donnée boutique n'a été enregistrée pour le moment.")
            
    with tab_live:
        st.markdown("<br>#### 🚦 Dernières utilisations de codes (Temps Réel)", unsafe_allow_html=True)
        st.markdown("<div class='info-box'><p><b>Service Client :</b> Ce tableau regroupe parfaitement les usages web et retail sans doublons. Un code <b>Orphelin</b> (Retail uniquement) signifie qu'il a été scanné en caisse boutique mais qu'aucune vente n'a encore été trouvée dans le logiciel.</p></div>", unsafe_allow_html=True)
        
        # UTILISATION DE EXISTS POUR ÉVITER LE DOUBLE-DÉCOMPTE DANS LE SUIVI LIVE
        q_live = f"""
            WITH retail_usages AS (
                SELECT 
                    CAST(r.redemption_date AS TIMESTAMP) as Date_Usage,
                    LOWER(r.referred_id) as Client,
                    r.referrer_id as Code_Utilise,
                    CASE WHEN r.referrer_id LIKE 'LPL-%' THEN 'Acquisition (LPL)' ELSE 'Cashback (KDO)' END as Type,
                    r.store_location as Canal,
                    CASE 
                        WHEN r.referrer_id LIKE 'KDO-%' AND LOWER(r.referred_id) = 'retail_client' THEN '✅ Validé (KDO Retail)'
                        WHEN EXISTS (
                            SELECT 1 
                            FROM `{RETAIL_PROJECT}.dwh_datasource_sales.transaction_details_visits` s
                            WHERE (LOWER(r.referred_id) = LOWER(s.customer_email) OR r.referred_id = s.customer_mobile_phone)
                            AND DATE(s.invoice_creation_datetime) BETWEEN DATE(r.redemption_date) AND DATE_ADD(DATE(r.redemption_date), INTERVAL 2 DAY)
                        ) THEN '✅ Validé (Achat Trouvé)' 
                        ELSE '⚠️ Orphelin' 
                    END as Statut
                FROM `{PROJECT_ID}.shopify_data_eu.referral_redemptions` r
                WHERE r.store_location NOT IN ('E-Commerce (Live)', 'SHOPIFY_ONLINE')
            ),
            ecom_usages AS (
                SELECT
                    CAST(order_date AS TIMESTAMP) as Date_Usage,
                    LOWER(email) as Client,
                    discount_code as Code_Utilise,
                    CASE WHEN discount_code LIKE '%LPL-%' THEN 'Acquisition (LPL)' ELSE 'Cashback (KDO)' END as Type,
                    'E-commerce' as Canal,
                    '✅ Validé (E-com)' as Statut
                FROM `{PROJECT_ID}.shopify_data_eu.custom_transactions_history`
                WHERE discount_code LIKE '%LPL-%' OR discount_code LIKE '%KDO-%'
            )
            SELECT * FROM retail_usages
            UNION ALL
            SELECT * FROM ecom_usages
            ORDER BY Date_Usage DESC
            LIMIT 100
        """
        try:
            df_live = client.query(q_live, job_config=job_config).to_dataframe()
            if not df_live.empty:
                df_live['Date_Usage'] = df_live['Date_Usage'].dt.strftime('%d/%m/%Y %H:%M')
                st.dataframe(df_live, use_container_width=True, hide_index=True)
            else:
                st.info("Aucune utilisation récente trouvée dans l'historique.")
        except Exception as e:
            st.error(f"Erreur lors du chargement des données en temps réel : {e}")
import os
import streamlit as st
from google.cloud import bigquery
from datetime import date

# --- CONFIGURATION DE LA PAGE ---
PROJECT_ID = "shopify-data-ltv"
st.set_page_config(page_title="LPL Club - Vérification VIP", page_icon="🕶️", layout="centered")

def set_design():
    LOGO_URL = "https://cdn.shopify.com/s/files/1/1169/1934/files/Logo_Bleu_-_Square.png?v=1770911062"
    BG_URL = "https://cdn.shopify.com/s/files/1/1169/1934/files/1125-LPL-DoubleJeu-Homme_Femme-Ella-Ecaille-Ezra-Gris-LB-Remy-Marine-Photo-2_1.jpg?v=1770912269"
    st.markdown(f"""
    <style>
    .stApp {{ background-image: url("{BG_URL}"); background-size: cover; background-position: center; background-attachment: fixed; }}
    .block-container {{ background-color: rgba(255, 255, 255, 0.95); padding: 3rem 2rem !important; border-radius: 15px; box-shadow: 0 10px 40px rgba(0,0,0,0.1); max-width: 650px; margin-top: 2rem; }}
    h1, h2, h3, h4, p, div, span, label {{ color: #1a1a1a !important; font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif !important; }}
    .logo-img {{ display: block; margin-left: auto; margin-right: auto; width: 140px; margin-bottom: 25px; }}
    .stTextInput div[data-baseweb="input"]:focus-within, div[data-baseweb="select"]:focus-within > div {{ border: 2px solid #0056b3 !important; box-shadow: 0 0 0 2px rgba(0, 86, 179, 0.2) !important; }}
    .stTextInput input, div[data-baseweb="select"] > div, ul[data-baseweb="menu"], li[role="option"] {{ color: #1a1a1a !important; background-color: #ffffff !important; border-radius: 6px !important; }}
    div.stButton > button {{ background-color: #1a1a1a !important; color: white !important; border-radius: 8px !important; width: 100% !important; height: 50px !important; font-weight: 600 !important; border: none !important; }}
    div.stButton > button p {{ color: white !important; }}
    div.stButton > button[kind="primary"] {{ background-color: #004494 !important; height: 75px !important; }}
    div.stButton > button[kind="primary"] p {{ font-size: 1.3rem !important; font-weight: 800 !important; color: white !important; }}
    #MainMenu, footer, header {{visibility: hidden;}}
    </style>
    <img src="{LOGO_URL}" class="logo-img">
    """, unsafe_allow_html=True)

# Application du design
set_design()

# --- INITIALISATION BIGQUERY ---
@st.cache_resource
def get_bq_client():
    return bigquery.Client(project=PROJECT_ID)

client = get_bq_client()

# --- INTERFACE UTILISATEUR ---
st.markdown("<h1 style='text-align: center; font-size: 2rem;'>VÉRIFICATION VIP LPL</h1>", unsafe_allow_html=True)
st.divider()

search_query = st.text_input("Recherche Client :", placeholder="Email ou Numéro de téléphone...")

# Bouton d'action principal
if st.button("🔎 VÉRIFIER LE STATUT", type="primary"):
    if not search_query:
        st.warning("⚠️ Veuillez entrer un email ou un numéro de téléphone.")
    else:
        with st.spinner('Recherche dans la base de données...'):
            # Nettoyage de la saisie
            search_val = search_query.strip().lower()
            
            # Requête sécurisée avec paramètres
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
            
            st.markdown("---") # Séparateur de résultats
            
            # --- AFFICHAGE DES RÉSULTATS ---
            if not results:
                st.error("❌ **INCONNU** : Aucun client trouvé avec ces informations.")
            else:
                row = results[0]
                identifiant = row.email if row.email else row.phone
                st.markdown(f"<h3 style='text-align: center;'>👤 {identifiant}</h3>", unsafe_allow_html=True)
                
                # Vérification de l'abonnement
                if row.is_lpl_club and row.lpl_club_expiry_date and row.lpl_club_expiry_date >= date.today():
                    st.success("✅ **OUI ! Ce client est membre VIP.**")
                    st.info(f"✨ Avantages valables jusqu'au : **{row.lpl_club_expiry_date.strftime('%d/%m/%Y')}**")
                else:
                    st.error("❌ **NON.** Ce client n'est pas VIP.")
                    
                    # Petit bonus : si le client a été VIP dans le passé, on l'affiche pour le vendeur
                    if row.lpl_club_expiry_date and row.lpl_club_expiry_date < date.today():
                        st.warning(f"ℹ️ Ancienne carte expirée depuis le : {row.lpl_club_expiry_date.strftime('%d/%m/%Y')}")
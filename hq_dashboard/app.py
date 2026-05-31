import os
import json
import requests
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, date, timedelta
from functools import wraps
from flask import Flask, redirect, request, session, jsonify, render_template
from google.cloud import bigquery

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-production-please")
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("FLASK_ENV") != "development"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)

PROJECT_ID = "shopify-data-ltv"
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
REDIRECT_URI = os.environ.get("REDIRECT_URI", "http://localhost:8080/auth/callback")
ALLOWED_DOMAIN = "lepetitlunetier.com"
SHOPIFY_STORE = os.environ.get("SHOPIFY_STORE", "")
SHOPIFY_TOKEN = os.environ.get("SHOPIFY_TOKEN", "")

bq_client = bigquery.Client(project=PROJECT_ID)

# Filtres qualité données — appliqués à toutes les requêtes
# custom_transactions_history : exclut les 0€ et remboursements complets (annulées déjà exclues à l'ETL)
CTH_VALID = "net_sales > 0"
# transaction_details_visits : exclut les visites supprimées et les lignes créditées/remboursées
TDV_VALID = "visit_is_deleted = 0 AND credit_note_invoice_id IS NULL"
# Idem, pour les requêtes où la table est aliasée en `t`
TDV_VALID_T = "t.visit_is_deleted = 0 AND t.credit_note_invoice_id IS NULL"


def shopify_order_count(days):
    """
    Nombre de commandes web payées (non annulées, non remboursées) sur les N derniers jours.
    - financial_status=paid  : exclut refunded, voided, partially_refunded
    - status=open + closed   : exclut les commandes annulées (status=cancelled)
    Les deux statuts (open = en cours, closed = traitée) couvrent toutes les commandes réelles.
    """
    if not SHOPIFY_STORE or not SHOPIFY_TOKEN:
        return 0
    try:
        min_date = (date.today() - timedelta(days=days)).isoformat()
        headers = {"X-Shopify-Access-Token": SHOPIFY_TOKEN}
        url = f"https://{SHOPIFY_STORE}/admin/api/2025-04/orders/count.json"
        total = 0
        for status in ("open", "closed"):
            r = requests.get(
                url,
                params={"status": status, "financial_status": "paid", "created_at_min": min_date},
                headers=headers,
                timeout=5,
            )
            if r.status_code == 200:
                total += r.json().get("count", 0)
        return total
    except Exception:
        pass
    return 0


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_email" not in session:
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated


def run_bq(query):
    return list(bq_client.query(query).result())


# ---------------------------------------------------------------------------
# AUTH
# ---------------------------------------------------------------------------

@app.route("/")
@login_required
def index():
    return render_template(
        "dashboard.html",
        user_email=session["user_email"],
        user_name=session.get("user_name", ""),
    )


@app.route("/login")
def login():
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "hd": ALLOWED_DOMAIN,
        "access_type": "online",
        "prompt": "select_account",
    }
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
    return render_template("login.html", auth_url=url)


@app.route("/auth/callback")
def auth_callback():
    code = request.args.get("code")
    if not code:
        return redirect("/login")

    token_resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
        },
        timeout=10,
    )

    if token_resp.status_code != 200:
        return render_template("login.html", error="Erreur OAuth. Réessayez.", auth_url="/login")

    access_token = token_resp.json().get("access_token")
    userinfo = requests.get(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    ).json()

    email = userinfo.get("email", "").lower()
    if not email.endswith(f"@{ALLOWED_DOMAIN}"):
        return render_template(
            "login.html",
            error=f"Accès refusé. Seuls les comptes @{ALLOWED_DOMAIN} sont autorisés.",
            auth_url="/login",
        )

    session.permanent = True
    session["user_email"] = email
    session["user_name"] = userinfo.get("name", email)
    return redirect("/")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


# ---------------------------------------------------------------------------
# API — LPL CLUB
# ---------------------------------------------------------------------------

@app.route("/api/lpl/kpis")
@login_required
def lpl_kpis():
    q_members = f"""
    WITH web_adhesions AS (
        SELECT email, MIN(adhesion_date) AS first_adhesion FROM (
            SELECT LOWER(email) AS email, DATE(created_at) AS adhesion_date
            FROM `{PROJECT_ID}.shopify_data_eu.lpl_club_web_orders`
            UNION ALL
            SELECT LOWER(email) AS email, DATE(order_date) AS adhesion_date
            FROM `{PROJECT_ID}.shopify_data_eu.custom_transactions_history`
            WHERE LOWER(shipping_method) LIKE '%lpl club%'
              AND {CTH_VALID}
        ) GROUP BY email
    ),
    retail_adhesions AS (
        SELECT LOWER(customer_email) AS email,
               MIN(CAST(invoice_creation_datetime AS DATE)) AS first_adhesion
        FROM `stable-splicer-294813.dwh_datasource_sales.transaction_details_visits`
        WHERE UPPER(CAST(lplclub2026 AS STRING)) IN ('TRUE', '1', 'OUI', 'YES')
          AND CAST(invoice_creation_datetime AS DATE) >= '2026-03-20'
          AND {TDV_VALID}
        GROUP BY customer_email
    ),
    active_members AS (
        SELECT LOWER(email) AS email
        FROM `{PROJECT_ID}.shopify_data_eu.dim_unified_customers`
        WHERE is_lpl_club = TRUE
    ),
    all_uses AS (
        SELECT DISTINCT LOWER(email) AS email
        FROM `{PROJECT_ID}.shopify_data_eu.lpl_club_web_uses`
        UNION DISTINCT
        SELECT LOWER(t.customer_email) AS email
        FROM `stable-splicer-294813.dwh_datasource_sales.transaction_details_visits` t
        JOIN retail_adhesions a ON LOWER(t.customer_email) = a.email
        WHERE t.article_code LIKE '%LPLCLUB%'
          AND CAST(t.invoice_creation_datetime AS DATE) > a.first_adhesion
          AND {TDV_VALID_T}
    )
    SELECT
        (SELECT COUNT(*) FROM active_members) AS total_active,
        (SELECT COUNT(DISTINCT u.email) FROM all_uses u JOIN active_members a ON u.email = a.email) AS total_used,
        (SELECT COUNT(DISTINCT email) FROM web_adhesions
         WHERE DATE_ADD(first_adhesion, INTERVAL 1 YEAR) >= CURRENT_DATE()) AS web_active,
        (SELECT COUNT(DISTINCT email) FROM retail_adhesions
         WHERE DATE_ADD(first_adhesion, INTERVAL 1 YEAR) >= CURRENT_DATE()) AS retail_active,
        (SELECT COUNT(DISTINCT email) FROM `{PROJECT_ID}.shopify_data_eu.lpl_club_web_uses`) AS web_used,
        (SELECT COUNT(DISTINCT LOWER(t.customer_email))
         FROM `stable-splicer-294813.dwh_datasource_sales.transaction_details_visits` t
         JOIN retail_adhesions a ON LOWER(t.customer_email) = a.email
         WHERE t.article_code LIKE '%LPLCLUB%'
           AND CAST(t.invoice_creation_datetime AS DATE) > a.first_adhesion
           AND {TDV_VALID_T}) AS retail_used
    """

    q_recrut = f"""
    WITH
    -- Toutes les commandes web (dénominateur)
    web_all AS (
        SELECT LOWER(email) AS email, DATE(order_date) AS d
        FROM `{PROJECT_ID}.shopify_data_eu.custom_transactions_history`
        WHERE DATE(order_date) >= DATE_SUB(CURRENT_DATE(), INTERVAL 10 WEEK)
          AND {CTH_VALID}
    ),
    -- Adhésions web : ancienne méthode (shipping) + nouvelle méthode (produit)
    web_vip AS (
        SELECT LOWER(email) AS email, DATE(order_date) AS d
        FROM `{PROJECT_ID}.shopify_data_eu.custom_transactions_history`
        WHERE LOWER(shipping_method) LIKE '%lpl club%'
          AND DATE(order_date) >= DATE_SUB(CURRENT_DATE(), INTERVAL 10 WEEK)
          AND {CTH_VALID}
        UNION DISTINCT
        SELECT LOWER(email) AS email, DATE(created_at) AS d
        FROM `{PROJECT_ID}.shopify_data_eu.lpl_club_web_orders`
        WHERE DATE(created_at) >= DATE_SUB(CURRENT_DATE(), INTERVAL 10 WEEK)
    ),
    web_stats AS (
        SELECT
            (SELECT COUNT(DISTINCT email) FROM web_all) AS web_tot_10w,
            (SELECT COUNT(DISTINCT email) FROM web_all WHERE d >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)) AS web_tot_7d,
            (SELECT COUNT(DISTINCT email) FROM web_vip) AS web_vip_10w,
            (SELECT COUNT(DISTINCT email) FROM web_vip WHERE d >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)) AS web_vip_7d
    ),
    retail_stats AS (
        SELECT
            COUNT(DISTINCT LOWER(customer_email)) AS ret_tot_10w,
            COUNT(DISTINCT CASE WHEN CAST(invoice_creation_datetime AS DATE) >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY) THEN LOWER(customer_email) END) AS ret_tot_7d,
            COUNT(DISTINCT CASE WHEN UPPER(CAST(lplclub2026 AS STRING)) IN ('TRUE', '1', 'OUI', 'YES') THEN LOWER(customer_email) END) AS ret_vip_10w,
            COUNT(DISTINCT CASE WHEN UPPER(CAST(lplclub2026 AS STRING)) IN ('TRUE', '1', 'OUI', 'YES') AND CAST(invoice_creation_datetime AS DATE) >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY) THEN LOWER(customer_email) END) AS ret_vip_7d
        FROM `stable-splicer-294813.dwh_datasource_sales.transaction_details_visits`
        WHERE CAST(invoice_creation_datetime AS DATE) >= DATE_SUB(CURRENT_DATE(), INTERVAL 10 WEEK)
          AND {TDV_VALID}
    )
    SELECT * FROM web_stats CROSS JOIN retail_stats
    """

    # Tendances 7j : nouvelles adhésions et utilisateurs actifs, période courante vs précédente.
    # Fenêtres complètes (hors aujourd'hui) pour une comparaison cohérente S vs S-1.
    q_trends = f"""
    SELECT
        -- Nouvelles adhésions WEB (7j complets, hors aujourd'hui)
        (SELECT COUNT(DISTINCT email) FROM (
            SELECT LOWER(email) AS email
            FROM `{PROJECT_ID}.shopify_data_eu.lpl_club_web_orders`
            WHERE DATE(created_at) >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
              AND DATE(created_at) < CURRENT_DATE()
            UNION DISTINCT
            SELECT LOWER(email) AS email
            FROM `{PROJECT_ID}.shopify_data_eu.custom_transactions_history`
            WHERE LOWER(shipping_method) LIKE '%lpl club%' AND {CTH_VALID}
              AND DATE(order_date) >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
              AND DATE(order_date) < CURRENT_DATE()
        )) AS new_web_7d,

        (SELECT COUNT(DISTINCT email) FROM (
            SELECT LOWER(email) AS email
            FROM `{PROJECT_ID}.shopify_data_eu.lpl_club_web_orders`
            WHERE DATE(created_at) >= DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY)
              AND DATE(created_at) < DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
            UNION DISTINCT
            SELECT LOWER(email) AS email
            FROM `{PROJECT_ID}.shopify_data_eu.custom_transactions_history`
            WHERE LOWER(shipping_method) LIKE '%lpl club%' AND {CTH_VALID}
              AND DATE(order_date) >= DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY)
              AND DATE(order_date) < DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
        )) AS new_web_prev7,

        -- Nouvelles adhésions RETAIL
        (SELECT COUNT(DISTINCT LOWER(customer_email))
         FROM `stable-splicer-294813.dwh_datasource_sales.transaction_details_visits`
         WHERE UPPER(CAST(lplclub2026 AS STRING)) IN ('TRUE', '1', 'OUI', 'YES')
           AND {TDV_VALID}
           AND CAST(invoice_creation_datetime AS DATE) >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
           AND CAST(invoice_creation_datetime AS DATE) < CURRENT_DATE()) AS new_retail_7d,

        (SELECT COUNT(DISTINCT LOWER(customer_email))
         FROM `stable-splicer-294813.dwh_datasource_sales.transaction_details_visits`
         WHERE UPPER(CAST(lplclub2026 AS STRING)) IN ('TRUE', '1', 'OUI', 'YES')
           AND {TDV_VALID}
           AND CAST(invoice_creation_datetime AS DATE) >= DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY)
           AND CAST(invoice_creation_datetime AS DATE) < DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)) AS new_retail_prev7,

        -- Utilisateurs actifs WEB (ont utilisé le discount LPL Club -10%)
        (SELECT COUNT(DISTINCT LOWER(email))
         FROM `{PROJECT_ID}.shopify_data_eu.lpl_club_web_uses`
         WHERE DATE(created_at) >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
           AND DATE(created_at) < CURRENT_DATE()) AS use_web_7d,

        (SELECT COUNT(DISTINCT LOWER(email))
         FROM `{PROJECT_ID}.shopify_data_eu.lpl_club_web_uses`
         WHERE DATE(created_at) >= DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY)
           AND DATE(created_at) < DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)) AS use_web_prev7,

        -- Utilisateurs actifs RETAIL (article LPLCLUB après la date d'adhésion)
        (SELECT COUNT(DISTINCT LOWER(t.customer_email))
         FROM `stable-splicer-294813.dwh_datasource_sales.transaction_details_visits` t
         JOIN (
             SELECT LOWER(customer_email) AS email,
                    MIN(CAST(invoice_creation_datetime AS DATE)) AS adhesion_date
             FROM `stable-splicer-294813.dwh_datasource_sales.transaction_details_visits`
             WHERE UPPER(CAST(lplclub2026 AS STRING)) IN ('TRUE', '1', 'OUI', 'YES')
               AND CAST(invoice_creation_datetime AS DATE) >= '2026-03-20'
               AND {TDV_VALID}
             GROUP BY customer_email
         ) a ON LOWER(t.customer_email) = a.email
         WHERE t.article_code LIKE '%LPLCLUB%'
           AND CAST(t.invoice_creation_datetime AS DATE) > a.adhesion_date
           AND CAST(t.invoice_creation_datetime AS DATE) >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
           AND CAST(t.invoice_creation_datetime AS DATE) < CURRENT_DATE()
           AND {TDV_VALID_T}) AS use_retail_7d,

        (SELECT COUNT(DISTINCT LOWER(t.customer_email))
         FROM `stable-splicer-294813.dwh_datasource_sales.transaction_details_visits` t
         JOIN (
             SELECT LOWER(customer_email) AS email,
                    MIN(CAST(invoice_creation_datetime AS DATE)) AS adhesion_date
             FROM `stable-splicer-294813.dwh_datasource_sales.transaction_details_visits`
             WHERE UPPER(CAST(lplclub2026 AS STRING)) IN ('TRUE', '1', 'OUI', 'YES')
               AND CAST(invoice_creation_datetime AS DATE) >= '2026-03-20'
               AND {TDV_VALID}
             GROUP BY customer_email
         ) a ON LOWER(t.customer_email) = a.email
         WHERE t.article_code LIKE '%LPLCLUB%'
           AND CAST(t.invoice_creation_datetime AS DATE) > a.adhesion_date
           AND CAST(t.invoice_creation_datetime AS DATE) >= DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY)
           AND CAST(t.invoice_creation_datetime AS DATE) < DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
           AND {TDV_VALID_T}) AS use_retail_prev7
    """

    try:
        # Toutes les requêtes en parallèle — évite ~15s d'attente séquentielle
        with ThreadPoolExecutor(max_workers=5) as ex:
            f_members = ex.submit(run_bq, q_members)
            f_recrut  = ex.submit(run_bq, q_recrut)
            f_trends  = ex.submit(run_bq, q_trends)
            f_7d      = ex.submit(shopify_order_count, 7)
            f_70d     = ex.submit(shopify_order_count, 70)
        m = f_members.result()[0]
        r = f_recrut.result()[0]
        t = f_trends.result()[0]
        web_orders_7d  = f_7d.result()
        web_orders_10w = f_70d.result()

        def pct(a, b):
            return round(a / b * 100, 1) if b else 0

        return jsonify({
            "total_active": m.total_active,
            "tx_utilisation": pct(m.total_used, m.total_active),
            "web_active": m.web_active,
            "retail_active": m.retail_active,
            "tx_web_util": pct(m.web_used, m.web_active),
            "tx_retail_util": pct(m.retail_used, m.retail_active),
            "tx_web_10w": pct(r.web_vip_10w, web_orders_10w),
            "tx_web_7d": pct(r.web_vip_7d, web_orders_7d),
            "tx_retail_10w": pct(r.ret_vip_10w, r.ret_tot_10w),
            "tx_retail_7d": pct(r.ret_vip_7d, r.ret_tot_7d),
            # Tendances 7j (fenêtres complètes, hors aujourd'hui)
            "new_web_7d":      int(t.new_web_7d or 0),
            "new_web_prev7":   int(t.new_web_prev7 or 0),
            "new_retail_7d":   int(t.new_retail_7d or 0),
            "new_retail_prev7":int(t.new_retail_prev7 or 0),
            "use_web_7d":      int(t.use_web_7d or 0),
            "use_web_prev7":   int(t.use_web_prev7 or 0),
            "use_retail_7d":   int(t.use_retail_7d or 0),
            "use_retail_prev7":int(t.use_retail_prev7 or 0),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/lpl/daily-adhesions")
@login_required
def lpl_daily_adhesions():
    q = f"""
    WITH web_adhesions AS (
        SELECT LOWER(email) AS email, DATE(order_date) AS qualifying_date, 'WEB' AS source
        FROM `{PROJECT_ID}.shopify_data_eu.custom_transactions_history`
        WHERE LOWER(shipping_method) LIKE '%lpl club%'
          AND {CTH_VALID}
        UNION ALL
        SELECT LOWER(email) AS email, DATE(created_at) AS qualifying_date, 'WEB' AS source
        FROM `{PROJECT_ID}.shopify_data_eu.lpl_club_web_orders`
    ),
    retail_adhesions AS (
        SELECT LOWER(customer_email) AS email, CAST(invoice_creation_datetime AS DATE) AS qualifying_date, 'RETAIL' AS source
        FROM `stable-splicer-294813.dwh_datasource_sales.transaction_details_visits`
        WHERE UPPER(CAST(lplclub2026 AS STRING)) IN ('TRUE', '1', 'OUI', 'YES')
          AND CAST(invoice_creation_datetime AS DATE) >= '2026-03-20'
          AND {TDV_VALID}
    ),
    manuel_adhesions AS (
        SELECT LOWER(email) AS email, DATE(added_at) AS qualifying_date, 'MANUEL' AS source
        FROM `{PROJECT_ID}.shopify_data_eu.manual_lpl_club_members`
    ),
    all_adhesions AS (
        SELECT * FROM web_adhesions
        UNION ALL SELECT * FROM retail_adhesions
        UNION ALL SELECT * FROM manuel_adhesions
    )
    SELECT
        CAST(qualifying_date AS STRING) AS jour,
        source,
        COUNT(DISTINCT email) AS count
    FROM all_adhesions
    WHERE qualifying_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY)
    GROUP BY jour, source
    ORDER BY jour ASC
    """
    try:
        rows = run_bq(q)
        return jsonify([{"jour": r.jour, "source": r.source, "count": r.count} for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/lpl/weekly-adhesions")
@login_required
def lpl_weekly_adhesions():
    # On interroge les tables brutes (pas dim_unified_customers) pour cumuler
    # toutes les méthodes : shipping_method (ancien), webhook BQ (nouveau), retail, manuel.
    q = f"""
    WITH web_adhesions AS (
        -- Méthode 1 & 2 : shipping_method contient LPL CLUB (ancien système jusqu'à avril 2026)
        SELECT LOWER(email) AS email, DATE(order_date) AS qualifying_date, 'WEB' AS source
        FROM `{PROJECT_ID}.shopify_data_eu.custom_transactions_history`
        WHERE LOWER(shipping_method) LIKE '%lpl club%'
          AND {CTH_VALID}
        UNION ALL
        -- Méthode 3 : nouveau produit "Adhésion LPL Club" loggé par le webhook en temps réel
        SELECT LOWER(email) AS email, DATE(created_at) AS qualifying_date, 'WEB' AS source
        FROM `{PROJECT_ID}.shopify_data_eu.lpl_club_web_orders`
    ),
    retail_adhesions AS (
        SELECT LOWER(customer_email) AS email, CAST(invoice_creation_datetime AS DATE) AS qualifying_date, 'RETAIL' AS source
        FROM `stable-splicer-294813.dwh_datasource_sales.transaction_details_visits`
        WHERE UPPER(CAST(lplclub2026 AS STRING)) IN ('TRUE', '1', 'OUI', 'YES')
          AND CAST(invoice_creation_datetime AS DATE) >= '2026-03-20'
          AND {TDV_VALID}
    ),
    manuel_adhesions AS (
        SELECT LOWER(email) AS email, DATE(added_at) AS qualifying_date, 'MANUEL' AS source
        FROM `{PROJECT_ID}.shopify_data_eu.manual_lpl_club_members`
    ),
    all_adhesions AS (
        SELECT * FROM web_adhesions
        UNION ALL SELECT * FROM retail_adhesions
        UNION ALL SELECT * FROM manuel_adhesions
    )
    SELECT
        CAST(DATE_TRUNC(qualifying_date, ISOWEEK) AS STRING) AS semaine,
        source,
        COUNT(DISTINCT email) AS count
    FROM all_adhesions
    WHERE qualifying_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 10 WEEK)
    GROUP BY semaine, source
    ORDER BY semaine ASC
    """
    try:
        rows = run_bq(q)
        return jsonify([{"semaine": r.semaine, "source": r.source, "count": r.count} for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/lpl/source-pie")
@login_required
def lpl_source_pie():
    # Même logique que weekly-adhesions : tables brutes pour cumuler toutes les méthodes
    q = f"""
    WITH web_adhesions AS (
        SELECT LOWER(email) AS email, 'WEB' AS source
        FROM `{PROJECT_ID}.shopify_data_eu.custom_transactions_history`
        WHERE LOWER(shipping_method) LIKE '%lpl club%'
          AND DATE(order_date) >= DATE_SUB(CURRENT_DATE(), INTERVAL 10 WEEK)
          AND {CTH_VALID}
        UNION DISTINCT
        SELECT LOWER(email) AS email, 'WEB' AS source
        FROM `{PROJECT_ID}.shopify_data_eu.lpl_club_web_orders`
        WHERE DATE(created_at) >= DATE_SUB(CURRENT_DATE(), INTERVAL 10 WEEK)
    ),
    retail_adhesions AS (
        SELECT LOWER(customer_email) AS email, 'RETAIL' AS source
        FROM `stable-splicer-294813.dwh_datasource_sales.transaction_details_visits`
        WHERE UPPER(CAST(lplclub2026 AS STRING)) IN ('TRUE', '1', 'OUI', 'YES')
          AND CAST(invoice_creation_datetime AS DATE) >= '2026-03-20'
          AND CAST(invoice_creation_datetime AS DATE) >= DATE_SUB(CURRENT_DATE(), INTERVAL 10 WEEK)
          AND {TDV_VALID}
    ),
    manuel_adhesions AS (
        SELECT LOWER(email) AS email, 'MANUEL' AS source
        FROM `{PROJECT_ID}.shopify_data_eu.manual_lpl_club_members`
        WHERE DATE(added_at) >= DATE_SUB(CURRENT_DATE(), INTERVAL 10 WEEK)
    )
    SELECT source, COUNT(DISTINCT email) AS count
    FROM (
        SELECT * FROM web_adhesions
        UNION ALL SELECT * FROM retail_adhesions
        UNION ALL SELECT * FROM manuel_adhesions
    )
    GROUP BY source
    ORDER BY count DESC
    """
    try:
        rows = run_bq(q)
        return jsonify([{"source": r.source, "count": r.count} for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/lpl/weekly-uses")
@login_required
def lpl_weekly_uses():
    q = f"""
    WITH web_uses AS (
        SELECT CAST(DATE_TRUNC(DATE(created_at), ISOWEEK) AS STRING) AS semaine, 'WEB' AS canal, COUNT(*) AS uses
        FROM `{PROJECT_ID}.shopify_data_eu.lpl_club_web_uses`
        WHERE DATE(created_at) >= DATE_SUB(CURRENT_DATE(), INTERVAL 10 WEEK)
        GROUP BY semaine
    ),
    -- Date d'adhésion retail par client (1ère transaction avec lplclub2026=TRUE)
    retail_adhesions AS (
        SELECT LOWER(customer_email) AS email,
               MIN(CAST(invoice_creation_datetime AS DATE)) AS adhesion_date
        FROM `stable-splicer-294813.dwh_datasource_sales.transaction_details_visits`
        WHERE UPPER(CAST(lplclub2026 AS STRING)) IN ('TRUE', '1', 'OUI', 'YES')
          AND CAST(invoice_creation_datetime AS DATE) >= '2026-03-20'
          AND {TDV_VALID}
        GROUP BY customer_email
    ),
    -- Utilisations retail = LPLCLUB article, STRICTEMENT après la date d'adhésion
    retail_uses AS (
        SELECT CAST(DATE_TRUNC(CAST(t.invoice_creation_datetime AS DATE), ISOWEEK) AS STRING) AS semaine,
               'BOUTIQUE' AS canal, COUNT(*) AS uses
        FROM `stable-splicer-294813.dwh_datasource_sales.transaction_details_visits` t
        JOIN retail_adhesions a ON LOWER(t.customer_email) = a.email
        WHERE t.article_code LIKE '%LPLCLUB%'
          AND CAST(t.invoice_creation_datetime AS DATE) > a.adhesion_date
          AND CAST(t.invoice_creation_datetime AS DATE) >= DATE_SUB(CURRENT_DATE(), INTERVAL 10 WEEK)
          AND {TDV_VALID_T}
        GROUP BY semaine
    )
    SELECT semaine, canal, SUM(uses) AS total_uses
    FROM (SELECT * FROM web_uses UNION ALL SELECT * FROM retail_uses)
    GROUP BY semaine, canal
    ORDER BY semaine ASC
    """
    try:
        rows = run_bq(q)
        return jsonify([{"semaine": r.semaine, "canal": r.canal, "uses": r.total_uses} for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/lpl/live-members-web")
@login_required
def lpl_live_members_web():
    # 1 ligne par client (MIN = date de 1ère adhésion web), triée par les plus récentes
    q = f"""
    SELECT email, MIN(qualifying_date) AS adhesion_date FROM (
        SELECT LOWER(email) AS email, DATE(created_at) AS qualifying_date
        FROM `{PROJECT_ID}.shopify_data_eu.lpl_club_web_orders`
        UNION ALL
        SELECT LOWER(email) AS email, DATE(order_date) AS qualifying_date
        FROM `{PROJECT_ID}.shopify_data_eu.custom_transactions_history`
        WHERE LOWER(shipping_method) LIKE '%lpl club%'
          AND {CTH_VALID}
    )
    GROUP BY email
    ORDER BY adhesion_date DESC
    LIMIT 30
    """
    try:
        rows = run_bq(q)
        return jsonify([{"email": r.email, "date": str(r.adhesion_date)} for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/lpl/live-members-retail")
@login_required
def lpl_live_members_retail():
    # 1 ligne par client (MIN = date de 1ère adhésion), triée par les plus récentes
    q = f"""
    SELECT email, MIN(qualifying_date) AS adhesion_date
    FROM (
        SELECT LOWER(customer_email) AS email,
               CAST(invoice_creation_datetime AS DATE) AS qualifying_date
        FROM `stable-splicer-294813.dwh_datasource_sales.transaction_details_visits`
        WHERE UPPER(CAST(lplclub2026 AS STRING)) IN ('TRUE', '1', 'OUI', 'YES')
          AND CAST(invoice_creation_datetime AS DATE) >= '2026-03-20'
          AND customer_email IS NOT NULL AND customer_email != ''
          AND {TDV_VALID}
    )
    GROUP BY email
    ORDER BY adhesion_date DESC
    LIMIT 30
    """
    try:
        rows = run_bq(q)
        return jsonify([{"email": r.email, "date": str(r.adhesion_date)} for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/lpl/live-uses-web")
@login_required
def lpl_live_uses_web():
    q = f"""
    SELECT LOWER(email) AS email,
           CAST(created_at AS STRING) AS date_utilisation,
           'LPL Club -10%' AS preuve
    FROM `{PROJECT_ID}.shopify_data_eu.lpl_club_web_uses`
    ORDER BY created_at DESC
    LIMIT 30
    """
    try:
        rows = run_bq(q)
        return jsonify([{"email": r.email, "date": r.date_utilisation, "preuve": r.preuve} for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/lpl/live-uses-retail")
@login_required
def lpl_live_uses_retail():
    q = f"""
    WITH retail_adhesions AS (
        SELECT LOWER(customer_email) AS email,
               MIN(CAST(invoice_creation_datetime AS DATE)) AS adhesion_date
        FROM `stable-splicer-294813.dwh_datasource_sales.transaction_details_visits`
        WHERE UPPER(CAST(lplclub2026 AS STRING)) IN ('TRUE', '1', 'OUI', 'YES')
          AND CAST(invoice_creation_datetime AS DATE) >= '2026-03-20'
          AND {TDV_VALID}
        GROUP BY customer_email
    )
    SELECT LOWER(t.customer_email) AS email,
           CAST(t.invoice_creation_datetime AS STRING) AS date_utilisation,
           t.article_code AS preuve
    FROM `stable-splicer-294813.dwh_datasource_sales.transaction_details_visits` t
    JOIN retail_adhesions a ON LOWER(t.customer_email) = a.email
    WHERE t.article_code LIKE '%LPLCLUB%'
      AND CAST(t.invoice_creation_datetime AS DATE) > a.adhesion_date
      AND {TDV_VALID_T}
    ORDER BY t.invoice_creation_datetime DESC
    LIMIT 30
    """
    try:
        rows = run_bq(q)
        return jsonify([{"email": r.email, "date": r.date_utilisation, "preuve": r.preuve} for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/lpl/live-uses")
@login_required
def lpl_live_uses():
    q = f"""
    WITH web_uses AS (
        SELECT LOWER(email) AS email,
               CAST(order_date AS STRING) AS date_utilisation,
               'WEB' AS canal,
               discount_code AS preuve
        FROM `{PROJECT_ID}.shopify_data_eu.custom_transactions_history`
        WHERE discount_code LIKE '%LPL Club -10%'
          AND {CTH_VALID}
    ),
    retail_uses AS (
        SELECT LOWER(customer_email) AS email,
               CAST(invoice_creation_datetime AS STRING) AS date_utilisation,
               'BOUTIQUE' AS canal,
               article_code AS preuve
        FROM `stable-splicer-294813.dwh_datasource_sales.transaction_details_visits`
        WHERE article_code LIKE '%LPLCLUB%'
          AND {TDV_VALID}
    )
    SELECT email, date_utilisation, canal, preuve
    FROM (SELECT * FROM web_uses UNION ALL SELECT * FROM retail_uses)
    ORDER BY date_utilisation DESC
    LIMIT 30
    """
    try:
        rows = run_bq(q)
        return jsonify([{
            "email": r.email,
            "date": r.date_utilisation,
            "canal": r.canal,
            "preuve": r.preuve,
        } for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/lpl/add-member", methods=["POST"])
@login_required
def lpl_add_member():
    data = request.json or {}
    email = data.get("email", "").strip().lower()
    added_by = data.get("added_by", "").strip()
    notes = data.get("notes", "").strip()

    if not email or "@" not in email:
        return jsonify({"error": "Email invalide"}), 400
    if not added_by:
        return jsonify({"error": "Auteur requis"}), 400

    notes_safe = notes.replace("'", "''")
    added_by_safe = added_by.replace("'", "''")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    expiry = (date.today() + timedelta(days=365)).strftime("%Y-%m-%d")

    q = f"""
    INSERT INTO `{PROJECT_ID}.shopify_data_eu.manual_lpl_club_members`
    (email, added_at, expiry_date, added_by, notes)
    VALUES ('{email}', TIMESTAMP('{now}'), DATE('{expiry}'), '{added_by_safe}', '{notes_safe}')
    """
    try:
        bq_client.query(q).result()
        return jsonify({"success": True, "expiry": expiry})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/lpl/run-etl", methods=["POST"])
@login_required
def lpl_run_etl():
    q_etl = f"""
    CREATE OR REPLACE TABLE `{PROJECT_ID}.shopify_data_eu.dim_unified_customers` AS
    WITH AllClients AS (
        SELECT LOWER(email) AS email, NULLIF(TRIM(phone), '') AS phone
        FROM `{PROJECT_ID}.shopify_data_eu.custom_transactions_history`
        WHERE email IS NOT NULL AND email != ''
        UNION DISTINCT
        SELECT LOWER(customer_email) AS email, NULLIF(TRIM(customer_mobile_phone), '') AS phone
        FROM `stable-splicer-294813.dwh_datasource_sales.transaction_details_visits`
        WHERE customer_email IS NOT NULL AND customer_email != ''
        UNION DISTINCT
        SELECT LOWER(email) AS email, NULL AS phone
        FROM `{PROJECT_ID}.shopify_data_eu.manual_lpl_club_members`
        WHERE email IS NOT NULL AND email != ''
    ),
    QualifyingOrders AS (
        SELECT LOWER(t.email) AS email, DATE(t.order_date) AS qualifying_date, 'WEB' AS source
        FROM `{PROJECT_ID}.shopify_data_eu.custom_transactions_history` t
        WHERE LOWER(t.shipping_method) LIKE '%lpl club%'
          AND t.net_sales > 0
        UNION ALL
        SELECT LOWER(t.email) AS email, DATE(p.order_date) AS qualifying_date, 'WEB' AS source
        FROM `{PROJECT_ID}.shopify_data_eu.transactions_products_2020` p
        JOIN `{PROJECT_ID}.shopify_data_eu.custom_transactions_history` t
          ON t.client_id = p.client_id AND DATE(t.order_date) = DATE(p.order_date)
        WHERE LOWER(p.product_title) = 'adhésion lpl club'
        UNION ALL
        SELECT LOWER(customer_email) AS email, CAST(invoice_creation_datetime AS DATE) AS qualifying_date, 'RETAIL' AS source
        FROM `stable-splicer-294813.dwh_datasource_sales.transaction_details_visits`
        WHERE UPPER(CAST(lplclub2026 AS STRING)) IN ('TRUE', '1', 'OUI', 'YES')
          AND CAST(invoice_creation_datetime AS DATE) >= '2026-03-20'
          AND {TDV_VALID}
        UNION ALL
        SELECT LOWER(email) AS email, DATE(added_at) AS qualifying_date, 'MANUEL' AS source
        FROM `{PROJECT_ID}.shopify_data_eu.manual_lpl_club_members`
    ),
    LatestQualifying AS (
        SELECT email,
               MAX(qualifying_date) AS last_club_order_date,
               ARRAY_AGG(source ORDER BY qualifying_date DESC LIMIT 1)[OFFSET(0)] AS latest_source
        FROM QualifyingOrders WHERE email IS NOT NULL GROUP BY email
    )
    SELECT c.email,
           MAX(c.phone) AS phone,
           l.last_club_order_date,
           l.latest_source AS source,
           DATE_ADD(l.last_club_order_date, INTERVAL 1 YEAR) AS lpl_club_expiry_date,
           CASE WHEN l.last_club_order_date IS NOT NULL
                 AND DATE_ADD(l.last_club_order_date, INTERVAL 1 YEAR) >= CURRENT_DATE()
                THEN TRUE ELSE FALSE END AS is_lpl_club
    FROM AllClients c
    LEFT JOIN LatestQualifying l ON c.email = l.email
    GROUP BY c.email, l.last_club_order_date, l.latest_source
    """
    try:
        bq_client.query(q_etl).result()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# API — REFERRAL
# ---------------------------------------------------------------------------

@app.route("/api/referral/kpis")
@login_required
def referral_kpis():
    q_main = f"""
    SELECT
        -- État des codes
        (SELECT COUNT(*) FROM `{PROJECT_ID}.shopify_data_eu.referral_codes`
         WHERE code LIKE 'LPL-%' AND status = 'ACTIVE') AS codes_actifs,
        (SELECT COUNT(*) FROM `{PROJECT_ID}.shopify_data_eu.referral_codes`
         WHERE code LIKE 'LPL-%' AND status = 'MAX_REACHED') AS codes_max_reached,
        (SELECT COUNT(*) FROM `{PROJECT_ID}.shopify_data_eu.referral_codes`
         WHERE code LIKE 'LPL-%' AND status LIKE 'BLOCKED%') AS codes_bloques,
        -- Impact programme
        (SELECT COUNT(*) FROM `{PROJECT_ID}.shopify_data_eu.referral_redemptions`
         WHERE referrer_id LIKE 'LPL-%') AS total_filleuls,
        (SELECT COUNT(*) FROM `{PROJECT_ID}.shopify_data_eu.referral_codes`
         WHERE code LIKE 'LPL-%' AND usage_count > 0) AS parrains_actifs,
        (SELECT COUNT(*) FROM `{PROJECT_ID}.shopify_data_eu.referral_redemptions`
         WHERE referrer_id LIKE 'LPL-%'
           AND DATE(redemption_date) >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)) AS filleuls_30j,
        (SELECT COUNT(*) FROM `{PROJECT_ID}.shopify_data_eu.referral_redemptions`
         WHERE referrer_id LIKE 'LPL-%'
           AND DATE(redemption_date) >= DATE_SUB(CURRENT_DATE(), INTERVAL 60 DAY)
           AND DATE(redemption_date) < DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)) AS filleuls_prev30j,
        -- Budget programme
        (SELECT COUNT(*) * 10.0 FROM `{PROJECT_ID}.shopify_data_eu.referral_redemptions`
         WHERE referrer_id LIKE 'LPL-%') AS cagnotte_generee,
        (SELECT COALESCE(SUM(reward_value), 0) FROM `{PROJECT_ID}.shopify_data_eu.referral_codes`
         WHERE code LIKE 'KDO-%' AND status = 'USED') AS cagnotte_depensee
    """
    # ca_filleuls isolé dans une requête dédiée pour debug et robustesse
    # TRIM() sur les deux côtés pour éviter les espaces parasites dans les emails
    q_ca = f"""
    SELECT COALESCE(SUM(t.net_sales), 0) AS ca_filleuls
    FROM `{PROJECT_ID}.shopify_data_eu.custom_transactions_history` t
    JOIN `{PROJECT_ID}.shopify_data_eu.referral_redemptions` rr
      ON TRIM(LOWER(t.email)) = TRIM(LOWER(rr.referred_id))
    WHERE rr.referrer_id LIKE 'LPL-%'
      AND t.net_sales > 0
      AND DATE(t.order_date) >= DATE(rr.redemption_date)
    """
    try:
        row = run_bq(q_main)[0]
        ca_rows = run_bq(q_ca)
        ca_filleuls = float(ca_rows[0].ca_filleuls) if ca_rows else 0.0
        print(f"[referral_kpis] ca_filleuls={ca_filleuls} total_filleuls={row.total_filleuls}", flush=True)
        return jsonify({
            "codes_actifs":    row.codes_actifs,
            "codes_max_reached": row.codes_max_reached,
            "codes_bloques":   row.codes_bloques,
            "total_filleuls":  row.total_filleuls,
            "parrains_actifs": row.parrains_actifs,
            "filleuls_30j":    row.filleuls_30j,
            "filleuls_prev30j": row.filleuls_prev30j,
            "ca_filleuls":     ca_filleuls,
            "cagnotte_generee": float(row.cagnotte_generee or 0),
            "cagnotte_depensee": float(row.cagnotte_depensee or 0),
        })
    except Exception as e:
        print(f"[referral_kpis] ERREUR: {e}", flush=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/referral/weekly")
@login_required
def referral_weekly():
    q = f"""
    SELECT
        CAST(DATE_TRUNC(DATE(redemption_date), ISOWEEK) AS STRING) AS semaine,
        COUNT(*) AS parrainages
    FROM `{PROJECT_ID}.shopify_data_eu.referral_redemptions`
    WHERE referrer_id LIKE 'LPL-%'
      AND DATE(redemption_date) >= DATE_SUB(CURRENT_DATE(), INTERVAL 10 WEEK)
    GROUP BY semaine
    ORDER BY semaine ASC
    """
    try:
        rows = run_bq(q)
        return jsonify([{"semaine": r.semaine, "parrainages": r.parrainages} for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/referral/top-parrains")
@login_required
def referral_top_parrains():
    q = f"""
    SELECT owner_email, usage_count, status
    FROM `{PROJECT_ID}.shopify_data_eu.referral_codes`
    WHERE code LIKE 'LPL-%' AND usage_count > 0
    ORDER BY usage_count DESC
    LIMIT 20
    """
    try:
        rows = run_bq(q)
        return jsonify([{"email": r.owner_email, "usages": r.usage_count, "statut": r.status} for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/referral/by-boutique")
@login_required
def referral_by_boutique():
    q = f"""
    SELECT store_location, COUNT(*) AS validations
    FROM `{PROJECT_ID}.shopify_data_eu.referral_redemptions`
    WHERE referrer_id LIKE 'LPL-%'
      AND store_location NOT LIKE 'SHOPIFY%'
      AND store_location IS NOT NULL
      AND store_location != ''
    GROUP BY store_location
    ORDER BY validations DESC
    """
    try:
        rows = run_bq(q)
        return jsonify([{"boutique": r.store_location, "count": r.validations} for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/referral/status-distribution")
@login_required
def referral_status_distribution():
    q = f"""
    SELECT status, COUNT(*) AS count
    FROM `{PROJECT_ID}.shopify_data_eu.referral_codes`
    WHERE code LIKE 'LPL-%'
    GROUP BY status
    ORDER BY count DESC
    """
    try:
        rows = run_bq(q)
        return jsonify([{"status": r.status, "count": r.count} for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/referral/recent-redemptions")
@login_required
def referral_recent_redemptions():
    q = f"""
    SELECT referrer_id, referred_id, store_location, CAST(redemption_date AS STRING) AS redemption_date
    FROM `{PROJECT_ID}.shopify_data_eu.referral_redemptions`
    WHERE referrer_id LIKE 'LPL-%'
    ORDER BY redemption_date DESC
    LIMIT 30
    """
    try:
        rows = run_bq(q)
        return jsonify([{
            "code": r.referrer_id,
            "filleul": r.referred_id,
            "boutique": r.store_location,
            "date": r.redemption_date,
        } for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    debug = os.environ.get("FLASK_ENV") == "development"
    app.run(host="0.0.0.0", port=port, debug=debug)

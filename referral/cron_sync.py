import os
import time
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
except Exception as e:
    print(f"Erreur de chargement des secrets : {e}")
    SHOPIFY_STORE = os.environ.get("SHOPIFY_STORE", "")
    SHOPIFY_TOKEN = os.environ.get("SHOPIFY_TOKEN", "")
    CRON_SECRET = os.environ.get("CRON_SECRET", "LPL_CRON_SUPER_SECRET_2024!")

client = bigquery.Client(project=PROJECT_ID)

def get_shopify_headers():
    return {
        "X-Shopify-Access-Token": SHOPIFY_TOKEN,
        "Content-Type": "application/json"
    }

def run_shopify_graphql(query, variables=None):
    api_version = "2025-04"
    url = f"https://{SHOPIFY_STORE}/admin/api/{api_version}/graphql.json"
    payload = {"query": query, "variables": variables or {}}
    try:
        # Ajout d'un timeout de 30s pour éviter les blocages infinis
        response = requests.post(url, json=payload, headers=get_shopify_headers(), timeout=30)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"Erreur HTTP Shopify: {response.text}")
            return None
    except requests.exceptions.RequestException as e:
        print(f"Exception lors de la requête Shopify: {e}")
        return None

# =====================================================================
# ROUTE UNIQUE : SYNCHRONISATION SHOPIFY -> BIGQUERY (FINANCE & PRODUITS)
# =====================================================================
@app.route('/api/cron-sync-orders', methods=['GET', 'POST'])
def cron_sync_orders():
    provided_secret = request.headers.get('X-Cron-Secret') or request.args.get('secret')
    if provided_secret != CRON_SECRET:
        return jsonify({"error": "Unauthorized"}), 403

    print("🚀 Début de la synchronisation Shopify -> BigQuery (Finance & Produits)...")
    
    tz_paris = ZoneInfo("Europe/Paris")
    now = datetime.now(tz_paris)
    end_str = now.strftime("%Y-%m-%d")
    
    # ⚠️ Fenêtre maintenue à 35 jours pour tes besoins Referral/Finance
    start_date = now - timedelta(days=35)
    start_str = start_date.strftime("%Y-%m-%d")
    
    print(f"📅 Période: {start_str}T00:00:00Z -> {end_str}T23:59:59Z")

    table_finance = f"{PROJECT_ID}.shopify_data_eu.custom_transactions_history"
    table_products = f"{PROJECT_ID}.shopify_data_eu.transactions_products_2020"
    
    print("🗑️ Purge des données récentes...")
    try:
        client.query(f"DELETE FROM `{table_finance}` WHERE order_date >= '{start_str}'").result()
        client.query(f"DELETE FROM `{table_products}` WHERE order_date >= '{start_str}'").result()
    except Exception as e:
        print(f"❌ Erreur lors de la purge BigQuery: {e}")
        return jsonify({"error": "Failed to purge old data"}), 500

    query = """
    query ($query: String!, $cursor: String) {
      orders(first: 250, query: $query, after: $cursor, sortKey: CREATED_AT) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id createdAt cancelledAt displayFinancialStatus tags sourceName email phone
          billingAddress { phone }
          shippingAddress { phone }
          totalPriceSet { shopMoney { amount } }
          customer { id email phone }
          refunds { 
             transactions(first: 5) { 
                 nodes { kind status amountSet { shopMoney { amount } } } 
             } 
          }
          discountApplications(first: 5) { 
             nodes { ... on DiscountCodeApplication { code } } 
          }
          shippingLines(first: 1) { 
             nodes { title } 
          }
          lineItems(first: 20) { 
            nodes { 
                title quantity 
                originalTotalSet { shopMoney { amount } } 
                product { productType } 
            } 
          }
        }
      }
    }
    """
    query_string = f"created_at:>={start_str}T00:00:00Z created_at:<={end_str}T23:59:59Z"
    
    cursor = None
    has_next = True
    all_rows_finance = []
    all_rows_products = []

    while has_next:
        variables = {"query": query_string, "cursor": cursor}
        res = run_shopify_graphql(query, variables)
        
        if res and "errors" in res:
            print(f"❌ Erreur bloquante renvoyée par Shopify GraphQL : {res['errors']}")
            return jsonify({"status": "error", "message": "GraphQL Validation Error", "details": res['errors']}), 500

        if not res or not res.get("data", {}).get("orders"):
            print("⚠️ Erreur de récupération ou fin des données atteinte.")
            break
            
        orders = res["data"]["orders"]
        
        for order in orders.get("nodes", []):
            if order.get("cancelledAt"): continue
            
            status = order.get("displayFinancialStatus")
            if status not in ['PAID', 'PARTIALLY_PAID']: continue
            
            tags = " ".join(order.get("tags", [])).lower()
            if "alan" in tags or "wholesale" in tags or "b2b" in tags: continue
            
            customer = order.get("customer") or {}
            client_id = customer.get("id", "").replace("gid://shopify/Customer/", "") if customer.get("id") else None
            if not client_id: continue
            
            order_date = order.get("createdAt").split("T")[0]
            order_id = order.get("id").replace("gid://shopify/Order/", "")
            
            total = float(order.get("totalPriceSet", {}).get("shopMoney", {}).get("amount", 0))
            refunded = 0.0
            
            for refund in order.get("refunds") or []:
                for trx in refund.get("transactions", {}).get("nodes", []):
                    if trx.get("kind") == "REFUND" and trx.get("status") == "SUCCESS":
                        refunded += float(trx.get("amountSet", {}).get("shopMoney", {}).get("amount", 0))
            
            net_sales = round(total - refunded, 2)
            
            email = order.get("email") or customer.get("email") or ""
            phone = order.get("phone") or \
                    (order.get("shippingAddress") or {}).get("phone") or \
                    (order.get("billingAddress") or {}).get("phone") or \
                    customer.get("phone") or ""

            discount_codes = []
            for app in order.get("discountApplications", {}).get("nodes", []):
                code = app.get("code")
                if code: discount_codes.append(code)
            discount_code_str = ", ".join(discount_codes) if discount_codes else None

            # --- Récupération de la méthode de livraison ---
            shipping_lines = order.get("shippingLines", {}).get("nodes", [])
            shipping_method = shipping_lines[0].get("title") if shipping_lines else None

            all_rows_finance.append({
                "order_date": order_date,
                "client_id": client_id,
                "net_sales": net_sales,
                "source": (order.get("sourceName") or "").lower(),
                "email": email.lower().strip() if email else None,
                "phone": phone.strip() if phone else None,
                "discount_code": discount_code_str,
                "shipping_method": shipping_method
            })
            
            for item in order.get("lineItems", {}).get("nodes", []):
                product_type = item.get("product", {}).get("productType") if item.get("product") else None
                all_rows_products.append({
                    "order_date": order_date,
                    "order_id": order_id,
                    "client_id": client_id,
                    "product_title": item.get("title"),
                    "product_type": product_type,
                    "quantity": item.get("quantity"),
                    "price": float(item.get("originalTotalSet", {}).get("shopMoney", {}).get("amount", 0))
                })
        
        has_next = orders.get("pageInfo", {}).get("hasNextPage", False)
        cursor = orders.get("pageInfo", {}).get("endCursor")

    # Insertion groupée via batch load (évite le conflit streaming buffer / DELETE)
    load_cfg = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
    total_orders_inserted = 0
    total_products_inserted = 0

    if all_rows_finance:
        client.load_table_from_json(all_rows_finance, table_finance, job_config=load_cfg).result()
        total_orders_inserted = len(all_rows_finance)

    if all_rows_products:
        client.load_table_from_json(all_rows_products, table_products, job_config=load_cfg).result()
        total_products_inserted = len(all_rows_products)
    
    print(f"✅ Succès ! Inséré au total: {total_orders_inserted} commandes et {total_products_inserted} produits.")
    return jsonify({"status": "success", "orders_inserted": total_orders_inserted, "products_inserted": total_products_inserted}), 200

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
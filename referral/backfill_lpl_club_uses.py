"""
Backfill lpl_club_web_uses — utilisations du discount "LPL Club -10%" en web.

Source : Shopify REST (discount_applications), toute la période depuis le lancement.
Le discount "LPL Club -10%" a été automatique depuis le 20.03.2026.

Sont ignorés :
  - codes referral LPL-XXXX (type: discount_code)
  - promo de lancement LPL2X1

La table est créée si elle n'existe pas. Idempotent (skip doublons sur order_id).

Usage :
    SHOPIFY_STORE="xxx.myshopify.com" SHOPIFY_TOKEN="shpat_..." \
    python3 backfill_lpl_club_uses.py
"""

import os
import time
import requests
import toml
from google.cloud import bigquery

# ── Config ────────────────────────────────────────────────────────────────────
PROJECT_ID = "shopify-data-ltv"
DATASET    = "shopify_data_eu"
TABLE      = "lpl_club_web_uses"
FULL_TABLE = f"`{PROJECT_ID}.{DATASET}.{TABLE}`"
LAUNCH_DATE = "2026-03-20"

try:
    secrets = toml.load(".streamlit/secrets.toml")
    SHOPIFY_STORE = secrets["shopify"]["store_url"]
    SHOPIFY_TOKEN = secrets["shopify"]["access_token"]
except Exception:
    SHOPIFY_STORE = os.environ["SHOPIFY_STORE"]
    SHOPIFY_TOKEN = os.environ["SHOPIFY_TOKEN"]

bq = bigquery.Client(project=PROJECT_ID)

# ── Création de la table si inexistante ───────────────────────────────────────
def ensure_table():
    print("Vérification / création de la table BQ…", flush=True)
    bq.query(f"""
        CREATE TABLE IF NOT EXISTS {FULL_TABLE} (
            email      STRING    NOT NULL,
            order_id   STRING    NOT NULL,
            created_at TIMESTAMP NOT NULL
        )
    """).result()
    print(f"✅ Table {FULL_TABLE} prête.", flush=True)


# ── Source : Shopify REST ─────────────────────────────────────────────────────
def fetch_shopify_uses():
    """
    Pagine toutes les commandes depuis LAUNCH_DATE.
    Retient uniquement celles avec un discount "LPL Club -10%" dans discountApplications.
    """
    print(f"\nScanning Shopify depuis {LAUNCH_DATE}…", flush=True)
    headers = {"X-Shopify-Access-Token": SHOPIFY_TOKEN}
    url = f"https://{SHOPIFY_STORE}/admin/api/2025-04/orders.json"
    params = {
        "status": "any",
        "financial_status": "paid",
        "created_at_min": f"{LAUNCH_DATE}T00:00:00Z",
        "limit": 250,
        "fields": "id,email,created_at,discount_applications",
    }
    results = []
    page = 0

    while url:
        page += 1
        print(f"  Page {page}…", end=" ", flush=True)
        r = requests.get(url, params=params, headers=headers, timeout=30)
        if r.status_code == 429:
            print("rate limit, pause 5s…", flush=True)
            time.sleep(5)
            continue
        if r.status_code != 200:
            print(f"\n❌ HTTP {r.status_code}: {r.text[:200]}", flush=True)
            break

        orders = r.json().get("orders", [])
        print(f"{len(orders)} commandes", flush=True)

        for order in orders:
            has_lpl_club = any(
                "LPL Club -10" in (da.get("title") or "")
                for da in order.get("discount_applications", [])
            )
            if not has_lpl_club:
                continue
            email = (order.get("email") or "").lower().strip()
            if not email:
                continue
            order_id = str(order["id"])
            created_at = order["created_at"].replace("T", " ").replace("Z", " UTC")
            results.append((email, order_id, created_at))
            print(f"    ✅ {order['created_at'][:10]} — {order_id} — {email}", flush=True)

        # Pagination via Link header
        link = r.headers.get("Link", "")
        url, params = None, None
        for part in link.split(","):
            if 'rel="next"' in part:
                next_url = part.split(";")[0].strip().strip("<>")
                page_info = next_url.split("page_info=")[-1].split("&")[0]
                url = f"https://{SHOPIFY_STORE}/admin/api/2025-04/orders.json"
                params = {"limit": 250, "page_info": page_info,
                          "fields": "id,email,created_at,discount_applications"}
        time.sleep(0.3)

    print(f"\n  → {len(results)} utilisation(s) LPL Club -10% trouvée(s).", flush=True)
    return results


# ── Insertion BQ (skip doublons sur order_id) ─────────────────────────────────
def fetch_existing_order_ids():
    return {row.order_id for row in bq.query(f"SELECT order_id FROM {FULL_TABLE}").result()}


def insert_uses(uses, existing_ids):
    to_insert = [(email, oid, ts) for email, oid, ts in uses if oid not in existing_ids]
    if not to_insert:
        print("\nRien à insérer — tout est déjà présent.", flush=True)
        return

    values = ",\n  ".join(
        f"('{email}', '{oid}', TIMESTAMP('{ts}'))"
        for email, oid, ts in to_insert
    )
    bq.query(f"""
        INSERT INTO {FULL_TABLE} (email, order_id, created_at)
        VALUES {values}
    """).result()
    print(f"\n✅ {len(to_insert)} ligne(s) insérée(s) dans {TABLE}.", flush=True)
    for email, oid, ts in to_insert:
        print(f"   {oid} — {email} — {ts}", flush=True)


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    ensure_table()
    uses = fetch_shopify_uses()
    print(f"\nTotal : {len(uses)} utilisation(s) à vérifier.", flush=True)
    existing = fetch_existing_order_ids()
    insert_uses(uses, existing)

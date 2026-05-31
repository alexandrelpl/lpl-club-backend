"""
Backfill LPL Club orders (old method: shipping line title contains "LPL CLUB").

Covers the gap: April 14, 2026 → May 28, 2026 inclusive.
These orders used the shipping method "Livraison ... LPL CLUB" before the
new product-based method was activated.

Inserts into shopify_data_eu.lpl_club_web_orders, skipping duplicates.

Usage:
    SHOPIFY_STORE="xxx.myshopify.com" SHOPIFY_TOKEN="shpat_..." \
    python3 backfill_lpl_club_shipping.py
"""

import os
import time
import requests
import toml
from google.cloud import bigquery

# ── Config ────────────────────────────────────────────────────────────────────
PROJECT_ID = "shopify-data-ltv"
START_DATE = "2026-04-14T00:00:00Z"
END_DATE   = "2026-05-29T00:00:00Z"   # exclusive (new method starts May 29)
LPL_CLUB_KEYWORD = "LPL CLUB"         # must appear in shipping line title

try:
    secrets = toml.load(".streamlit/secrets.toml")
    SHOPIFY_STORE = secrets["shopify"]["store_url"]
    SHOPIFY_TOKEN = secrets["shopify"]["access_token"]
except Exception:
    SHOPIFY_STORE = os.environ["SHOPIFY_STORE"]
    SHOPIFY_TOKEN = os.environ["SHOPIFY_TOKEN"]

bq = bigquery.Client(project=PROJECT_ID)

# ── GraphQL query ─────────────────────────────────────────────────────────────
QUERY = """
query FetchOrders($cursor: String) {
  orders(
    first: 50,
    after: $cursor,
    query: "financial_status:paid created_at:>=%s created_at:<%s"
  ) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      name
      email
      createdAt
      shippingLines(first: 5) {
        nodes { title }
      }
    }
  }
}
""" % (START_DATE, END_DATE)


def run_graphql(cursor=None):
    url = f"https://{SHOPIFY_STORE}/admin/api/2025-04/graphql.json"
    headers = {
        "X-Shopify-Access-Token": SHOPIFY_TOKEN,
        "Content-Type": "application/json",
    }
    variables = {"cursor": cursor} if cursor else {}
    for attempt in range(3):
        r = requests.post(url, json={"query": QUERY, "variables": variables}, headers=headers, timeout=30)
        if r.status_code == 429:
            print("Rate limited, sleeping 5s…")
            time.sleep(5)
            continue
        if r.status_code == 200:
            return r.json()
        print(f"HTTP {r.status_code}: {r.text[:200]}")
        time.sleep(2)
    raise RuntimeError("Shopify GraphQL failed after 3 retries")


def has_lpl_club_shipping(order):
    for line in order.get("shippingLines", {}).get("nodes", []):
        if LPL_CLUB_KEYWORD.upper() in (line.get("title") or "").upper():
            return True
    return False


def fetch_all_adhesions():
    results = []
    cursor = None
    page = 0
    while True:
        page += 1
        print(f"Fetching page {page}…", flush=True)
        data = run_graphql(cursor)
        orders = data["data"]["orders"]
        for order in orders["nodes"]:
            if has_lpl_club_shipping(order):
                email = (order.get("email") or "").lower().strip()
                if not email:
                    continue
                order_id = order["id"].split("/")[-1]
                created_at = order["createdAt"]
                results.append((email, order_id, created_at))
                print(f"  ✅ {order['name']} — {email}")
        if not orders["pageInfo"]["hasNextPage"]:
            break
        cursor = orders["pageInfo"]["endCursor"]
        time.sleep(0.3)
    return results


def fetch_existing_order_ids():
    q = f"SELECT order_id FROM `{PROJECT_ID}.shopify_data_eu.lpl_club_web_orders`"
    return {row.order_id for row in bq.query(q).result()}


def insert_to_bq(adhesions, existing_ids):
    to_insert = [(email, oid, ts) for email, oid, ts in adhesions if oid not in existing_ids]
    if not to_insert:
        print("Nothing to insert — all already present in BQ.")
        return

    values = ",\n  ".join(
        f"('{email}', '{oid}', TIMESTAMP('{ts.replace('T', ' ').replace('Z', ' UTC')}'))"
        for email, oid, ts in to_insert
    )
    q = f"""
    INSERT INTO `{PROJECT_ID}.shopify_data_eu.lpl_club_web_orders`
      (email, order_id, created_at)
    VALUES
      {values}
    """
    bq.query(q).result()
    print(f"\n✅ Inserted {len(to_insert)} rows into lpl_club_web_orders.")
    for email, oid, ts in to_insert:
        print(f"   {oid} — {email} — {ts}")


if __name__ == "__main__":
    print(f"Scanning Shopify orders (shipping method) from {START_DATE} to {END_DATE}…\n")
    adhesions = fetch_all_adhesions()
    print(f"\nFound {len(adhesions)} LPL Club adhesion(s) via shipping line.")

    if adhesions:
        print("Checking existing BQ records…")
        existing = fetch_existing_order_ids()
        insert_to_bq(adhesions, existing)
    else:
        print("No adhesions found. Nothing to do.")

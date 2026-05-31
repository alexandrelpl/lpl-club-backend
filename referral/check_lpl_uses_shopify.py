"""
Scanne TOUTES les commandes Shopify du 20.03.2026 au 29.05.2026
et liste celles qui ont un discount contenant "LPL" ou "CLUB"
(quelque soit le type : code manuel, automatique, script, staff).

Usage :
    SHOPIFY_STORE="xxx.myshopify.com" SHOPIFY_TOKEN="shpat_..." \
    python3 check_lpl_uses_shopify.py
"""

import os, time, requests, toml

try:
    secrets = toml.load(".streamlit/secrets.toml")
    SHOPIFY_STORE = secrets["shopify"]["store_url"]
    SHOPIFY_TOKEN = secrets["shopify"]["access_token"]
except Exception:
    SHOPIFY_STORE = os.environ["SHOPIFY_STORE"]
    SHOPIFY_TOKEN = os.environ["SHOPIFY_TOKEN"]

headers = {"X-Shopify-Access-Token": SHOPIFY_TOKEN}
found = []
page = 0

url = f"https://{SHOPIFY_STORE}/admin/api/2025-04/orders.json"
params = {
    "status": "any",
    "financial_status": "paid",
    "created_at_min": "2026-03-20T00:00:00Z",
    "created_at_max": "2026-05-29T00:00:00Z",
    "limit": 250,
    "fields": "id,name,email,created_at,discount_applications",
}

while url:
    page += 1
    print(f"Page {page}…", end=" ", flush=True)
    r = requests.get(url, params=params, headers=headers, timeout=30)
    if r.status_code == 429:
        print("rate limit, pause…", flush=True)
        time.sleep(5)
        continue
    if r.status_code != 200:
        print(f"\nErreur HTTP {r.status_code}: {r.text[:200]}")
        break

    orders = r.json().get("orders", [])
    print(f"{len(orders)} commandes", flush=True)

    for o in orders:
        for da in o.get("discount_applications", []):
            label = (da.get("title") or da.get("code") or "").upper()
            if "LPL" in label or "CLUB" in label:
                found.append({
                    "order": o["name"],
                    "email": o.get("email", ""),
                    "date":  o["created_at"][:10],
                    "discount": da.get("title") or da.get("code"),
                    "type": da.get("type"),
                })

    # Pagination via Link header
    link = r.headers.get("Link", "")
    url, params = None, None
    for part in link.split(","):
        if 'rel="next"' in part:
            next_url = part.split(";")[0].strip().strip("<>")
            page_info = next_url.split("page_info=")[-1].split("&")[0]
            url = f"https://{SHOPIFY_STORE}/admin/api/2025-04/orders.json"
            params = {
                "limit": 250,
                "page_info": page_info,
                "fields": "id,name,email,created_at,discount_applications",
            }
    time.sleep(0.2)

print(f"\n{'='*60}")
print(f"{page} pages scannées. {len(found)} commande(s) avec discount LPL/CLUB :")
print(f"{'='*60}")
for f in found:
    print(f"  {f['date']} | {f['order']} | {f['email']}")
    print(f"            → {f['discount']} (type: {f['type']})")

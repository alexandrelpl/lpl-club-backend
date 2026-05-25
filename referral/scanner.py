import re
import urllib.request
import urllib.parse
import os
import toml
import base64
import requests as std_requests # Pour Shopify et le Proxy
from curl_cffi import requests # Pour le scraping anti-bot
from google.cloud import bigquery

# --- CONFIGURATION ---
PROJECT_ID = "shopify-data-ltv"

try:
    secrets = toml.load(".streamlit/secrets.toml")
    SHOPIFY_STORE = secrets["shopify"]["store_url"]
    SHOPIFY_TOKEN = secrets["shopify"]["access_token"]
except Exception:
    SHOPIFY_STORE = os.environ.get("SHOPIFY_STORE", "")
    SHOPIFY_TOKEN = os.environ.get("SHOPIFY_TOKEN", "")

client = bigquery.Client(project=PROJECT_ID)

urls_a_scanner = [
    "https://www.planreduc.com/code-reduction-le-petit-lunetier-bon-2603.html",
    "https://www.dealabs.com/codes-promo/lepetitlunetier",
    "https://www.poulpeo.com/reductions-le-petit-lunetier.htm",
    "https://lepetitlunetier.loveminty.fr/code-promo-le-petit-lunetier",
    "https://www.marieclaire.fr/codes-promo/lepetitlunetier.com",
    "https://fr.shopping.rakuten.com/boutique/lepetitlunetier",
    "https://fr.coupert.com/codes-promo/le-petit-lunetier",
    "https://lepetitlunetier.codepromo.club/",
    "https://www.savoo.fr/marques/codes-promo-le-petit-lunetier",
    "https://www.coupert.com/store/lepetitlunetier.com",
    "https://fr.igraal.com/codes-promo/le-petit-lunetier",
    "https://www.lareduction.fr/code-promo/le-petit-lunetier",
    "https://www.poulpeo.com/reductions-le-petit-lunetier.htm",
    "https://www.ma-reduc.com/reductions-pour-le-petit-lunetier.php",
    "https://wanteeed.com/fr/boutiques/le-petit-lunetier",
    "https://www.ebuyclub.com/reduction-le-petit-lunetier-11972",
    "https://www.radins.com/code-promo/le-petit-lunetier",
    "https://www.bon-reduc.com/code-promo/le-petit-lunetier",
    "https://www.groupon.fr/code-promo/magasins/le-petit-lunetier",
    "https://www.retailmenot.fr/codes-promo/le-petit-lunetier",
    "https://codepromo.lefigaro.fr/code-promo/le-petit-lunetier",
    "https://codespromo.ouest-france.fr/code-promo/le-petit-lunetier",
    "https://codepromo.bfmtv.com/code-promo/le-petit-lunetier",
    "https://codespromo.futura-sciences.com/code-promo/le-petit-lunetier",
    "https://www.cuponation.fr/code-promo-le-petit-lunetier",
    "https://www.bravopromo.fr/code-promo-le-petit-lunetier.html",
    "https://www.reduc.fr/code-promo/le-petit-lunetier",
    "https://www.reducavenue.com/code-promo/le-petit-lunetier",
    "https://www.couponnetwork.fr/le-petit-lunetier",
    "https://www.remisesetreductions.fr/le-petit-lunetier",
    "https://www.joinhoney.com/shop/le-petit-lunetier",
    "https://franceselection.couponasion.com/le-petit-lunetier",
    "https://www.shoop.fr/cashback/le-petit-lunetier",
    "https://www.karmanow.com/shop/le-petit-lunetier",
    "https://www.getdirecto.com/le-petit-lunetier",
    "https://joinpouch.com/fr/le-petit-lunetier"
]

motif_code = r"LPL-[A-Z0-9]{4}"

# --- FONCTIONS SHOPIFY ---
def delete_shopify_discount(rule_id):
    if not rule_id: return
    url = f"https://{SHOPIFY_STORE}/admin/api/2024-04/graphql.json"
    headers = {"X-Shopify-Access-Token": SHOPIFY_TOKEN, "Content-Type": "application/json"}
    mutation = "mutation discountCodeDelete($id: ID!) { discountCodeDelete(id: $id) { deletedCodeDiscountId } }"
    try:
        std_requests.post(url, json={"query": mutation, "variables": {"id": rule_id}}, headers=headers)
    except Exception as e:
        print(f"Erreur suppression Shopify pour {rule_id}: {e}")

# --- FONCTION DE BLOCAGE ---
def bloquer_fraudeur(code_trouve):
    # 1. On cherche à qui appartient ce code et s'il est déjà bloqué
    q_find = f"SELECT owner_email, shopify_rule_id, status FROM `{PROJECT_ID}.shopify_data_eu.referral_codes` WHERE code = @code LIMIT 1"
    
    # CORRECTION : Tout sur une seule ligne pour éviter la casse
    res = list(client.query(q_find, job_config=bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("code", "STRING", code_trouve)])))
    
    if not res:
        print(f"   -> ⚪ Code {code_trouve} ignoré (Faux code ou non trouvé en base).")
        return
        
    c = res[0]
    
    if c.status == 'BLOCKED_PUBLIC':
        print(f"   -> ♻️ Code {code_trouve} déjà traité et bloqué précédemment.")
        return
        
    owner_email = c.owner_email
    print(f"   -> 🚨 Cible verrouillée : {owner_email} (Code: {code_trouve})")

    # 2. On bloque le code LPL sur Shopify
    if c.shopify_rule_id:
        delete_shopify_discount(c.shopify_rule_id)

    # 3. On cherche et on détruit sa cagnotte (KDO) active s'il en a une
    q_kdo = f"SELECT code, shopify_rule_id FROM `{PROJECT_ID}.shopify_data_eu.referral_codes` WHERE owner_email = @email AND code LIKE 'KDO-%' AND status = 'ACTIVE'"
    
    # CORRECTION : Tout sur une seule ligne
    kdos = list(client.query(q_kdo, job_config=bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("email", "STRING", owner_email)])))
    
    for kdo in kdos:
        if kdo.shopify_rule_id:
            delete_shopify_discount(kdo.shopify_rule_id)
        print(f"   -> 💥 Cagnotte associée détruite : {kdo.code}")

    # 4. On passe TOUS les codes de ce fraudeur en BLOCKED_PUBLIC dans BigQuery
    q_update = f"UPDATE `{PROJECT_ID}.shopify_data_eu.referral_codes` SET status = 'BLOCKED_PUBLIC' WHERE owner_email = @email"
    
    # CORRECTION : Tout sur une seule ligne
    client.query(q_update, job_config=bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("email", "STRING", owner_email)])).result()
    
    print(f"   -> ✅ Compte de {owner_email} intégralement suspendu.")

# --- SCANNER ---
def scanner_les_sites():
    codes_trouves = set() 

    for url in urls_a_scanner:
        html_content = ""
        methode_reussie = None
        print(f"\nRecherche sur : {url}")
        
        # On utilise une "Session" pour retenir les cookies anti-bots entre la page 1 et les clics cachés
        session = requests.Session(impersonate="chrome110")
        
        # --- TENTATIVE 1 : Le "Vrai Utilisateur Chrome" ---
        try:
            reponse = session.get(url, timeout=10)
            if reponse.status_code == 200:
                html_content = reponse.text
                methode_reussie = "chrome"
                print(" -> ✅ Accès direct réussi.")
        except Exception:
            pass

        # --- TENTATIVE 2 : Le "Vrai Robot SEO Googlebot" ---
        if not html_content:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"})
                with urllib.request.urlopen(req, timeout=10) as reponse:
                    html_content = reponse.read().decode('utf-8')
                    methode_reussie = "googlebot"
                    print(" -> ✅ Accès Googlebot réussi.")
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    print(" -> ⚪ Page non existante (404), on ignore.")
                    continue
            except Exception:
                pass

        # --- TENTATIVE 3 : Le Proxy Public Gratuit (AllOrigins) ---
        if not html_content:
            try:
                url_encodee = urllib.parse.quote(url)
                url_proxy = f"https://api.allorigins.win/get?url={url_encodee}"
                reponse_proxy = std_requests.get(url_proxy, timeout=15)
                if reponse_proxy.status_code == 200:
                    data = reponse_proxy.json()
                    if "contents" in data and data["contents"]:
                        html_content = data["contents"]
                        methode_reussie = "proxy"
                        print(" -> ✅ Accès via Proxy réussi.")
            except Exception:
                print(" -> ❌ Échec total de la lecture.")

        # --- ANALYSE ET DEEP SCAN AVANCÉ ---
        if html_content:
            # 1. Recherche sur la page principale
            resultats = re.findall(motif_code, html_content)
            
            # 2. Construction de la liste des pages cachées à visiter
            liens_a_visiter = []
            
            # A. Les liens 'voir_reduc.asp' classiques
            liens_caches = re.findall(r"voir_reduc\.asp\?[^'\"]+", html_content)
            for lien in liens_caches:
                lien_propre = lien.replace("&amp;", "&")
                liens_a_visiter.append(urllib.parse.urljoin(url, lien_propre))
                
            # B. La technique d'encodage Base64 (PlanReduc)
            partenaires_b64 = re.findall(r"partenaire1\(['\"]([A-Za-z0-9+/=]+)['\"]", html_content)
            for b64_str in partenaires_b64:
                try:
                    # On décode la chaîne mystère
                    decoded_url = base64.b64decode(b64_str).decode('utf-8')
                    # On ajoute ce nouveau lien à notre liste de choses à visiter
                    liens_a_visiter.append(urllib.parse.urljoin(url, decoded_url))
                except Exception:
                    pass
            
            # C. NOUVEAU : Reconstruction des URLs de modales (Loveminty & Codepromo.club)
            url_sans_param = url.split('?')[0] # On s'assure d'avoir une URL de base propre
            
            # 1. Loveminty : extraction de l'ID après 'obj' (ex: /rort/obj53c139b498042918)
            ids_loveminty = re.findall(r"/rort/obj([a-zA-Z0-9]+)", html_content)
            for cid in ids_loveminty:
                liens_a_visiter.append(f"{url_sans_param}?bid={cid}")
                
            # 2. Codepromo.club : extraction de l'ID après 'sie' (ex: /klicken/sie53c139b498042918)
            ids_codepromo = re.findall(r"/klicken/sie([a-zA-Z0-9]+)", html_content)
            for cid in ids_codepromo:
                liens_a_visiter.append(f"{url_sans_param}?club={cid}&code=1")
            
            # On supprime les doublons de liens
            liens_a_visiter = list(set(liens_a_visiter))
            
            # 3. L'exécution du Deep Scan
            if liens_a_visiter:
                print(f" -> 🕵️  Deep Scan activé : {len(liens_a_visiter)} page(s) cachée(s) interceptée(s)...")
                for url_revelation in liens_a_visiter:
                    texte_cache = ""
                    try:
                        # IMPORTANT : On visite la page cachée avec EXACTEMENT la même méthode et les mêmes cookies!
                        if methode_reussie == "chrome":
                            rep_cachee = session.get(url_revelation, timeout=10)
                            if rep_cachee.status_code == 200:
                                texte_cache = rep_cachee.text
                                
                        elif methode_reussie == "googlebot":
                            req = urllib.request.Request(url_revelation, headers={"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"})
                            with urllib.request.urlopen(req, timeout=10) as rep:
                                texte_cache = rep.read().decode('utf-8')
                                
                        elif methode_reussie == "proxy":
                            url_encodee = urllib.parse.quote(url_revelation)
                            url_proxy = f"https://api.allorigins.win/get?url={url_encodee}"
                            rep_proxy = std_requests.get(url_proxy, timeout=15)
                            if rep_proxy.status_code == 200:
                                data = rep_proxy.json()
                                if "contents" in data and data["contents"]:
                                    texte_cache = data["contents"]

                        # On cherche le code dans cette nouvelle page révélée
                        if texte_cache:
                            resultats_caches = re.findall(motif_code, texte_cache)
                            if resultats_caches:
                                resultats.extend(resultats_caches)
                    except Exception:
                        pass
            
            # 4. Bilan et Blocage
            if resultats:
                resultats_uniques = list(set(resultats))
                for code in resultats_uniques:
                    codes_trouves.add(code)
                    print(f" -> ⚠️ ALERTE : Code '{code}' détecté sur la page!")
                    # ACTION DE BLOCAGE IMMÉDIATE
                    bloquer_fraudeur(code)
            else:
                print(" -> RAS : Aucun code compromis.")

    return list(codes_trouves)

if __name__ == "__main__":
    print("🚀 Lancement du scan de masse anti-pillage (30 sites)...")
    codes_a_bloquer = scanner_les_sites()
    
    print("\n=================================")
    print("      BILAN DU SCAN GLOBAL      ")
    print("=================================")
    if len(codes_a_bloquer) > 0:
        print(f"🚨 {len(codes_a_bloquer)} CODE(S) À DÉSACTIVER : {codes_a_bloquer}")
    else:
        print("✅ Aucun code LPL-XXXX compromis n'a été trouvé.")
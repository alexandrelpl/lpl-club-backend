-- ============================================================
-- MISE À JOUR dim_unified_customers — mai 2026
-- Transition : shipping_method → produit "Adhésion LPL Club"
--
-- À exécuter dans la console BigQuery :
-- https://console.cloud.google.com/bigquery?project=shopify-data-ltv
-- ============================================================

CREATE OR REPLACE TABLE `shopify-data-ltv.shopify_data_eu.dim_unified_customers` AS

WITH AllClients AS (
    SELECT LOWER(email) AS email, NULLIF(TRIM(phone), '') AS phone
    FROM `shopify-data-ltv.shopify_data_eu.custom_transactions_history`
    WHERE email IS NOT NULL AND email != ''
    UNION DISTINCT
    SELECT LOWER(customer_email) AS email, NULLIF(TRIM(customer_mobile_phone), '') AS phone
    FROM `stable-splicer-294813.dwh_datasource_sales.transaction_details_visits`
    WHERE customer_email IS NOT NULL AND customer_email != ''
    UNION DISTINCT
    SELECT LOWER(email) AS email, NULL AS phone
    FROM `shopify-data-ltv.shopify_data_eu.manual_lpl_club_members`
    WHERE email IS NOT NULL AND email != ''
),

QualifyingOrders AS (

    -- WEB — ancien système (méthode de livraison "LPL Club")
    SELECT LOWER(t.email) AS email, DATE(t.order_date) AS qualifying_date, 'WEB' AS source
    FROM `shopify-data-ltv.shopify_data_eu.custom_transactions_history` t
    WHERE LOWER(t.shipping_method) LIKE '%lpl club%'

    UNION ALL

    -- WEB — nouveau système (produit "Adhésion LPL Club" — depuis mai 2026)
    -- Détecté par product_title (immédiat) et sku (après prochaine rotation cron_sync)
    SELECT LOWER(t.email) AS email, DATE(p.order_date) AS qualifying_date, 'WEB' AS source
    FROM `shopify-data-ltv.shopify_data_eu.transactions_products_2020` p
    JOIN `shopify-data-ltv.shopify_data_eu.custom_transactions_history` t
      ON t.client_id = p.client_id
     AND DATE(t.order_date) = DATE(p.order_date)
    WHERE LOWER(p.product_title) = 'adhésion lpl club'

    UNION ALL

    -- RETAIL — filtre >= 2026-03-20 (exclut le backfill Dacker antérieur à l'ouverture du programme)
    SELECT LOWER(customer_email) AS email, CAST(invoice_creation_datetime AS DATE) AS qualifying_date, 'RETAIL' AS source
    FROM `stable-splicer-294813.dwh_datasource_sales.transaction_details_visits`
    WHERE UPPER(CAST(lplclub2026 AS STRING)) IN ('TRUE', '1', 'OUI', 'YES')
      AND CAST(invoice_creation_datetime AS DATE) >= '2026-03-20'

    UNION ALL

    -- MANUEL
    SELECT LOWER(email) AS email, DATE(added_at) AS qualifying_date, 'MANUEL' AS source
    FROM `shopify-data-ltv.shopify_data_eu.manual_lpl_club_members`

),

LatestQualifying AS (
    SELECT
        email,
        MAX(qualifying_date) AS last_club_order_date,
        ARRAY_AGG(source ORDER BY qualifying_date DESC LIMIT 1)[OFFSET(0)] AS latest_source
    FROM QualifyingOrders
    WHERE email IS NOT NULL
    GROUP BY email
)

SELECT
    c.email,
    MAX(c.phone) AS phone,
    l.last_club_order_date,
    l.latest_source AS source,
    DATE_ADD(l.last_club_order_date, INTERVAL 1 YEAR) AS lpl_club_expiry_date,
    CASE
        WHEN l.last_club_order_date IS NOT NULL
         AND DATE_ADD(l.last_club_order_date, INTERVAL 1 YEAR) >= CURRENT_DATE()
        THEN TRUE
        ELSE FALSE
    END AS is_lpl_club
FROM AllClients c
LEFT JOIN LatestQualifying l ON c.email = l.email
GROUP BY c.email, l.last_club_order_date, l.latest_source;

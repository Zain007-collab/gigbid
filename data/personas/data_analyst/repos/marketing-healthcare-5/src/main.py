# marketing-healthcare-5 (synthetic)

SELECT event_name FROM `proj.analytics_123.events_*`

function refresh() { SpreadsheetApp.getActive().getSheetByName('KPI') }

// Looker Studio data source: BigQuery custom query

SELECT customer_id, SUM(amount) FROM orders GROUP BY 1

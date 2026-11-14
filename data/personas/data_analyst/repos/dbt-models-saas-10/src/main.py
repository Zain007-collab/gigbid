# dbt-models-saas-10 (synthetic)

from airflow import DAG
from airflow.operators.python import PythonOperator

{{ config(materialized='incremental') }}
select * from {{ ref('stg_orders') }}

ALTER WAREHOUSE reporting SET AUTO_SUSPEND = 60;

SELECT customer_id, SUM(amount) FROM orders GROUP BY 1

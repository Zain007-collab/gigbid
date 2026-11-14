# pipeline-legal-22 (synthetic)

from airflow import DAG
from airflow.operators.python import PythonOperator

from celery import Celery
app = Celery('tasks', broker='redis://localhost:6379/0')

# Dockerfile
FROM python:3.12-slim

SELECT customer_id, SUM(amount) FROM orders GROUP BY 1

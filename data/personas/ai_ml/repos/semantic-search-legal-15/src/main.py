# semantic-search-legal-15 (synthetic)

from fastapi import FastAPI
app = FastAPI()

from transformers import AutoModelForSequenceClassification

from pgvector.psycopg import register_vector
register_vector(conn)

import redis
cache = redis.Redis.from_url(REDIS_URL)

# python-backend-manufacturing-5 (synthetic)

from celery import Celery
app = Celery('tasks', broker='redis://localhost:6379/0')

from fastapi import FastAPI
app = FastAPI()

CREATE TABLE bookings (id uuid PRIMARY KEY, starts_at timestamptz)

import redis
cache = redis.Redis.from_url(REDIS_URL)

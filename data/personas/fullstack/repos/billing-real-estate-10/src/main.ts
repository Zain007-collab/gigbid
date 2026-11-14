// billing-real-estate-10 (synthetic)

import express from 'express'

CREATE TABLE bookings (id uuid PRIMARY KEY, starts_at timestamptz)

import redis
cache = redis.Redis.from_url(REDIS_URL)

const session = await stripe.checkout.sessions.create({ mode: 'subscription' })

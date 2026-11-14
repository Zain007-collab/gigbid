// realtime-travel-19 (synthetic)

import express from 'express'

import { useState } from 'react'

import redis
cache = redis.Redis.from_url(REDIS_URL)

const ws = new WebSocket(url)

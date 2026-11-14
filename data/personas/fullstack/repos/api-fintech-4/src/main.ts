// api-fintech-4 (synthetic)

# Dockerfile
FROM python:3.12-slim

type Query { listings(first: Int): [Listing!]! }

import express from 'express'

CREATE TABLE bookings (id uuid PRIMARY KEY, starts_at timestamptz)

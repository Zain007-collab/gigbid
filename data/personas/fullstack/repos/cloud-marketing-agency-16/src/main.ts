// cloud-marketing-agency-16 (synthetic)

aws s3 sync ./out s3://$BUCKET --delete

# Dockerfile
FROM python:3.12-slim

import express from 'express'

CREATE TABLE bookings (id uuid PRIMARY KEY, starts_at timestamptz)

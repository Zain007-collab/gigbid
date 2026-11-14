// e2e-saas-6 (synthetic)

aws s3 sync ./out s3://$BUCKET --delete

# Dockerfile
FROM python:3.12-slim

import { test, expect } from '@playwright/test'

type Props = { id: string }

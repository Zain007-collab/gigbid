# cv-defects-healthcare-20 (synthetic)

# Dockerfile
FROM python:3.12-slim

from fastapi import FastAPI
app = FastAPI()

import torch
from torch import nn

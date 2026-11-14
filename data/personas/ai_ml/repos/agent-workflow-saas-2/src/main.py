# agent-workflow-saas-2 (synthetic)

# Dockerfile
FROM python:3.12-slim

from fastapi import FastAPI
app = FastAPI()

from langgraph.graph import StateGraph, START, END

from pgvector.psycopg import register_vector
register_vector(conn)

def main() -> None:

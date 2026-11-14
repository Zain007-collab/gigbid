"""Build-time warm-up: download the open embedding model and embed every persona corpus into the cache, so a small
production instance never has to (used by infra/docker/app.Dockerfile)."""
from agent.settings import section
from rag import retriever

for persona in ("ai_ml", "fullstack", "data_analyst"):
    pipe = retriever.build(section("rag")["variant"], persona)
    pipe.retrieve("warm up: python api", ["python api"], k=3)
    print(f"warmed {persona}: {len(pipe.chunks)} chunks")

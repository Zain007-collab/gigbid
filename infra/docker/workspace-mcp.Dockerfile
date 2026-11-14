# workspace-mcp service (built from the shared recipe in python-base.Dockerfile)
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.lock.txt .
RUN pip install --only-binary :all: -r requirements.lock.txt
COPY agent agent
COPY apps/api apps/api
COPY apps/__init__.py apps/__init__.py
COPY datagen datagen
COPY gigsim gigsim
COPY mcp_servers mcp_servers
COPY rag rag
COPY shared shared
COPY worker worker
COPY data/personas data/personas
COPY data/policy_pack data/policy_pack
EXPOSE 8103
CMD ["python","-m","mcp_servers.workspace.server"]

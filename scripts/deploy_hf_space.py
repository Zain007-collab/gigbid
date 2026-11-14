"""Deploy GigBid to a Hugging Face Docker Space (single-container image).

    python scripts/deploy_hf_space.py [--space gigbid] [--private]

Reads secrets from the git-ignored .env, creates the Space if needed, sets Space *secrets* through the HF API (never
written to files), and uploads only committed files (git archive HEAD) plus a root Dockerfile and Space README.
"""
from __future__ import annotations

import argparse
import io
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent import settings  # noqa: E402,F401  (loads .env)

SECRETS = {  # Space secret name -> .env variable
    "DATABASE_URL": "DEPLOY_DATABASE_URL", "GROQ_API_KEY": "GROQ_API_KEY", "OPENROUTER_API_KEY": "OPENROUTER_API_KEY",
    "SESSION_SECRET": "SESSION_SECRET", "TOKEN_ENCRYPTION_KEY": "TOKEN_ENCRYPTION_KEY",
    "GIGSIM_ADMIN_TOKEN": "GIGSIM_ADMIN_TOKEN", "MARKETPLACE_SERVICE_TOKEN": "MARKETPLACE_SERVICE_TOKEN",
    "REVIEWER_EMAIL": "REVIEWER_EMAIL", "REVIEWER_PASSWORD": "REVIEWER_PASSWORD",
    "LANGFUSE_PUBLIC_KEY": "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY": "LANGFUSE_SECRET_KEY",
    "LANGFUSE_HOST": "LANGFUSE_HOST",
}
SPACE_README = """---
title: GigBid
emoji: 🧭
colorFrom: blue
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
---
GigBid — a multi-agent freelancer copilot (LangGraph + MCP + advanced RAG). Every proposal and message waits for
human approval. Source: https://github.com/Zain007-collab/gigbid
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--space", default="gigbid")
    ap.add_argument("--private", action="store_true")
    a = ap.parse_args()
    from huggingface_hub import HfApi

    token = os.environ.get("HF_TOKEN") or sys.exit("HF_TOKEN missing in .env")
    api = HfApi(token=token)
    user = api.whoami()["name"]
    repo_id = f"{user}/{a.space}"
    api.create_repo(repo_id, repo_type="space", space_sdk="docker", private=a.private, exist_ok=True)
    for name, var in SECRETS.items():
        if os.environ.get(var):
            api.add_space_secret(repo_id, name, os.environ[var])
    print("secrets set:", [n for n, v in SECRETS.items() if os.environ.get(v)])

    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    archive = subprocess.run(["git", "archive", "HEAD"], cwd=ROOT, capture_output=True, check=True).stdout
    with tempfile.TemporaryDirectory() as work:
        tarfile.open(fileobj=io.BytesIO(archive)).extractall(work)
        (Path(work) / "Dockerfile").write_text((ROOT / "infra/docker/app.Dockerfile").read_text())
        (Path(work) / "README.md").write_text(SPACE_README)
        api.upload_folder(folder_path=work, repo_id=repo_id, repo_type="space", commit_message=f"deploy {head}",
                          delete_patterns=["*"])
    print(f"deployed {head} -> https://huggingface.co/spaces/{repo_id}")
    print(f"app URL: https://{user.lower()}-{a.space}.hf.space   (build logs: https://huggingface.co/spaces/{repo_id}?logs=build)")


if __name__ == "__main__":
    main()

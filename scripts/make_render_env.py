"""Write deploy/render.env (git-ignored) from .env, ready for Render's 'Add from .env' in the Environment tab."""
import os
from pathlib import Path

from agent import settings  # noqa: F401  (loads .env)

MAP = {"DATABASE_URL": "DEPLOY_DATABASE_URL", "GROQ_API_KEY": "GROQ_API_KEY", "OPENROUTER_API_KEY": "OPENROUTER_API_KEY",
       "SESSION_SECRET": "SESSION_SECRET", "TOKEN_ENCRYPTION_KEY": "TOKEN_ENCRYPTION_KEY",
       "GIGSIM_ADMIN_TOKEN": "GIGSIM_ADMIN_TOKEN", "MARKETPLACE_SERVICE_TOKEN": "MARKETPLACE_SERVICE_TOKEN",
       "REVIEWER_EMAIL": "REVIEWER_EMAIL", "REVIEWER_PASSWORD": "REVIEWER_PASSWORD"}
out = Path(__file__).resolve().parents[1] / "deploy" / "render.env"
out.parent.mkdir(exist_ok=True)
out.write_text("".join(f"{k}={os.environ[v]}\n" for k, v in MAP.items() if os.environ.get(v)))
out.chmod(0o600)
print(f"wrote {out} ({sum(1 for v in MAP.values() if os.environ.get(v))} variables) — paste it into Render, then delete it")

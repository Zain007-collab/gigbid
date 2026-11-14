"""Ingest GitHub repositories into a portfolio through GitHub's official MCP server (B1, A5).

    python -m rag.ingest.github_ingest --user <github-login> --persona ai_ml [--max-repos 10]

Uses the hosted GitHub MCP server (https://api.githubcopilot.com/mcp/) over Streamable HTTP with a read-only,
fine-grained PAT (GITHUB_PERSONAL_ACCESS_TOKEN, public repositories only). Only read tools are called
(`search_repositories`, `get_file_contents`). Each repo becomes data/personas/<persona>/repos/<repo>/ with its README
and up to N top-level source files; the Portfolio server re-indexes on the next query (only new chunks are embedded).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GITHUB_MCP_URL = os.environ.get("GITHUB_MCP_URL", "https://api.githubcopilot.com/mcp/")
READ_TOOLS = {"search_repositories", "get_file_contents"}           # allowlist: never call write tools
CODE_EXT = (".py", ".ts", ".tsx", ".js", ".sql", ".ipynb", ".go", ".rs", ".java", ".r", ".R", ".scala")


def _text(result) -> str:
    out = []
    for c in result.content:
        if getattr(c, "text", None):
            out.append(c.text)
        elif getattr(c, "resource", None) is not None and getattr(c.resource, "text", None):
            out.append(c.resource.text)
    return "\n".join(out)


async def ingest(user: str, persona: str, max_repos: int, max_files: int) -> list[str]:
    from langchain_mcp_adapters.client import MultiServerMCPClient
    from agent import settings  # noqa: F401  (loads .env)

    pat = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN")
    if not pat:
        raise SystemExit("GITHUB_PERSONAL_ACCESS_TOKEN missing (read-only fine-grained PAT, public repos)")
    client = MultiServerMCPClient({"github": {"transport": "streamable_http", "url": GITHUB_MCP_URL,
                                              "headers": {"Authorization": f"Bearer {pat}"}}})
    done = []
    async with client.session("github") as s:
        async def call(tool: str, args: dict) -> str:
            assert tool in READ_TOOLS, f"{tool} is not an allowed read tool"
            return _text(await s.call_tool(tool, args))

        found = json.loads(await call("search_repositories", {"query": f"user:{user} fork:false", "perPage": max_repos}))
        repos = found.get("items", found if isinstance(found, list) else [])[:max_repos]
        for repo in repos:
            name = repo["name"]
            dest = ROOT / "data" / "personas" / persona / "repos" / f"gh-{re.sub(r'[^A-Za-z0-9_.-]', '-', name)}"
            (dest / "src").mkdir(parents=True, exist_ok=True)
            try:
                readme = await call("get_file_contents", {"owner": user, "repo": name, "path": "README.md"})
            except Exception:  # noqa: BLE001 - repo without a README
                readme = repo.get("description") or name
            (dest / "README.md").write_text(f"# {name}\n\n{readme}\n\n_Source: github.com/{user}/{name} (ingested via GitHub MCP)._\n")
            listing = await call("get_file_contents", {"owner": user, "repo": name, "path": "/"})
            try:
                entries = json.loads(listing)
            except json.JSONDecodeError:
                entries = []
            files = [e["path"] for e in entries if isinstance(e, dict) and e.get("type") == "file"
                     and e.get("path", "").endswith(CODE_EXT)][:max_files]
            for path in files:
                body = await call("get_file_contents", {"owner": user, "repo": name, "path": path})
                (dest / "src" / Path(path).name).write_text(body[:20000])
            done.append(f"{name} ({len(files)} code files)")
    return done


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True)
    ap.add_argument("--persona", required=True)
    ap.add_argument("--max-repos", type=int, default=10)
    ap.add_argument("--max-files", type=int, default=5)
    a = ap.parse_args()
    for r in asyncio.run(ingest(a.user, a.persona, a.max_repos, a.max_files)):
        print("ingested", r)


if __name__ == "__main__":
    main()

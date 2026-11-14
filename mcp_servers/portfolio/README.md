# GigBid Portfolio MCP server

Give any MCP host (Claude Desktop, Cursor, your own agent) **read-only, evidence-grade access to a freelancer
portfolio**: case studies, repo READMEs and code, client reviews, a CV and past proposals with their outcomes.
Retrieval is the configuration that won GigBid's evaluation (requirement decomposition + hybrid BM25/dense +
multi-vector project grouping; open embeddings, runs on a CPU).

## Install and run

```bash
# one command, no clone (needs uv):
PORTFOLIO_DIR=/path/to/my-portfolio uvx --from git+https://github.com/<you>/gigbid gigbid-portfolio-mcp

# or from a clone
PORTFOLIO_DIR=data/personas/ai_ml python -m mcp_servers.portfolio.server        # stdio
MCP_TRANSPORT=streamable-http PORTFOLIO_SERVICE_TOKEN=... python -m mcp_servers.portfolio.server   # HTTP :8102
```

### Claude Desktop (`claude_desktop_config.json`)
```json
{
  "mcpServers": {
    "my-portfolio": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/<you>/gigbid", "gigbid-portfolio-mcp"],
      "env": { "PORTFOLIO_DIR": "/Users/me/portfolio" }
    }
  }
}
```

## Portfolio folder layout
```
my-portfolio/
  cv.pdf | cv.md
  case_studies/*.md          # optional front matter: project, industry, date
  repos/<repo>/README.md      # and repos/<repo>/src/* (code is indexed too — skills hidden in imports count)
  reviews.json               # [{review_id, project_id, rating, text, date, client_industry}]
  proposals.jsonl            # {proposal_id, job_title, text, outcome: won|replied|ignored, cited_projects, date}
  uploads/*.md               # new work: picked up automatically, only new chunks are embedded
```
Try it with a bundled synthetic persona: `PORTFOLIO_DIR=data/personas/fullstack`.

## Tools, resources, prompts
| Tool | What it returns |
|---|---|
| `search_evidence(requirement, k=5, filters?)` | evidence items with type, source, date, outcome, ranking score, cosine similarity, snippet, and `supported` |
| `get_case_study(id)` | full case study plus ids of every other item about the same project |
| `list_skills()` | skills evidenced anywhere (prose **and** code), with counts and example item ids |

Resources: `portfolio://case/{id}`, `portfolio://proposal/{id}`, `portfolio://cv`.
Prompts: *Answer a screening question from my portfolio*, *Find evidence for every requirement in a job post*,
*Tighten a proposal and check every claim*.

All tools are annotated `readOnlyHint=true`; the server never writes to your portfolio. Job-post text you paste into
the prompts is treated as untrusted data.

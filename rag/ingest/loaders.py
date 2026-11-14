"""Load a persona corpus into typed documents with metadata (B1).

Every document carries: persona, type, project, skills (if stated), date,
client_industry and, for proposals, outcome. `_manifest.json` (ground truth)
is never loaded.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Doc:
    doc_id: str
    text: str
    meta: dict = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()[:16]


_FRONT = re.compile(r"^---\n(.*?)\n---\n", re.S)


def _frontmatter(text: str) -> tuple[dict, str]:
    m = _FRONT.match(text)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, text[m.end():]


def _pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:  # md twin is always present for synthetic personas
        return ""
    return "\n".join(p.extract_text() or "" for p in PdfReader(str(path)).pages)


def load_persona(persona_dir: Path) -> list[Doc]:
    pid = persona_dir.name
    docs: list[Doc] = []
    base = {"persona": pid}

    cv_pdf = persona_dir / "cv.pdf"
    cv_text = _pdf_text(cv_pdf) if cv_pdf.exists() else ""
    if not cv_text and (persona_dir / "cv.md").exists():
        cv_text = (persona_dir / "cv.md").read_text()
    if cv_text:
        docs.append(Doc(f"{pid}:cv", cv_text, {**base, "type": "cv", "source": "cv.pdf"}))

    for f in sorted((persona_dir / "case_studies").glob("*.md")):
        fm, body = _frontmatter(f.read_text())
        docs.append(Doc(f"{pid}:case:{fm.get('project', f.stem)}", body, {
            **base, "type": "case_study", "project": fm.get("project", f.stem), "date": fm.get("date"),
            "client_industry": fm.get("industry"), "source": str(f.relative_to(persona_dir))}))

    repos = persona_dir / "repos"
    for r in sorted(p for p in repos.iterdir() if p.is_dir()) if repos.exists() else []:
        project = _project_for_repo(persona_dir, r.name)
        readme = r / "README.md"
        if readme.exists():
            docs.append(Doc(f"{pid}:readme:{r.name}", readme.read_text(), {
                **base, "type": "readme", "project": project, "repo": r.name,
                "source": str(readme.relative_to(persona_dir))}))
        for code in sorted((r / "src").glob("*")) if (r / "src").exists() else []:
            docs.append(Doc(f"{pid}:code:{r.name}:{code.name}", code.read_text(), {
                **base, "type": "code", "project": project, "repo": r.name,
                "source": str(code.relative_to(persona_dir))}))

    if (persona_dir / "reviews.json").exists():
        for rv in json.loads((persona_dir / "reviews.json").read_text()):
            docs.append(Doc(f"{pid}:review:{rv['review_id']}", rv["text"], {
                **base, "type": "review", "project": rv.get("project_id"), "date": rv.get("date"),
                "client_industry": rv.get("client_industry"), "rating": rv.get("rating")}))

    if (persona_dir / "proposals.jsonl").exists():
        for line in (persona_dir / "proposals.jsonl").read_text().splitlines():
            p = json.loads(line)
            cited = p.get("cited_projects") or []
            docs.append(Doc(f"{pid}:proposal:{p['proposal_id']}", f"{p['job_title']}\n\n{p['text']}", {
                **base, "type": "proposal", "project": cited[0] if cited else None, "cited_projects": cited,
                "outcome": p.get("outcome"), "date": p.get("date"), "client_industry": p.get("client_industry"),
                "category": p.get("category")}))

    # extra uploads from the UI land in uploads/ as markdown
    for f in sorted((persona_dir / "uploads").glob("*.md")) if (persona_dir / "uploads").exists() else []:
        fm, body = _frontmatter(f.read_text())
        docs.append(Doc(f"{pid}:upload:{f.stem}", body, {
            **base, "type": fm.get("type", "case_study"), "project": fm.get("project", f"upload-{f.stem}"),
            "source": str(f.relative_to(persona_dir))}))
    return docs


def _project_for_repo(persona_dir: Path, repo: str) -> str | None:
    """Repo -> project id via the repo name's numeric suffix, which matches the case-study id."""
    m = re.search(r"-(\d+)$", repo)
    return f"{persona_dir.name}-p{int(m.group(1)):02d}" if m else None

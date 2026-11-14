"""Prompt loading and untrusted-content delimiting.

Every prompt is a versioned markdown file in agent/prompts/ (no inline prompts).
Untrusted text (job posts, client messages) is wrapped in tags after any tag
look-alikes inside it are neutralised, and the system prompt says to treat it
as data only.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

PROMPT_DIR = Path(__file__).with_name("prompts")


@lru_cache(maxsize=64)
def load(name: str) -> str:
    text = (PROMPT_DIR / f"{name}.md").read_text()
    return re.sub(r"^<!--.*?-->\n", "", text, flags=re.S)  # strip version header comment


def version(name: str) -> str:
    m = re.match(r"<!--\s*version:\s*(\S+)", (PROMPT_DIR / f"{name}.md").read_text())
    return m.group(1) if m else "0"


def untrusted(tag: str, text: str) -> str:
    """Delimit untrusted content. Any '<' that could open/close our tags is defanged."""
    safe = re.sub(r"</?\s*untrusted[^>]*>", "[tag removed]", text, flags=re.I)
    return f"<untrusted_{tag}>\n{safe}\n</untrusted_{tag}>"


def render(name: str, **kw: str) -> str:
    out = load(name)
    for k, v in kw.items():
        out = out.replace("{{" + k + "}}", v)
    return out

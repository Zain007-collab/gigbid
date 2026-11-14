"""Repository rules CI enforces (assignment: 'Rules your CI must enforce').

1. No secrets committed (.env files, private keys, provider key patterns).
2. gigsim/ unchanged: the simulator and its seed are frozen by gigsim/CHECKSUMS.sha256
   (regenerate deliberately with `python scripts/check_repo_rules.py --freeze-gigsim`).
3. Every LLM prompt lives in agent/prompts/: no prompt-like string literals elsewhere in agent/.
4. Every number in the README results section comes from evals/results (scripts/results_table.py --check).
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
errors: list[str] = []


def tracked() -> list[str]:
    return subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()


def check_secrets(files: list[str]) -> None:
    pats = [re.compile(p) for p in (r"gsk_[A-Za-z0-9]{20,}", r"sk-[A-Za-z0-9]{32,}", r"sk-ant-[A-Za-z0-9-]{20,}",
                                    r"ghp_[A-Za-z0-9]{30,}", r"-----BEGIN [A-Z ]*PRIVATE KEY-----", r"AKIA[0-9A-Z]{16}")]
    for f in files:
        if Path(f).name == ".env" or (Path(f).name.startswith(".env.") and Path(f).name != ".env.example"):
            errors.append(f"secret file committed: {f}")
        p = ROOT / f
        if p.suffix in (".png", ".jpg", ".pdf", ".zip", ".gz") or not p.is_file() or p.stat().st_size > 2_000_000:
            continue
        text = p.read_text(errors="ignore")
        for pat in pats:
            if pat.search(text):
                errors.append(f"possible secret in {f}: {pat.pattern}")


def gigsim_digest() -> str:
    h = hashlib.sha256()
    for p in sorted((ROOT / "gigsim").rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and p.name != "CHECKSUMS.sha256":
            h.update(str(p.relative_to(ROOT)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def check_gigsim() -> None:
    f = ROOT / "gigsim" / "CHECKSUMS.sha256"
    if not f.exists() or f.read_text().strip() != gigsim_digest():
        errors.append("gigsim/ changed without re-freezing (run --freeze-gigsim deliberately and explain why)")


def check_prompts() -> None:
    prompt_like = re.compile(r'"""[^"]*\b(You are|You write|You verify|Classify the|Draft a)\b', re.S)
    for p in (ROOT / "agent").rglob("*.py"):
        if prompt_like.search(p.read_text()):
            errors.append(f"inline prompt in {p.relative_to(ROOT)} — move it to agent/prompts/")


if __name__ == "__main__":
    if "--freeze-gigsim" in sys.argv:
        (ROOT / "gigsim" / "CHECKSUMS.sha256").write_text(gigsim_digest() + "\n")
        print("froze gigsim/")
        sys.exit(0)
    check_secrets(tracked())
    check_gigsim()
    check_prompts()
    r = subprocess.run([sys.executable, "scripts/results_table.py", "--check"], cwd=ROOT)
    if r.returncode:
        errors.append("README results section is out of date with evals/results (run scripts/results_table.py)")
    for e in errors:
        print("FAIL:", e)
    print("repo rules:", "FAILED" if errors else "ok")
    sys.exit(1 if errors else 0)

"""PII masking for logs, traces and the audit log (E4)."""
from __future__ import annotations

import re
from typing import Any

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[EMAIL]"),
    # card numbers: 13–19 digits with optional spaces/dashes
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "[CARD]"),
    (re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"), "[IBAN]"),
    # phone: +country or long digit runs with separators
    (re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d(?!\w)"), "[PHONE]"),
    (re.compile(r"(?i)\b(?:paypal\.me|wa\.me|t\.me|venmo\.com|cash\.app)/\S+"), "[PAYMENT_HANDLE]"),
    (re.compile(r"(?i)\b(?:skype|telegram|whatsapp)\s*[:@]\s*\S+"), "[CONTACT_HANDLE]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer [TOKEN]"),
]


def mask(text: str) -> str:
    for pat, repl in _PATTERNS:
        text = pat.sub(repl, text)
    return text


def mask_obj(obj: Any) -> Any:
    """Recursively mask strings inside dicts/lists (for JSON audit payloads)."""
    if isinstance(obj, str):
        return mask(obj)
    if isinstance(obj, dict):
        return {k: ("[REDACTED]" if _is_secret_key(k) else mask_obj(v)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [mask_obj(v) for v in obj]
    return obj


def _is_secret_key(k: str) -> bool:
    k = k.lower()
    return any(s in k for s in ("token", "secret", "password", "authorization", "api_key"))

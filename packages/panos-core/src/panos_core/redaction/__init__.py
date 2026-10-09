"""Secret redaction shared by logging, audit and (later) prompt building (NFR-SEC-004).

Three layers:
1. known provider/credential formats (regex);
2. sensitive keys in mappings (``password``, ``api_key``, ...);
3. high-entropy tokens: long, mixed-case alphanumeric strings that look random.

Redaction is best-effort defense in depth. It complements, and does not replace,
keeping secrets out of logs and prompts by construction.
"""

import math
import re
from collections import Counter
from collections.abc import Mapping
from itertools import pairwise
from typing import Any, Final

REDACTED: Final = "[REDACTED]"

_SENSITIVE_KEY_RE: Final = re.compile(
    r"(pass(word|wd|phrase)?|secret|token|api[_-]?key|authori[sz]ation|^auth$|cookie|"
    r"credential|private[_-]?key|access[_-]?key|session[_-]?id)",
    re.IGNORECASE,
)

# (pattern, replacement). Order matters: specific provider keys before generic rules.
_VALUE_PATTERNS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
        REDACTED,
    ),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}"), REDACTED),  # Anthropic
    (re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}"), REDACTED),  # OpenAI
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"), REDACTED),  # GitHub
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}"), REDACTED),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"), REDACTED),  # Slack
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), REDACTED),  # AWS access key id
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), REDACTED),  # Google API key
    (re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}"), REDACTED),  # GitLab
    (re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"), REDACTED),  # npm
    (re.compile(r"\bhf_[A-Za-z0-9]{30,}\b"), REDACTED),  # Hugging Face
    (re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{20,}"), REDACTED),  # Stripe
    (re.compile(r"\bSG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}"), REDACTED),  # SendGrid
    (  # Azure connection-string keys
        re.compile(r"(?i)\b(AccountKey|SharedAccessKey|SharedAccessSignature)=[^;\s]+"),
        r"\1=" + REDACTED,
    ),
    (
        re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
        REDACTED,
    ),  # JWT
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]{8,}"), r"\1 " + REDACTED),
    (re.compile(r"(?i)([a-z][a-z0-9+.\-]*://[^\s:/@]+:)[^\s@/]+@"), r"\1" + REDACTED + "@"),
    (
        re.compile(
            r"(?i)\b((?:pass(?:word|wd)?|secret|token|api[_-]?key|access[_-]?key)\s*[=:]\s*)"
            r"([\"']?)[^\s\"',;]+\2"
        ),
        r"\1" + REDACTED,
    ),
)


# Candidate tokens for entropy analysis. "/" is excluded so file paths are not split
# into candidates; 32+ characters keeps ordinary identifiers out.
_ENTROPY_CANDIDATE_RE: Final = re.compile(r"(?<![A-Za-z0-9+_\-])[A-Za-z0-9+_\-]{32,}={0,2}")
ENTROPY_THRESHOLD: Final = 4.0  # bits/char; hex tops out at 4.0, random base64 is ~5-6


def shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = Counter(value)
    n = len(value)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


CLASS_SWITCH_THRESHOLD: Final = 0.4  # random base64 ≈ 0.6; CamelCase identifiers ≈ 0.2


def _char_class(c: str) -> int:
    return 0 if c.islower() else 1 if c.isupper() else 2 if c.isdigit() else 3


def _class_switch_ratio(value: str) -> float:
    if len(value) < 2:
        return 0.0
    switches = sum(_char_class(a) != _char_class(b) for a, b in pairwise(value))
    return switches / (len(value) - 1)


def looks_like_random_secret(token: str) -> bool:
    """Heuristic for unknown-format secrets.

    * all of upper, lower and digits present: keeps lowercase hex (commit SHAs, UUIDs,
      Panos IDs) and ordinary words out;
    * Shannon entropy above ``ENTROPY_THRESHOLD``;
    * frequent character-class switches: keeps long CamelCase identifiers out.
    """
    core = token.rstrip("=")
    has_classes = (
        any(c.islower() for c in core)
        and any(c.isupper() for c in core)
        and any(c.isdigit() for c in core)
    )
    return (
        has_classes
        and shannon_entropy(core) > ENTROPY_THRESHOLD
        and _class_switch_ratio(core) > CLASS_SWITCH_THRESHOLD
    )


def _redact_entropy(text: str) -> str:
    return _ENTROPY_CANDIDATE_RE.sub(
        lambda m: REDACTED if looks_like_random_secret(m.group(0)) else m.group(0), text
    )


def redact_text(text: str, *, entropy: bool = True) -> str:
    """Replace known secret patterns and, unless disabled, high-entropy tokens."""
    for pattern, replacement in _VALUE_PATTERNS:
        text = pattern.sub(replacement, text)
    return _redact_entropy(text) if entropy else text


def is_sensitive_key(key: str) -> bool:
    return bool(_SENSITIVE_KEY_RE.search(key))


def redact(value: Any, *, _depth: int = 0) -> Any:
    """Recursively redact secrets in strings, mappings and sequences.

    String values under sensitive keys are replaced entirely; other strings are scanned
    for secret patterns. Non-string scalars are returned unchanged.
    """
    if _depth > 20:
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {
            # Key-based redaction applies to string values only, so numeric fields
            # such as token counts stay visible.
            k: (
                REDACTED
                if isinstance(k, str) and isinstance(v, str) and v and is_sensitive_key(k)
                else redact(v, _depth=_depth + 1)
            )
            for k, v in value.items()
        }
    if isinstance(value, list | tuple | set | frozenset):
        items = [redact(v, _depth=_depth + 1) for v in value]
        return type(value)(items) if not isinstance(value, list) else items
    return value


__all__ = [
    "REDACTED",
    "is_sensitive_key",
    "looks_like_random_secret",
    "redact",
    "redact_text",
    "shannon_entropy",
]

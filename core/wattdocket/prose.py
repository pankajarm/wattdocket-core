"""Prose binding — INV-10.

The model writes the sentence; the code owns the digits. This module is the
guard that makes that true mechanically: every numeral in model output must
already exist in the computed payload, or the prose is rejected and the
renderer falls back to the template.

Why not just trust the prompt? Because reasoning models narrate arithmetic.
A single confident "about 190,000 tonnes" that appears nowhere in the ledger
is the exact failure this product exists to prevent, and a prompt is not a
guarantee. A regex plus a set membership check is.
"""

from __future__ import annotations

import re

NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


class UnboundProse(ValueError):
    """Model prose contained a number the ledger does not have."""


def extract_numerals(text: str) -> list[str]:
    """Every numeral in the text, normalised to a bare digit string."""
    out = []
    for m in NUMBER_RE.finditer(text or ""):
        raw = m.group(0).strip(",.")
        raw = raw.replace(",", "")
        if not raw:
            continue
        # normalise trailing zeros: 154000.0 and 154,000 are the same claim
        if "." in raw:
            raw = raw.rstrip("0").rstrip(".")
        out.append(raw)
    return out


def _payload_strings(payload: dict) -> set[str]:
    """All numbers present in the computed payload, in both raw and rounded form."""
    found: set[str] = set()
    stack = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, (list, tuple)):
            stack.extend(node)
        elif isinstance(node, bool):
            continue
        elif isinstance(node, (int, float)):
            for form in (f"{node}", f"{node:.0f}", f"{node:.1f}",
                         f"{node:,.0f}", f"{node:,.1f}"):
                s = form.replace(",", "")
                if "." in s:
                    s = s.rstrip("0").rstrip(".")
                found.add(s)
            found.add(str(abs(int(node))))
        elif isinstance(node, str):
            found.update(extract_numerals(node))
    return found


def unbound_numerals(text: str, payload: dict) -> list[str]:
    allowed = _payload_strings(payload)
    return [n for n in extract_numerals(text) if n not in allowed]


def prose_is_bound(text: str, payload: dict) -> bool:
    return not unbound_numerals(text, payload)


def assert_bound(text: str, payload: dict) -> str:
    bad = unbound_numerals(text, payload)
    if bad:
        raise UnboundProse(
            f"model prose contains numbers absent from the ledger: {bad}"
        )
    return text.strip()

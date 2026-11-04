"""The one place a model is allowed — and even here it cannot invent a digit.

`compose()` asks Muse Spark for a sentence about a computed payload. The reply
is then checked with `wattdocket.prose.assert_bound`: every numeral it contains
must already exist in the payload. If not, `UnboundProse` is raised and the
caller serves the template instead. The model has no path to the ledger and no
authority over arithmetic.

Env: META_API_KEY. MODEL_OFF=1 disables.
"""

from __future__ import annotations

import json
import os
import urllib.request

from wattdocket.prose import assert_bound

BASE = os.environ.get("META_API_BASE", "https://api.meta.ai/v1")
MODEL = os.environ.get("WATTDOCKET_EXPLAIN_MODEL", "muse-spark-1.3")
TIMEOUT = float(os.environ.get("WATTDOCKET_EXPLAIN_TIMEOUT", "4"))

SYSTEM = (
    "You explain a computed environmental reading to a member of the public. "
    "Rules you must not break: (1) you may only use numbers that appear in the "
    "payload you are given — never compute, never round to a new value, never "
    "estimate; (2) no legal conclusions and no health claims; (3) if a value is "
    "not published, say so plainly; (4) two sentences maximum, plain English, "
    "no jargon, no marketing."
)


class ExplainUnavailable(RuntimeError):
    pass


def _key() -> str:
    k = os.environ.get("META_API_KEY")
    if k:
        return k
    raise ExplainUnavailable("no Meta API credential available")


def compose(payload: dict, question: str = "") -> str:
    if os.environ.get("MODEL_OFF", "0") in ("1", "true", "yes"):
        raise ExplainUnavailable("MODEL_OFF")

    body = {
        "model": MODEL,
        "instructions": SYSTEM,
        "input": [{
            "role": "user",
            "content": [{
                "type": "input_text",
                "text": (f"Payload (the only numbers you may use):\n"
                         f"{json.dumps(payload, indent=1)}\n\n"
                         f"Question: {question or 'Explain this to a resident.'}"),
            }],
        }],
        "max_output_tokens": 700,
    }
    req = urllib.request.Request(
        f"{BASE}/responses", data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {_key()}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read())
    except Exception as e:                       # noqa: BLE001
        raise ExplainUnavailable(f"model call failed: {type(e).__name__}") from e

    text = ""
    for item in data.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    text = c.get("text", "").strip()
    if not text:
        raise ExplainUnavailable("model returned no text")

    # INV-10: the sentence may not contain a number the ledger does not have.
    return assert_bound(text, payload)

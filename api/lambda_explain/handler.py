"""Lambda handler: the one place the site touches a model.

The client supplies only identities. For a production card that is the
facility id and the immutable data release the page is pinned to; the facts
and fallback come from that release's `explain.json`, read from the site's own
origin. Older pages send only a facility id and use the registry packaged from
the checked-in demo cards.

What it will not do:
  - accept a facility that is not in the shipped registry
  - return prose containing a numeral absent from the trusted facts
  - forward client-provided prompts or fallback text to the Meta API

Env: META_API_KEY (required, set at deploy time — never committed), MODEL_OFF=1
disables the model and returns the template, WATTDOCKET_ALLOWED_ORIGIN for CORS,
WATTDOCKET_RELEASE_ORIGIN for where releases are read (default the live site).
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from collections import OrderedDict

from wattdocket.prose import UnboundProse, assert_bound
from quota import claim_daily_attempt

MODEL = os.environ.get("WATTDOCKET_EXPLAIN_MODEL", "muse-spark-1.3")
ORIGIN = os.environ.get("WATTDOCKET_ALLOWED_ORIGIN", "*")
MAX_INPUT = 6000
MAX_BODY = 32768
RELEASE_ORIGIN = os.environ.get("WATTDOCKET_RELEASE_ORIGIN", "https://wattdocket.com").rstrip("/")
RELEASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
MAX_RELEASE_BYTES = 16 * 1024 * 1024
RELEASE_CACHE_SIZE = 3

SYSTEM = (
    "You explain a computed environmental reading to a member of the public.\n"
    "You are given a list of FACTS, each a complete sentence fragment with its "
    "units.\n"
    "Rules you must not break:\n"
    "1. Use only numbers that appear in the FACTS. Never compute, never round to "
    "a new value, never estimate. Quote them as written, with their units.\n"
    "2. Never write a bare number without saying what it measures.\n"
    "3. When a FACT says something is not published, say so plainly — that is a "
    "finding, not a gap to smooth over. But NEVER invent an absence: if a subject "
    "is simply not in the FACTS, do not mention it and do not describe it as "
    "unpublished.\n"
    "4. No legal conclusions, no health claims, no marketing.\n"
    "5. Two sentences, under 60 words, plain English for someone who has never "
    "heard of eGRID or a subregion.\n"
    "6. If FACTS give an annual carbon estimate and range, describe them as "
    "a reference-load estimate, not a measurement of the facility.\n"
    "7. Mention missing information only when FACTS explicitly identify it.\n"
    "8. Do not repeat the facility id or any identifier that looks like a code."
)

_REGISTRY: dict[str, dict] | None = None


def _registry() -> dict[str, dict]:
    """Load the release's generated, server-owned card facts and templates."""
    global _REGISTRY
    if _REGISTRY is None:
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "registry.json"), encoding="utf-8") as fh:
            _REGISTRY = json.load(fh)
    return _REGISTRY


_RELEASES: "OrderedDict[str, dict]" = OrderedDict()


class ReleaseUnavailable(RuntimeError):
    pass


def _slug(value: str) -> str:
    """The release builder's record key for a facility id."""
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _fetch_release(release_id: str) -> dict:
    """Read one release's explain facts from the site's own origin.

    The URL is fixed except for a validated release id, so a caller cannot
    point the function anywhere else. Releases are immutable, so a cached copy
    never goes stale.
    """
    url = f"{RELEASE_ORIGIN}/data/releases/{release_id}/explain.json"
    req = urllib.request.Request(url, headers={"accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=6) as r:
            raw = r.read(MAX_RELEASE_BYTES + 1)
    except Exception as exc:                       # noqa: BLE001
        raise ReleaseUnavailable(type(exc).__name__) from exc
    if len(raw) > MAX_RELEASE_BYTES:
        raise ReleaseUnavailable("too large")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ReleaseUnavailable("invalid JSON") from exc
    if not isinstance(data, dict) or data.get("release_id") != release_id or not isinstance(data.get("records"), dict):
        raise ReleaseUnavailable("unexpected release file")
    return data["records"]


def _release_records(release_id: str) -> dict:
    if release_id in _RELEASES:
        _RELEASES.move_to_end(release_id)
        return _RELEASES[release_id]
    records = _fetch_release(release_id)
    _RELEASES[release_id] = records
    while len(_RELEASES) > RELEASE_CACHE_SIZE:
        _RELEASES.popitem(last=False)
    return records


def _release_entry(facility_id: str, release_id: str) -> dict | None:
    """Trusted facts and template for one production card, or None."""
    entry = _release_records(release_id).get(_slug(facility_id))
    if not isinstance(entry, dict) or entry.get("facility_id") != facility_id:
        return None
    facts, template = entry.get("facts"), entry.get("template")
    if (not isinstance(facts, list) or not facts or not all(isinstance(f, str) for f in facts)
            or not isinstance(template, str) or not template.strip()):
        return None
    return {"facts": facts, "template": template.strip()}


def _headers() -> dict:
    """Response headers — deliberately without any access-control-* entry.

    The Function URL's own CORS configuration already emits those, and returning
    them from the handler as well produced *duplicate*
    `access-control-allow-origin` headers. The CORS spec forbids more than one,
    so every browser rejected the response with "Failed to fetch" while curl
    read it happily — a fallback that looked like a model outage and was really
    a header collision.
    """
    return {
        "content-type": "application/json",
        "cache-control": "no-store",
    }


def _reply(status: int, body: dict) -> dict:
    return {"statusCode": status, "headers": _headers(),
            "body": json.dumps(body)}


def _facts_text(payload: dict) -> str:
    """Render the payload as labelled facts.

    The first version sent a dict of snake_case keys, and the model dutifully
    produced "using 10 and 736.6" — numbers stripped of their meaning. Facts
    with units attached are both easier for the model to use and easier for a
    reader to check.
    """
    facts = payload.get("facts")
    if isinstance(facts, list) and facts:
        return "\n".join(f"- {f}" for f in facts)
    return json.dumps(payload, sort_keys=True)[:MAX_INPUT]


def _compose(payload: dict) -> str:
    import urllib.request

    key = os.environ.get("META_API_KEY", "")
    if not key:
        raise RuntimeError("no META_API_KEY")
    body = json.dumps({
        "model": MODEL,
        "instructions": SYSTEM,
        "input": [{
            "role": "user",
            "content": [{
                "type": "input_text",
                "text": ("FACTS (the only numbers you may use):\n"
                         + _facts_text(payload)[:MAX_INPUT]
                         + "\n\nWrite the two sentences."),
            }],
        }],
        # Reasoning tokens can consume the output budget before the model
        # produces text. A bounded allowance and minimal effort give it room
        # to finish; an empty response still returns the trusted fallback.
        "max_output_tokens": 3072,
        "reasoning": {"effort": "minimal"},
    }).encode()
    req = urllib.request.Request(
        f"{os.environ.get('META_API_BASE', 'https://api.meta.ai/v1')}/responses",
        data=body, method="POST",
        headers={"Authorization": f"Bearer {key}", "content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=25) as r:
        data = json.loads(r.read().decode())

    for item in data.get("output", []):
        if item.get("type") in ("message", "output_text"):
            for c in item.get("content", []):
                if c.get("type") in ("output_text", "text"):
                    return (c.get("text") or "").strip()

    # No message: say *why*, because "no text in model response" is what an
    # exhausted reasoning budget looks like, and that is indistinguishable from
    # a refusal or a malformed reply unless the status travels with it.
    status = data.get("status")
    why = (data.get("incomplete_details") or {}).get("reason")
    kinds = [i.get("type") for i in data.get("output", [])]
    raise RuntimeError(f"no text (status={status}, reason={why}, output={kinds})")


def handler(event, context):        # noqa: ANN001, ARG001
    method = (event.get("requestContext", {}).get("http", {}).get("method")
              or event.get("httpMethod") or "POST")
    if method == "OPTIONS":
        # Lambda answers preflight itself from the Function URL CORS config;
        # reaching here means it did not, so keep the body empty and add nothing.
        return {"statusCode": 204, "headers": {}, "body": ""}
    if method != "POST":
        return _reply(405, {"error": "POST only"})

    raw = event.get("body") or "{}"
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_BODY or event.get("isBase64Encoded"):
        return _reply(400, {"error": "invalid request body"})
    try:
        body = json.loads(raw)
    except (ValueError, TypeError):
        return _reply(400, {"error": "invalid JSON"})
    if not isinstance(body, dict):
        return _reply(400, {"error": "invalid request body"})
    # Older site releases send {payload: {facility_id, facts}, template}; use
    # only the identifier and ignore every client-supplied fact and template.
    legacy = body.get("payload")
    facility_id = body.get("facility_id")
    if facility_id is None and isinstance(legacy, dict):
        facility_id = legacy.get("facility_id") or legacy.get("id")
    if not isinstance(facility_id, str) or not facility_id or len(facility_id) > 128:
        return _reply(400, {"error": "invalid facility identity"})
    release_id = body.get("release_id")
    if release_id is not None:
        if not isinstance(release_id, str) or not RELEASE_ID.match(release_id):
            return _reply(400, {"error": "invalid release identity"})
        try:
            trusted = _release_entry(facility_id, release_id)
        except ReleaseUnavailable:
            return _reply(503, {"source": "template", "degraded": True,
                                "reason": "RELEASE_UNAVAILABLE"})
    elif len(facility_id) > 64:
        return _reply(400, {"error": "invalid facility identity"})
    else:
        trusted = _registry().get(facility_id)
    if trusted is None:
        return _reply(403, {"source": "template", "degraded": True,
                            "reason": "UNKNOWN_FACILITY"})
    template = trusted["template"]
    payload = {"facts": trusted["facts"]}

    if os.environ.get("MODEL_OFF", "0") in ("1", "true", "yes"):
        return _reply(200, {"text": template, "source": "template",
                            "model": None, "degraded": True, "reason": "MODEL_OFF"})

    try:
        admission = claim_daily_attempt()
    except Exception:  # A broken counter must never allow an unmetered model call.
        admission = "QUOTA_UNAVAILABLE"
    if admission != "ALLOWED":
        reason = "DAILY_QUOTA_EXHAUSTED" if admission == "DAILY_QUOTA_EXHAUSTED" else "QUOTA_UNAVAILABLE"
        return _reply(200, {"text": template, "source": "template", "model": None,
                            "degraded": True, "reason": reason})

    try:
        prose = _compose(payload)
        if not prose:
            raise ValueError("empty model response")
        # Check only the exact server-owned display facts, not facility IDs,
        # raw card internals, or any caller-supplied numbers.
        assert_bound(prose, payload)
        return _reply(200, {"text": prose, "source": "model",
                            "model": MODEL, "degraded": False})
    except UnboundProse as exc:
        return _reply(200, {"text": template, "source": "template", "model": None,
                            "degraded": True, "reason": "UNBOUND_NUMERAL",
                            "detail": str(exc)[:120]})
    except Exception as exc:                       # noqa: BLE001
        # Never echo upstream bodies, credentials or attacker-controlled text.
        reason = "MODEL_TIMEOUT" if (
            isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError)
        ) else "MODEL_UNAVAILABLE"
        return _reply(200, {"text": template, "source": "template", "model": None,
                            "degraded": True, "reason": reason})

"""Retired composer retained only to give existing imports a clear error.

The former composer accepted arbitrary client payloads and optionally read a
local credential store. Neither behavior belongs in the public explanation
path. Use scripts/serve_local_explain.py, which uses the same release-pinned
facts and response validation as api/lambda_explain/handler.py.

This module does not read credentials, files, environment variables, or the
network. Calling compose always raises ExplainUnavailable.
"""


class ExplainUnavailable(RuntimeError):
    """The legacy explanation contract is no longer supported."""


def compose(payload: dict, question: str = "") -> str:
    """Reject legacy callers without inspecting or forwarding their payload."""
    raise ExplainUnavailable(
        "LEGACY_EXPLAIN_RETIRED: use scripts/serve_local_explain.py with a pinned data release"
    )

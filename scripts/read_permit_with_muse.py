"""Run the permit reader end to end with a real model, and report the gate.

    python scripts/read_permit_with_muse.py data/permits/<file>.pdf

Prints how many obligations the model proposed, how many survived verbatim
verification, and what it rejected. The rejection list is the interesting
output: it is the difference between a citation and an assertion.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "core"))

from wattdocket.permits import PermitDocument, read_with_model  # noqa: E402


def main() -> None:
    pdf = sys.argv[1] if len(sys.argv) > 1 else "data/permits/tceq_amazon_ale60_pbr164110.pdf"
    model = sys.argv[2] if len(sys.argv) > 2 else "muse-spark-1.3"

    def chat_fn(prompt: str) -> str:
        # Meta Model API, Responses endpoint. The output budget must exceed the
        # reasoning tokens or the text comes back empty.
        key = os.environ.get("META_API_KEY")
        if not key:
            raise SystemExit("set META_API_KEY (see .env.example)")
        base = os.environ.get("META_API_BASE", "https://api.meta.ai/v1")
        body = {"model": model, "input": prompt, "max_output_tokens": 4000,
                "reasoning": {"effort": "low"}}
        req = urllib.request.Request(
            f"{base}/responses", data=json.dumps(body).encode(), method="POST",
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
        return "".join(c.get("text", "")
                       for item in data.get("output", []) if item.get("type") == "message"
                       for c in item.get("content", []))

    doc = PermitDocument.load(ROOT / pdf)
    started = time.time()
    result = read_with_model(doc, chat_fn, max_pages=3)
    result["model"] = model
    result["seconds"] = round(time.time() - started, 1)

    print(f"model:     {model}")
    print(f"document:  {result['document']} ({doc.page_count} pages)")
    print(f"sha256:    {result['document_sha256'][:16]}…")
    print(f"proposed:  {result['proposed']}")
    print(f"verified:  {len(result['verified'])}")
    for c in result["verified"]:
        cit = c["citation"]
        print(f"  ✓ p{cit['page']} L{cit['line']}  {c['metric']:24s} "
              f"{c['value']} {c['unit']}")
        print(f"      “{cit['quote'][:96]}”")
    print(f"rejected:  {len(result['rejected'])}")
    for r in result["rejected"]:
        print(f"  ✗ {r['metric']}: {r['reason'][:70]}")
    print(f"seconds:   {result['seconds']}")

    out = ROOT / "artifacts" / "permit_read.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2))
    print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

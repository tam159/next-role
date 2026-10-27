"""RunLedger adapter for one career-agent resume trace.

Speaks newline-delimited JSON on stdout. Logs go to stderr.

The trace is fixed: ``web_search``, then ``write_file`` of a rendercv YAML
resume. Tool results come from the runner (a cassette in replay, the handlers
in ``tools.py`` while recording). The final output is the resume document
returned by ``write_file``.
"""

from __future__ import annotations

import json
import sys
from typing import Any

# Compact rendercv input: cv, design, locale. No settings block — the career
# agent's resume skill forbids one; render_resume_pdf injects settings later.
RESUME_YAML = """\
cv:
  name: Ada Lovelace
  headline: Senior AI Engineer
  email: ada@example.com
  location: London, UK
  sections:
    summary:
      - Research the company before writing a tailored resume.
    experience:
      - company: Analytical Engines
        position: Senior AI Engineer
        start_date: "2020-01"
        end_date: present
        highlights:
          - Shipped research-backed machine learning systems.
design:
  theme: classic
locale:
  language: english
"""


def send(payload: dict[str, Any]) -> None:
    """Write one protocol message to stdout."""
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def main() -> int:
    """Handle one task: search, then write the resume YAML."""
    task: dict[str, Any] = {}
    pending = ""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        message = json.loads(line)
        kind = message.get("type")
        if kind == "task_start":
            raw_input = message.get("input")
            task = raw_input if isinstance(raw_input, dict) else {}
            company = str(task.get("company", ""))
            role = str(task.get("role", ""))
            pending = "search"
            send(
                {
                    "type": "tool_call",
                    "name": "web_search",
                    "call_id": "c1",
                    "args": {
                        "query": f"{company} {role} interview".strip(),
                        "search_depth": "basic",
                        "topic": "general",
                        "max_results": 2,
                    },
                }
            )
            continue
        if kind != "tool_result":
            continue
        if not message.get("ok", False):
            detail = message.get("error") or "tool failed"
            send({"type": "task_error", "message": str(detail)})
            return 1
        if pending == "search":
            pending = "write"
            send(
                {
                    "type": "tool_call",
                    "name": "write_file",
                    "call_id": "c2",
                    "args": {
                        "file_path": task.get("yaml_path"),
                        "content": RESUME_YAML,
                    },
                }
            )
            continue
        if pending == "write":
            result = message.get("result")
            if not isinstance(result, dict) or not isinstance(
                result.get("document"), dict
            ):
                send(
                    {
                        "type": "task_error",
                        "message": "write_file result did not include a resume document",
                    }
                )
                return 1
            send({"type": "final_output", "output": result["document"]})
            return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

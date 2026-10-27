"""Write rendercv's JSON Schema next to this suite.

Uses ``rendercv.schema.json_schema_generator.generate_json_schema``, which is
rendercv's own ``RenderCVModel`` schema. RenderCV clears ``required`` so the
same file can describe a design-only or locale-only document. A tailored
resume must include ``cv``, so this script sets ``required`` to ``["cv"]``
after generation.
"""

from __future__ import annotations

import json
from pathlib import Path

from rendercv.schema.json_schema_generator import generate_json_schema

_SCHEMA_PATH = Path(__file__).with_name("schema.json")


def main() -> None:
    """Generate ``schema.json`` beside this script."""
    schema = generate_json_schema()
    schema["required"] = ["cv"]
    _SCHEMA_PATH.write_text(
        json.dumps(schema, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

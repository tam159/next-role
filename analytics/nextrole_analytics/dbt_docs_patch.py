"""Fill the empty ``database`` field in dbt docs artifacts so the Database tab renders.

dbt-clickhouse leaves ``database`` blank on every relation (ClickHouse has a
single level — its "database" is dbt's ``schema``), and the dbt docs site's
Database tab groups relations by database → schema, so with an empty database
the tab shows nothing at all. This rewrites ``manifest.json`` + ``catalog.json``
in a dbt target directory, labelling every node and source with a display
database name — the tree then reads ``clickhouse → nextrole_marts → fct_run``.

Runs inside the ``dbt-docs`` compose service between ``dbt docs generate`` and
``dbt docs serve``:

    python -m nextrole_analytics.dbt_docs_patch /tmp/dbt-target [database-label]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

DEFAULT_DATABASE_LABEL = "clickhouse"


def _fill(entry: dict[str, Any], key: str, database: str) -> bool:
    """Set ``entry[key]`` when it exists but is blank; return whether it changed."""
    if key in entry and not entry[key]:
        entry[key] = database
        return True
    return False


def patch_artifacts(target_dir: Path, database: str = DEFAULT_DATABASE_LABEL) -> int:
    """Patch manifest.json + catalog.json under ``target_dir``; return patched count."""
    patched = 0
    for name in ("manifest.json", "catalog.json"):
        path = target_dir / name
        data = json.loads(path.read_text())
        for section in ("nodes", "sources"):
            for entry in data.get(section, {}).values():
                if name == "manifest.json":
                    patched += _fill(entry, "database", database)
                else:
                    patched += _fill(entry.get("metadata", {}), "database", database)
        path.write_text(json.dumps(data))
    return patched


def main(argv: list[str]) -> None:
    """CLI: ``<target_dir> [database-label]``."""
    target_dir = Path(argv[1])
    database = argv[2] if len(argv) > 2 else DEFAULT_DATABASE_LABEL  # noqa: PLR2004
    patched = patch_artifacts(target_dir, database)
    print(f"dbt docs artifacts: labelled {patched} relations with database={database!r}")  # noqa: T201


if __name__ == "__main__":
    main(sys.argv)

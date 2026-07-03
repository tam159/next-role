"""Wrapper for migration execution (for testing the Go server)."""

import asyncio
from pathlib import Path

from runtime_postgres import database
from runtime_postgres.database import (
    create_pool,
    migrate,
    migrate_vector_index,
)


async def migrate_for_tests():
    database._pg_pool = create_pool()
    database.config.MIGRATIONS_PATH = Path(__file__).parent / ".." / "migrations"

    await database._pg_pool.open(wait=True)

    await migrate()
    await migrate_vector_index()


if __name__ == "__main__":
    asyncio.run(migrate_for_tests())

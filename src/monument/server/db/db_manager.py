"""
Monument database manager.
Handles namespace validation, DB creation, and schema initialization.
No ORM, no migrations - fail-fast on schema version mismatch.
"""

import re
import sqlite3
import time
from pathlib import Path
from typing import Optional

# Schema version must match PRAGMA user_version in schema.sql
EXPECTED_SCHEMA_VERSION = 1

# Namespace validation regex from design doc
NAMESPACE_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


class NamespaceError(Exception):
    """Invalid namespace identifier."""
    pass


class SchemaVersionError(Exception):
    """Schema version mismatch."""
    pass


def validate_namespace(namespace: str) -> None:
    """
    Validate namespace identifier.
    Raises NamespaceError if invalid.
    """
    if not NAMESPACE_PATTERN.match(namespace):
        raise NamespaceError(
            f"Invalid namespace '{namespace}'. "
            f"Must match pattern: ^[a-zA-Z0-9][a-zA-Z0-9_-]{{0,63}}$"
        )


def get_db_path(namespace: str) -> Path:
    """
    Get the DB file path for a namespace.
    Does not create the file, just returns the path.
    """
    validate_namespace(namespace)
    # Always relative to project root
    project_root = Path(__file__).parent.parent.parent.parent.parent
    db_dir = project_root / "data" / "sims"
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir / f"{namespace}.db"


def init_db(db_path: Path) -> None:
    """
    Initialize a new database with pragmas, schema, and indexes.
    Raises SchemaVersionError if DB exists with wrong schema version.
    """
    # Load SQL scripts
    sql_dir = Path(__file__).parent
    pragmas_sql = (sql_dir / "pragmas.sql").read_text()
    schema_sql = (sql_dir / "schema.sql").read_text()
    indexes_sql = (sql_dir / "indexes.sql").read_text()

    # Create/open DB
    conn = sqlite3.connect(db_path)
    try:
        # Apply pragmas
        conn.executescript(pragmas_sql)

        # Apply schema
        conn.executescript(schema_sql)

        # Apply indexes
        conn.executescript(indexes_sql)

        # Verify schema version
        cursor = conn.execute("PRAGMA user_version")
        version = cursor.fetchone()[0]

        if version != EXPECTED_SCHEMA_VERSION:
            raise SchemaVersionError(
                f"Schema version mismatch for {db_path}. "
                f"Expected {EXPECTED_SCHEMA_VERSION}, got {version}. "
                f"Cannot run this namespace without manual intervention."
            )

        conn.commit()
    finally:
        conn.close()


def get_connection(namespace: str) -> sqlite3.Connection:
    """
    Get a connection to the namespace DB.
    Lazy-creates and initializes DB if it doesn't exist.
    """
    db_path = get_db_path(namespace)

    # If DB doesn't exist, initialize it
    if not db_path.exists():
        init_db(db_path)
    else:
        # Verify schema version of existing DB
        conn = sqlite3.connect(db_path)
        try:
            cursor = conn.execute("PRAGMA user_version")
            version = cursor.fetchone()[0]
            if version != EXPECTED_SCHEMA_VERSION:
                raise SchemaVersionError(
                    f"Schema version mismatch for {namespace}. "
                    f"Expected {EXPECTED_SCHEMA_VERSION}, got {version}."
                )
        finally:
            conn.close()

    # Return a fresh connection
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # Enable column access by name
    return conn


def init_world(conn: sqlite3.Connection, width: int, height: int, goal: str = "") -> None:
    """
    Initialize a new world in the database.
    Sets up metadata, creates blank tiles, and prepares for agent registration.
    """
    cursor = conn.cursor()

    # Initialize metadata
    meta_values = [
        ("supertick_id", "0"),
        ("phase", "SETUP"),  # Start in SETUP phase until agents are registered
        ("goal", goal),
        ("width", str(width)),
        ("height", str(height)),
        ("last_adjudication_json", "null"),
        ("schema_version", str(EXPECTED_SCHEMA_VERSION)),
    ]

    cursor.executemany(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        meta_values
    )

    # Create all tiles as blank/white
    tiles = []
    for x in range(width):
        for y in range(height):
            tiles.append((x, y, "#FFFFFF"))

    cursor.executemany(
        "INSERT OR REPLACE INTO tiles (x, y, color) VALUES (?, ?, ?)",
        tiles
    )

    conn.commit()


def register_actor(conn: sqlite3.Connection, actor_id: str, x: int, y: int, facing: str = "N") -> None:
    """
    Register a new actor in the world.
    """
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO actors (id, x, y, facing, points, eliminated_at) VALUES (?, ?, ?, ?, 100, NULL)",
        (actor_id, x, y, facing)
    )
    conn.commit()


def unregister_actor(conn: sqlite3.Connection, actor_id: str) -> None:
    """
    Unregister (delete) an actor from the world.
    """
    cursor = conn.cursor()
    cursor.execute("DELETE FROM actors WHERE id = ?", (actor_id,))
    conn.commit()


def get_registered_actor_count(conn: sqlite3.Connection) -> int:
    """
    Get the count of registered (non-eliminated) actors.
    """
    cursor = conn.execute("SELECT COUNT(*) FROM actors WHERE eliminated_at IS NULL")
    return cursor.fetchone()[0]

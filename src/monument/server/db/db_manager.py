"""
Monument database manager.
Handles namespace validation, DB creation, and schema initialization.
No ORM, no migrations - fail-fast on schema version mismatch.
"""

import json
import re
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Optional, List

# Schema version must match PRAGMA user_version in schema.sql
EXPECTED_SCHEMA_VERSION = 7

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


def init_world(conn: sqlite3.Connection, width: int, height: int, goal: str = "", epoch: int = 10) -> None:
    """
    Initialize a new world in the database.
    Sets up metadata, creates blank tiles, and prepares for agent registration.

    Args:
        epoch: Number of superticks to auto-advance before pausing (default: 10)
    """
    cursor = conn.cursor()

    # Initialize metadata
    meta_values = [
        ("supertick_id", "0"),
        ("phase", "SETUP"),  # Start in SETUP phase until agents are registered
        ("goal", goal),
        ("width", str(width)),
        ("height", str(height)),
        ("epoch", str(epoch)),  # Number of ticks to run before pausing
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


def register_actor(
    conn: sqlite3.Connection,
    actor_id: str,
    x: int,
    y: int,
    facing: str = "N",
    scopes: Optional[List[str]] = None,
    secret: Optional[str] = None,
    custom_instructions: str = "",
    llm_model: str = ""
) -> str:
    """
    Register a new actor in the world.

    Args:
        conn: Database connection
        actor_id: Unique actor identifier
        x, y: Starting position
        facing: Initial facing direction (N, S, E, W)
        scopes: List of allowed actions (default: all actions)
        secret: Authentication secret (auto-generated if not provided)
        custom_instructions: Agent's identity, role, and objectives for this world

    Returns:
        The actor's secret (generated or provided)
    """
    # Default scopes: all actions
    if scopes is None:
        scopes = ["MOVE", "PAINT", "SPEAK", "WAIT", "SKIP"]

    # Generate secret if not provided (32-character hex string)
    if secret is None:
        secret = secrets.token_hex(16)

    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT OR REPLACE INTO actors (id, secret, x, y, facing, scopes, custom_instructions, llm_model, eliminated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        (actor_id, secret, x, y, facing, json.dumps(scopes), custom_instructions, llm_model)
    )

    # Get current supertick for initial position record
    cursor.execute("SELECT value FROM meta WHERE key = 'supertick_id'")
    row = cursor.fetchone()
    current_tick = int(row[0]) if row else 0

    # Record initial spawn position in actor_history
    cursor.execute(
        """
        INSERT INTO actor_history (actor_id, supertick_id, x, y, facing, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (actor_id, current_tick, x, y, facing, int(time.time()))
    )
    conn.commit()

    return secret


def unregister_actor(conn: sqlite3.Connection, actor_id: str) -> None:
    """
    Unregister (delete) an actor from the world.
    """
    cursor = conn.cursor()
    cursor.execute("DELETE FROM actors WHERE id = ?", (actor_id,))
    conn.commit()


def update_actor_scopes(conn: sqlite3.Connection, actor_id: str, scopes: List[str]) -> None:
    """
    Update an actor's allowed action scopes.
    """
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE actors SET scopes = ? WHERE id = ?",
        (json.dumps(scopes), actor_id)
    )
    conn.commit()


def update_actor_instructions(conn: sqlite3.Connection, actor_id: str, custom_instructions: str) -> None:
    """
    Update an actor's custom instructions (identity, role, objectives).
    """
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE actors SET custom_instructions = ? WHERE id = ?",
        (custom_instructions, actor_id)
    )
    conn.commit()


def update_actor_llm_model(conn: sqlite3.Connection, actor_id: str, llm_model: str) -> None:
    """
    Update an actor's preferred LLM model.
    """
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE actors SET llm_model = ? WHERE id = ?",
        (llm_model, actor_id)
    )
    conn.commit()


def regenerate_actor_secret(conn: sqlite3.Connection, actor_id: str) -> str:
    """
    Generate a new secret for an actor.

    Returns:
        The new secret
    """
    new_secret = secrets.token_hex(16)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE actors SET secret = ? WHERE id = ?",
        (new_secret, actor_id)
    )
    conn.commit()
    return new_secret


def get_registered_actor_count(conn: sqlite3.Connection) -> int:
    """
    Get the count of registered (non-eliminated) actors.
    """
    cursor = conn.execute("SELECT COUNT(*) FROM actors WHERE eliminated_at IS NULL")
    return cursor.fetchone()[0]


def add_chat_message(conn: sqlite3.Connection, supertick_id: int, from_id: str, message: str) -> None:
    """
    Add a chat message to the chat log.
    Used for testing or manual message insertion.
    In production, this would be called by the game engine during MERGE phase.
    """
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO chat (supertick_id, from_id, message, created_at) VALUES (?, ?, ?, ?)",
        (supertick_id, from_id, message, int(time.time()))
    )
    conn.commit()


def get_world_state_at_tick(conn: sqlite3.Connection, supertick_id: int) -> dict:
    """
    Reconstruct world tile state at a specific supertick by replaying tile_history.

    Args:
        conn: Database connection
        supertick_id: The supertick to reconstruct

    Returns:
        dict mapping (x, y) -> color
    """
    # Get world dimensions
    cursor = conn.execute("SELECT value FROM meta WHERE key='width'")
    width = int(cursor.fetchone()[0])
    cursor = conn.execute("SELECT value FROM meta WHERE key='height'")
    height = int(cursor.fetchone()[0])

    # Initialize all tiles as white (#FFFFFF)
    tiles = {}
    for x in range(width):
        for y in range(height):
            tiles[(x, y)] = "#FFFFFF"

    # Apply all tile changes up to and including the requested supertick
    cursor = conn.execute(
        """
        SELECT x, y, new_color
        FROM tile_history
        WHERE supertick_id <= ?
        ORDER BY supertick_id ASC, created_at ASC
        """,
        (supertick_id,)
    )

    for x, y, new_color in cursor.fetchall():
        tiles[(x, y)] = new_color

    return tiles

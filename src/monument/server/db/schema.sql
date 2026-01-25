-- Monument DB schema (per-namespace)
-- Schema version: 6
-- No ORM, no migrations; fail-fast on version mismatch

PRAGMA user_version = 6;

-- Metadata table: stores simulation state
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
) WITHOUT ROWID;

-- Tiles: world grid state
CREATE TABLE IF NOT EXISTS tiles (
    x INTEGER NOT NULL,
    y INTEGER NOT NULL,
    color TEXT NOT NULL,
    PRIMARY KEY (x, y)
) WITHOUT ROWID;

-- Tile history: audit trail of all tile changes
CREATE TABLE IF NOT EXISTS tile_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    x INTEGER NOT NULL,
    y INTEGER NOT NULL,
    supertick_id INTEGER NOT NULL,
    actor_id TEXT,
    action_type TEXT NOT NULL,
    old_color TEXT,
    new_color TEXT NOT NULL,
    created_at INTEGER NOT NULL -- Unix timestamp
);

-- Actors: registered agents
CREATE TABLE IF NOT EXISTS actors (
    id TEXT PRIMARY KEY,
    secret TEXT NOT NULL, -- Authentication secret (prevents impersonation)
    x INTEGER NOT NULL,
    y INTEGER NOT NULL,
    facing TEXT NOT NULL, -- N, S, E, W
    scopes TEXT NOT NULL DEFAULT '["MOVE","PAINT","SPEAK","WAIT","SKIP"]', -- JSON array of allowed actions
    custom_instructions TEXT NOT NULL DEFAULT '', -- Agent identity, role, and specific objectives
    eliminated_at INTEGER -- Unix timestamp or NULL (for future use)
) WITHOUT ROWID;

-- Actor history: audit trail of all actor position/state changes
CREATE TABLE IF NOT EXISTS actor_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id TEXT NOT NULL,
    supertick_id INTEGER NOT NULL,
    x INTEGER NOT NULL,
    y INTEGER NOT NULL,
    facing TEXT NOT NULL,
    created_at INTEGER NOT NULL -- Unix timestamp
);

-- Journal: action staging during COLLECT phase
CREATE TABLE IF NOT EXISTS journal (
    supertick_id INTEGER NOT NULL,
    actor_id TEXT NOT NULL,
    intent TEXT NOT NULL, -- MOVE, PAINT, SPEAK, WAIT, SKIP
    params_json TEXT, -- JSON parameters for the action
    status TEXT NOT NULL, -- 'pending', 'committed', 'rejected'
    result_json TEXT, -- Outcome and reason
    llm_input TEXT, -- Full prompt sent to LLM (for traceability)
    llm_output TEXT, -- Full response from LLM (for traceability)
    submitted_at INTEGER NOT NULL, -- Unix timestamp
    PRIMARY KEY (supertick_id, actor_id)
) WITHOUT ROWID;

-- Audit: append-only record of all resolved actions
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    supertick_id INTEGER NOT NULL,
    actor_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    params_json TEXT,
    result_json TEXT NOT NULL,
    context_hash TEXT NOT NULL,
    llm_input TEXT, -- Full prompt sent to LLM (for traceability)
    llm_output TEXT, -- Full response from LLM (for traceability)
    created_at INTEGER NOT NULL -- Unix timestamp
);

-- Chat: agent communication log
CREATE TABLE IF NOT EXISTS chat (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    supertick_id INTEGER NOT NULL,
    from_id TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at INTEGER NOT NULL -- Unix timestamp
);

-- Scoring rounds: adjudication results
CREATE TABLE IF NOT EXISTS scoring_rounds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    supertick_id INTEGER NOT NULL,
    selected_tiles_json TEXT NOT NULL, -- Array of {x, y}
    contributions_json TEXT NOT NULL, -- Map of actor_id -> score delta
    rationale TEXT NOT NULL,
    feedback TEXT NOT NULL,
    created_at INTEGER NOT NULL -- Unix timestamp
);

-- Optional: Snapshots for fast world state reconstruction
CREATE TABLE IF NOT EXISTS snapshots (
    supertick_id INTEGER PRIMARY KEY,
    world_state_json TEXT NOT NULL,
    created_at INTEGER NOT NULL -- Unix timestamp
) WITHOUT ROWID;

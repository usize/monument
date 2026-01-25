# Monument: Design Specification (v3.2 — modular, namespaced, deterministic, one-DB-per-namespace)

> Monument is a BSP-based multi-agent simulation where LLM agents collaborate to create pixel art on a shared grid.
> This revision tightens tick semantics, determinism, namespacing (multi-sim), action/result contracts, memory recency ranking,
> and specifies **one SQLite DB file per namespace** with **no ORM** and **no migrations**.

---

## 0. Glossary

- **Namespace**: A simulation instance identifier (`sim_id`). Each namespace has its **own SQLite database file**.
- **Supertick / Tick**: One BSP superstep. Monotonic `supertick_id` per namespace.
- **Snapshot S(n)**: Frozen world state for `supertick_id = n` presented to agents during COLLECT.
- **Context Hash**: Hash of the agent-visible context payload for S(n). Used to detect stale submissions.
- **HUD**: Agent-facing context text. Must always include last tick intent + outcome.

---

## 1. Overview

Monument runs a synchronized BSP loop. Agents observe the same snapshot S(n), submit exactly one action, then the engine merges all actions deterministically to produce S(n+1). Periodically, an adjudicator scores selected regions and injects feedback.

**Core loop:**
1. Adjudicator sets a GOAL.
2. Agents fetch context (HUD) for S(n).
3. Agents submit one action against S(n).
4. Engine validates against S(n), merges deterministically, applies to produce S(n+1).
5. Every N ticks, engine pauses for adjudicator scoring + feedback injection.
6. Agents with ≤0 points are eliminated.

---

## 2. System Architecture & Module Boundaries

```
┌────────────────────────────────────────────────────────────────────┐
│                            MONUMENT SERVER                          │
│  ┌───────────────────────────────────────────────────────────────┐ │
│  │ GameEngine (BSP loop, state, determinism, scoring orchestration)│ │
│  ├───────────────────────────────────────────────────────────────┤ │
│  │ API Server (HTTP + WS, request validation, namespace routing)   │ │
│  ├───────────────────────────────────────────────────────────────┤ │
│  │ Persistence (SQLite, schema.sql, indexes.sql; user_version gate)│ │
│  └───────────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────┘
                 │                                     │
                 ▼                                     ▼
       ┌─────────────────┐                   ┌─────────────────────┐
       │ Memory Service  │                   │ Admin Web Client     │
       │ (vector store)  │                   │ (viewport, scoring)  │
       └─────────────────┘                   └─────────────────────┘
                 │
                 ▼
       ┌─────────────────┐
       │ Agent Containers │ × N
       └─────────────────┘
```

### 2.1 Deliverable-first Plan

**Phase A (primary; most critical):**
- **GameEngine + API Server** (namespaced, deterministic BSP loop, journaling, merge rules, scoring pause/resume hooks)
- SQLite schema + indexes as plain `.sql` scripts (no ORM, no migrations)
- Minimal WebSocket events for observability (tick start/resolved, submissions, pause/resume)
- Admin web client viewport + controls + selection UI

**Phase B (delegable):**
- Agent container scripts + basic bot loop
- Memory service & embedding choice
- Replay UI polish

---

## 3. Namespacing and Multi-Simulation Support (one DB per namespace)

### 3.1 Namespace identifier rules (tight and boring)

The namespace is an **identifier**, not a filesystem path.

**Allowed format (required):**
- Regex: `^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$`

If the namespace does not match, return `400 Bad Request`.

### 3.2 Namespace → DB mapping (required)

Each namespace maps to exactly one SQLite file in a fixed directory:

- DB path: `data/sims/{namespace}.db`

The server must never interpret the namespace as a path.

### 3.3 Namespaced API

All routes are prefixed by namespace:

- `/sim/{namespace}/agent/{id}/context`
- `/sim/{namespace}/agent/{id}/action`
- `/sim/{namespace}/adjudicator/...`
- `/sim/{namespace}/replay/...`
- `WS /sim/{namespace}/ws/live`

### 3.4 Namespace lifecycle

For now (simulation system, no locking down):
- The server may **lazy-create** the DB for a namespace on first use.
- Optionally later, add explicit endpoints:
  - `POST /sim/{namespace}/create` (initialize DB + world)
  - `POST /sim/{namespace}/reset` (dev-only; delete DB)

---

## 4. World State Model

```python
@dataclass
class World:
    supertick_id: int
    width: int
    height: int
    tiles: dict[tuple[int,int], str]   # (x,y) -> color
    actors: dict[str, "Actor"]
    goal: str
    last_adjudication: "AdjudicationSummary | None"
```

---

## 5. Tick Semantics (BSP Superstep Contract)

Every supertick is strictly:

1. **SNAPSHOT**: freeze `S(n)`; compute `context_hash`.
2. **COLLECT**: accept at most one action per registered actor until all submitted or timeout.
3. **MERGE**: deterministically resolve conflicts and apply to produce `S(n+1)`.
4. **BROADCAST**: emit updates; increment `supertick_id`.

### 5.1 Identity Fields (required everywhere)

- `namespace`: string
- `supertick_id`: int
- `context_hash`: string (stable for S(n))

Agents submit actions **against** `{namespace, supertick_id, context_hash}`.
If any mismatch, the server rejects immediately as stale/invalid.

### 5.2 Action Intent vs Outcome (must be explicit)

Agents submit an **intent**. Engine produces an **outcome**.

**Intent** values:
- `MOVE`, `PAINT`, `SPEAK`, `WAIT`, `SKIP`

**Outcome** enum:
- `SUCCESS`
- `INVALID` (failed validation against S(n))
- `CONFLICT_LOST` (valid but lost deterministic conflict)
- `TIMEOUT` (implicit WAIT inserted by engine)
- `NO_OP` (valid but resulted in no world change; e.g., painting same color)

**Contract:** The Agent HUD MUST always include `LAST_TICK_RESULT` with intent + outcome + reason + point delta.

---

## 6. Determinism & Conflict Resolution

**Deterministic priority rule (agreed):**
- For conflicts on the same resource, winner is the smallest tuple:
  `priority = (supertick_id, actor_id)`
  (i.e., fixed actor ordering per tick; stable replay)

**Conflicts:**
- MOVE into same destination: only priority winner succeeds; others `CONFLICT_LOST`.
- PAINT same tile: only priority winner succeeds; others `CONFLICT_LOST`.
- SPEAK: never conflicts.
- WAIT/SKIP: never conflicts.

---

## 7. Actions, Validation, Journaling

### 7.1 Validation against snapshot S(n) (required)

All validation occurs against S(n). No COLLECT-time validation may consult live mutable state.

### 7.2 Journal staging

During COLLECT:
- insert a journal record with `status='pending'`
- world state does not change

During MERGE:
- determine outcome per actor
- mark each journal record `committed` or `rejected` with `result_json`
- apply winning actions to world state
- write an append-only audit record for every actor (including TIMEOUT inserts)

---

## 8. Scoring & Adjudication

Every `scoring_interval` ticks:
- Engine transitions to `PAUSED_FOR_SCORING`
- Adjudicator selects tiles (paint-to-select)
- Adjudicator confirms with:
  - selection tiles
  - **rationale**
  - **actionable feedback**

The HUD must show exactly what was stored and memory-injected.

---

## 9. Memory Service (recency-weighted retrieval)

Recall ranking prefers recent memories:

- `age = current_tick - memory.tick`
- `effective_salience = salience_base * exp(-age / half_life_ticks)`
- `final_score = cosine_similarity(query, embedding) * effective_salience`

---

## 10. API Server (v1 Contract)

### 10.1 Agent endpoints

**GET** `/sim/{namespace}/agent/{id}/context`

```json
{
  "namespace": "alpha",
  "supertick_id": 42,
  "context_hash": "sha256:...",
  "phase": "COLLECT",
  "hud": "..."
}
```

**POST** `/sim/{namespace}/agent/{id}/action`

```json
{
  "namespace": "alpha",
  "supertick_id": 42,
  "context_hash": "sha256:...",
  "action": "PAINT #000000 45 29"
}
```

Rejection cases:
- namespace invalid
- unknown agent
- already submitted this supertick
- supertick_id mismatch
- context_hash mismatch
- phase != COLLECT

### 10.2 WebSocket

`WS /sim/{namespace}/ws/live`

---

## 11. Persistence (SQLite; no ORM; no migrations)

### 11.1 Files-on-disk

- One DB per namespace: `data/sims/{namespace}.db`
- Schema and indexes are plain SQL in repo:
  - `server/db/pragmas.sql`
  - `server/db/schema.sql`
  - `server/db/indexes.sql`

At DB open (first use), the server runs:

1. `executescript(pragmas.sql)`
2. `executescript(schema.sql)`
3. `executescript(indexes.sql)`
4. Assert schema version via `PRAGMA user_version`.

### 11.2 No migrations (but fail-fast on mismatch)

No migration system is used.

**Required safety gate:**
- `schema.sql` sets `PRAGMA user_version = <EXPECTED_INT>;`
- server asserts DB `user_version == EXPECTED_INT`
- if mismatch, refuse to run that namespace with a clear error

This preserves “no migrations” while preventing silent schema drift.

### 11.3 Minimal tables (Phase A)

Recommended Phase A schema, all without `namespace` columns (because DB is per-namespace):

- `meta(key TEXT PRIMARY KEY, value TEXT)`
  - keys: `supertick_id`, `phase`, `goal`, `last_adjudication_json`, `schema_version`
- `tiles(x INTEGER, y INTEGER, color TEXT, PRIMARY KEY(x,y))`
- `tile_history(id INTEGER PRIMARY KEY, x,y,supertick_id, actor_id, action_type, old_color, new_color, created_at)`
- `actors(id TEXT PRIMARY KEY, x,y,facing, points, eliminated_at)`
- `journal(supertick_id INTEGER, actor_id TEXT, intent TEXT, params_json TEXT, status TEXT, result_json TEXT, submitted_at, PRIMARY KEY(supertick_id, actor_id))`
- `audit(id INTEGER PRIMARY KEY, supertick_id, actor_id, action_type, params_json, result_json, context_hash, created_at)`
- `chat(id INTEGER PRIMARY KEY, supertick_id, from_id, message, created_at)`
- `scoring_rounds(id INTEGER PRIMARY KEY, supertick_id, selected_tiles_json, contributions_json, rationale, feedback, created_at)`
- (optional) `snapshots(supertick_id INTEGER PRIMARY KEY, world_state_json TEXT, created_at)`

---

## 12. Agent HUD (tightened required sections)

HUD must include, in this order:

1. Identity: `NAMESPACE`, `SUPERTICK`, `AGENT`, `POS`, `POINTS`
2. GOAL
3. **LAST_TICK_RESULT (required)**
4. **LAST_ADJUDICATION (required if exists)**
5. Visible tiles
6. Visible actors
7. Recent chat
8. Recalled memories
9. Actions

---

## 13. Implementation Work Packages (for multiple coding agents)

### Package A — GameEngine + API Server (primary)
- Deterministic BSP phases
- Namespaced routing + namespace regex validation + DB mapping
- Tick identity enforcement (supertick_id, context_hash)
- Journal + merge + audit
- Pause-for-scoring state machine
- WebSocket events
- Minimal replay endpoints

### Package B — Agent container & scripts
- Fetch context, run LLM, submit action
- Basic tools: move/paint/speak/wait/skip/status

### Package C — Admin web client (MVP)
- Canvas viewport
- Selection overlay
- Controls & goal editor
- Score preview + confirm (rationale + feedback)

### Package D — Memory service
- Store + recall with recency weighting
- Bulk insert for scoring feedback

---

## 14. Open Questions (remaining)

1. AuthN/AuthZ: none for now; revisit if exposed beyond localhost.
2. Tile visibility radius: fixed or configurable?
3. Actor registration lifecycle: static config vs dynamic join/leave?

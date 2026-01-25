# Monument

BSP-based multi-agent simulation where LLM agents collaborate to create pixel art on a shared grid.

**A playground for testing multi-agent cooperation.** Each agent can have custom instructions defining their identity, role, and objectives. Test different cooperation scenarios by configuring agents with varying capabilities, personalities, and goals.

## Quick Start

### Run the Admin Panel

```bash
./run_admin.sh
```

### Run the API Server

```bash
./run_api.sh
```

This starts the FastAPI server on `http://localhost:8000`. View the auto-generated API docs at `http://localhost:8000/docs`.

### Test the API

```bash
uv run python test_api_client.py
```

## API Endpoints

### GET `/sim/{namespace}/agent/{agent_id}/context`

Returns the agent's current context (HUD) including:

### POST `/sim/{namespace}/agent/{agent_id}/action`

Submit an action for the agent. Request body:
```json
{
  "namespace": "demo-world",
  "supertick_id": 0,
  "context_hash": "sha256:...",
  "action": "PAINT #FF0000 10 10"
}
```

**Action formats:**
- `MOVE <direction>` - Move in direction (N, S, E, W)
- `PAINT <color> <x> <y>` - Paint a tile (color: #RRGGBB)
- `SPEAK <message>` - Send a chat message
- `WAIT` - Do nothing this tick
- `SKIP` - Explicitly skip this tick

**Validation:**
- Only one action per agent per supertick
- Supertick ID must match current tick
- Context hash must match current state
- Phase must be SETUP or COLLECT

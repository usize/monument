"""
Monument API Server
Handles agent context retrieval and action submission.
"""

import hashlib
import json
import time
from typing import Optional

from fastapi import FastAPI, HTTPException, Header, Query
from pydantic import BaseModel

from monument.server.db import db_manager
from monument.server import game_engine


app = FastAPI(title="Monument API", version="1.0.0")


# ============================================================================
# Request/Response Models
# ============================================================================

class ActionSubmission(BaseModel):
    namespace: str
    supertick_id: int
    context_hash: str
    action: str  # e.g., "PAINT #FF0000", "MOVE N", "SPEAK hello", "WAIT"
    llm_input: Optional[str] = None  # Full prompt sent to LLM (for traceability)
    llm_output: Optional[str] = None  # Full response from LLM (for traceability)


class ContextResponse(BaseModel):
    namespace: str
    supertick_id: int
    context_hash: str
    phase: str
    hud: str


class ActionResponse(BaseModel):
    success: bool
    message: str


# ============================================================================
# Helper Functions
# ============================================================================

def compute_context_hash(namespace: str, supertick_id: int, phase: str, goal: str) -> str:
    """
    Compute a stable hash for the current context.
    Agents use this to ensure they're submitting against the correct snapshot.
    """
    payload = f"{namespace}:{supertick_id}:{phase}:{goal}"
    return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()[:16]}"


def authenticate_actor(conn, actor_id: str, provided_secret: str) -> Optional[dict]:
    """
    Authenticate an actor by their secret.

    Returns:
        Actor data dict if authentication successful, None otherwise
    """
    cursor = conn.execute(
        "SELECT id, secret, x, y, facing, scopes FROM actors WHERE id = ? AND eliminated_at IS NULL",
        (actor_id,)
    )
    row = cursor.fetchone()

    if not row:
        return None

    actor_data = {
        "id": row[0],
        "secret": row[1],
        "x": row[2],
        "y": row[3],
        "facing": row[4],
        "scopes": json.loads(row[5])
    }

    # Verify secret
    if actor_data["secret"] != provided_secret:
        return None

    return actor_data


def build_hud(
    conn,
    actor_id: str,
    namespace: str,
    supertick_id: int,
    context_hash: str,
    history_length: int = 3,
    chat_length: int = 3,
) -> str:
    """
    Build the HUD (Heads-Up Display) for an agent.
    Returns formatted text with all necessary context.
    """
    # Get metadata
    cursor = conn.execute("SELECT key, value FROM meta")
    meta = {row[0]: row[1] for row in cursor.fetchall()}

    # Get actor info including scopes and custom instructions
    cursor = conn.execute(
        "SELECT x, y, facing, scopes, custom_instructions FROM actors WHERE id = ? AND eliminated_at IS NULL",
        (actor_id,)
    )
    actor_row = cursor.fetchone()
    if not actor_row:
        return None

    x, y, facing, scopes_json, custom_instructions = actor_row
    scopes = json.loads(scopes_json)

    # Get world bounds early for HUD usage
    width = int(meta.get('width', 64))
    height = int(meta.get('height', 64))

    # Get all tiles (full map visibility, no viewport restriction)
    cursor = conn.execute(
        """
        SELECT x, y, color FROM tiles
        ORDER BY y, x
        """
    )
    visible_tiles = cursor.fetchall()
    tile_map = {(tx, ty): color for tx, ty, color in visible_tiles}

    # Build HUD sections
    hud = []
    hud.append("=" * 60)
    hud.append("MONUMENT - AGENT CONTEXT")
    hud.append("=" * 60)
    hud.append("")
    hud.append(f"NAMESPACE: {namespace}")
    hud.append(f"SUPERTICK: {supertick_id}")
    hud.append(f"AGENT: {actor_id}")
    hud.append(f"POSITION: ({x}, {y})")
    hud.append(f"FACING: {facing}")
    hud.append(f"PHASE: {meta.get('phase', 'UNKNOWN')}")
    hud.append("")
    hud.append("COORD SYSTEM:")
    hud.append("  Origin (0,0) is top-left. +X = east/right. +Y = south/down.")
    hud.append("  MOVE N decreases Y. MOVE S increases Y. MOVE E increases X. MOVE W decreases X.")
    hud.append("")

    # Simple local compass visualization
    def format_direction(dx: int, dy: int, direction: str) -> str:
        target_x = x + dx
        target_y = y + dy
        if not (0 <= target_x < width and 0 <= target_y < height):
            return f"[{direction}] (wall)"
        tile_color = tile_map.get((target_x, target_y))
        color_info = f" {tile_color}" if tile_color else ""
        return f"[{direction}] ({target_x},{target_y}){color_info}"

    hud.append("LOCAL COMPASS (you are @):")
    hud.append(f"        {format_direction(0, -1, 'N')}")
    hud.append(
        f"{format_direction(-1, 0, 'W')}    @ ({x},{y}) {tile_map.get((x, y), '')}    {format_direction(1, 0, 'E')}"
    )
    hud.append(f"        {format_direction(0, 1, 'S')}")
    hud.append("")

    # Custom instructions (agent's identity and objectives)
    if custom_instructions:
        hud.append("YOUR IDENTITY & OBJECTIVES:")
        # Split by newlines and indent each line
        for line in custom_instructions.split('\n'):
            hud.append(f"  {line}")
        hud.append("")

    hud.append(f"WORLD GOAL: {meta.get('goal', 'None')}")
    hud.append("")

    # Get all actors
    cursor = conn.execute(
        """
        SELECT id, x, y, facing FROM actors
        WHERE eliminated_at IS NULL
        """
    )
    visible_actors = cursor.fetchall()

    # Build world state section
    hud.append("WORLD TILES:")
    hud.append(f"  World size: {width}x{height}")
    hud.append(f"  Total tiles: {len(visible_tiles)}")

    # Group tiles by color for compact display
    color_counts = {}
    for tile_x, tile_y, color in visible_tiles:
        if color not in color_counts:
            color_counts[color] = []
        color_counts[color].append((tile_x, tile_y))

    hud.append(f"  Colors present:")
    for color, positions in sorted(color_counts.items()):
        if len(positions) <= 3:
            # Show all positions for rare colors
            pos_str = ", ".join([f"({px},{py})" for px, py in positions])
            hud.append(f"    {color}: {pos_str}")
        else:
            # Just show count for common colors
            hud.append(f"    {color}: {len(positions)} tiles")

    hud.append("")
    hud.append("ACTORS:")
    if visible_actors:
        for other_id, other_x, other_y, other_facing in visible_actors:
            if other_id == actor_id:
                hud.append(f"  {other_id} (YOU) at ({other_x}, {other_y}) facing {other_facing}")
            else:
                distance = abs(other_x - x) + abs(other_y - y)  # Manhattan distance
                hud.append(f"  {other_id} at ({other_x}, {other_y}) facing {other_facing} [distance: {distance}]")
    else:
        hud.append("  No other actors")

    hud.append("")

    # Context from previous supertick (audit history)
    if supertick_id > 0:
        prev_tick = supertick_id - 1
        cursor = conn.execute(
            """
            SELECT actor_id, action_type, params_json, result_json FROM audit
            WHERE supertick_id = ?
            ORDER BY id ASC
            """,
            (prev_tick,)
        )
        prev_actions = cursor.fetchall()

        hud.append(f"PREVIOUS SUPERTICK ({prev_tick}) RESULTS:")
        if prev_actions:
            for audit_actor_id, action_type, params_json, result_json in prev_actions:
                params = json.loads(params_json) if params_json else {}
                result = json.loads(result_json) if result_json else {}
                outcome = result.get("outcome", "UNKNOWN")
                reason = result.get("reason", "")
                params_str = params.get("params", "") if params else ""
                if audit_actor_id == actor_id:
                    hud.append(f"  (YOU) {action_type} {params_str} -> {outcome}: {reason}")
                else:
                    hud.append(f"  {audit_actor_id}: {action_type} {params_str} -> {outcome}: {reason}")
        else:
            hud.append("  No actions recorded")
        hud.append("")

    # Per-agent history with LLM outputs for quick recall
    cursor = conn.execute(
        """
        SELECT supertick_id, action_type, params_json, result_json, llm_output
        FROM audit
        WHERE actor_id = ?
        ORDER BY supertick_id DESC, id DESC
        LIMIT ?
        """,
        (actor_id, history_length)
    )
    recent_actions = cursor.fetchall()

    hud.append(f"YOUR LAST {history_length} ACTIONS (most recent last):")
    if recent_actions:
        for row in reversed(recent_actions):
            row_tick, action_type, params_json, result_json, llm_output = row
            params = json.loads(params_json) if params_json else {}
            params_str = params.get("params", "")
            result = json.loads(result_json) if result_json else {}
            outcome = result.get("outcome", "UNKNOWN")
            reason = result.get("reason", "")
            hud.append(f"  Tick {row_tick}: {action_type} {params_str} -> {outcome}: {reason}")
            if llm_output:
                hud.append("    LLM RESPONSE:")
                for line in llm_output.strip().splitlines():
                    hud.append(f"      {line}")
    else:
        hud.append("  No historical actions available")
    hud.append("")

    # Supervisor visibility into other agents' actions
    if "SUPERVISOR" in scopes:
        cursor = conn.execute(
            """
            SELECT supertick_id, actor_id, action_type, params_json
            FROM audit
            WHERE actor_id != ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (actor_id, history_length)
        )
        supervisor_actions = cursor.fetchall()
        hud.append(f"SUPERVISOR ACTION LOG (last {history_length} from other agents):")
        if supervisor_actions:
            for row in reversed(supervisor_actions):
                log_tick, log_actor_id, log_action, log_params_json = row
                params = json.loads(log_params_json) if log_params_json else {}
                params_str = params.get("params", "")
                hud.append(f"  Tick {log_tick}: {log_actor_id} -> {log_action} {params_str}")
        else:
            hud.append("  No recent actions from other agents")
        hud.append("")

    # Recent chat messages regardless of tick
    cursor = conn.execute(
        """
        SELECT supertick_id, from_id, message
        FROM chat
        ORDER BY id DESC
        LIMIT ?
        """,
        (chat_length,)
    )
    chat_messages = cursor.fetchall()

    hud.append(f"RECENT CHAT (last {chat_length} messages, oldest first):")
    if chat_messages:
        for msg_tick, from_id, message in reversed(chat_messages):
            tick_label = "current" if msg_tick == supertick_id else f"tick {msg_tick}"
            hud.append(f"  [{tick_label}] {from_id}: {message}")
    else:
        hud.append("  No chat messages yet")

    hud.append("")

    # TODO: Add recalled memories

    hud.append("AVAILABLE ACTIONS:")

    # Filter actions based on agent's scopes
    action_descriptions = {
        "MOVE": "  MOVE <direction>     - Move in direction (N, S, E, W)",
        "PAINT": "  PAINT <color>        - Paint your current tile (color: #RRGGBB)",
        "SPEAK": "  SPEAK <message>      - Send a chat message",
        "WAIT": "  WAIT                 - Do nothing this tick",
        "SKIP": "  SKIP                 - Explicitly skip this tick"
    }

    allowed_actions = [action_descriptions[scope] for scope in scopes if scope in action_descriptions]

    if allowed_actions:
        for action_desc in allowed_actions:
            hud.append(action_desc)
    else:
        hud.append("  (No actions available)")

    hud.append("")
    hud.append("=" * 60)

    return "\n".join(hud)


def validate_action_submission(conn, actor_id: str, submission: ActionSubmission) -> Optional[str]:
    """
    Validate an action submission.
    Returns error message if invalid, None if valid.
    """
    # Get current meta
    cursor = conn.execute("SELECT key, value FROM meta")
    meta = {row[0]: row[1] for row in cursor.fetchall()}

    current_supertick = int(meta.get('supertick_id', 0))
    current_phase = meta.get('phase', 'UNKNOWN')
    current_goal = meta.get('goal', '')

    # Compute expected context hash
    expected_hash = compute_context_hash(submission.namespace, current_supertick, current_phase, current_goal)

    # Validate supertick_id
    if submission.supertick_id != current_supertick:
        return f"Supertick mismatch. Expected {current_supertick}, got {submission.supertick_id}"

    # Validate context_hash
    if submission.context_hash != expected_hash:
        return f"Context hash mismatch. Expected {expected_hash}, got {submission.context_hash}"

    # Validate phase (must be COLLECT to accept actions)
    # For now, we'll accept in SETUP phase too for testing
    if current_phase not in ['SETUP', 'COLLECT']:
        return f"Cannot submit actions in phase {current_phase}"

    # Check if actor already submitted this tick
    cursor = conn.execute(
        "SELECT 1 FROM journal WHERE supertick_id = ? AND actor_id = ?",
        (current_supertick, actor_id)
    )
    if cursor.fetchone():
        return f"Agent {actor_id} already submitted an action for supertick {current_supertick}"

    # Check if actor exists and is not eliminated
    cursor = conn.execute(
        "SELECT 1 FROM actors WHERE id = ? AND eliminated_at IS NULL",
        (actor_id,)
    )
    if not cursor.fetchone():
        return f"Actor {actor_id} not found or eliminated"

    return None  # Valid!


# ============================================================================
# API Endpoints
# ============================================================================

@app.get("/")
async def root():
    """API health check."""
    return {"status": "ok", "service": "Monument API"}


@app.get("/sim/{namespace}/agent/{agent_id}/context", response_model=ContextResponse)
async def get_agent_context(
    namespace: str,
    agent_id: str,
    x_agent_secret: str = Header(..., description="Agent authentication secret"),
    history_length: int = Query(3, ge=1, le=20, description="Number of previous actions + LLM responses to include"),
    chat_length: Optional[int] = Query(
        None,
        ge=1,
        le=50,
        description="Number of chat messages to include in HUD. Defaults to history_length if omitted."
    )
):
    """
    Get the current context (HUD) for an agent.
    This provides all information needed to decide on an action.
    Requires X-Agent-Secret header for authentication.
    """
    try:
        # Validate namespace
        db_manager.validate_namespace(namespace)

        # Get connection
        conn = db_manager.get_connection(namespace)

        # Authenticate agent
        actor_data = authenticate_actor(conn, agent_id, x_agent_secret)
        if not actor_data:
            conn.close()
            raise HTTPException(status_code=401, detail=f"Authentication failed for agent {agent_id}")

        # Get current state
        cursor = conn.execute("SELECT key, value FROM meta")
        meta = {row[0]: row[1] for row in cursor.fetchall()}

        supertick_id = int(meta.get('supertick_id', 0))
        phase = meta.get('phase', 'SETUP')
        goal = meta.get('goal', '')

        # Compute context hash
        context_hash = compute_context_hash(namespace, supertick_id, phase, goal)

        # Build HUD
        chat_length_value = chat_length if chat_length is not None else history_length

        hud = build_hud(
            conn,
            agent_id,
            namespace,
            supertick_id,
            context_hash,
            history_length=history_length,
            chat_length=chat_length_value
        )
        if hud is None:
            conn.close()
            raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")

        conn.close()

        return ContextResponse(
            namespace=namespace,
            supertick_id=supertick_id,
            context_hash=context_hash,
            phase=phase,
            hud=hud
        )

    except db_manager.NamespaceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/sim/{namespace}/agent/{agent_id}/action", response_model=ActionResponse)
async def submit_agent_action(
    namespace: str,
    agent_id: str,
    submission: ActionSubmission,
    x_agent_secret: str = Header(..., description="Agent authentication secret")
):
    """
    Submit an action for an agent.
    Only one action per agent per supertick is allowed.
    Requires X-Agent-Secret header for authentication.
    """
    try:
        # Validate namespace
        db_manager.validate_namespace(namespace)

        # Verify namespace matches
        if submission.namespace != namespace:
            raise HTTPException(status_code=400, detail="Namespace mismatch in URL and body")

        # Get connection
        conn = db_manager.get_connection(namespace)

        # Authenticate agent
        actor_data = authenticate_actor(conn, agent_id, x_agent_secret)
        if not actor_data:
            conn.close()
            raise HTTPException(status_code=401, detail=f"Authentication failed for agent {agent_id}")

        # Validate submission
        error = validate_action_submission(conn, agent_id, submission)
        if error:
            conn.close()
            raise HTTPException(status_code=400, detail=error)

        # Parse action
        action_parts = submission.action.strip().split(maxsplit=1)
        intent = action_parts[0].upper()
        params = action_parts[1] if len(action_parts) > 1 else ""

        # Valid intents
        valid_intents = ['MOVE', 'PAINT', 'SPEAK', 'WAIT', 'SKIP']
        if intent not in valid_intents:
            conn.close()
            raise HTTPException(status_code=400, detail=f"Invalid intent '{intent}'. Must be one of: {valid_intents}")

        # Validate action parameters
        if intent == 'MOVE':
            direction = params.strip().upper()
            if direction not in ['N', 'S', 'E', 'W']:
                conn.close()
                raise HTTPException(
                    status_code=400,
                    detail=f"MOVE action requires direction (N, S, E, or W). Got: '{submission.action}'"
                )
        elif intent == 'PAINT':
            color = params.strip()
            if not color:
                conn.close()
                raise HTTPException(
                    status_code=400,
                    detail=f"PAINT action requires format 'PAINT <color>'. Got: '{submission.action}'"
                )
        elif intent == 'SPEAK':
            if not params.strip():
                conn.close()
                raise HTTPException(
                    status_code=400,
                    detail=f"SPEAK action requires a message. Got: '{submission.action}'"
                )

        # Check if agent has permission for this action (scope check)
        if intent not in actor_data["scopes"]:
            conn.close()
            raise HTTPException(
                status_code=403,
                detail=f"Action '{intent}' not allowed. Agent scopes: {actor_data['scopes']}"
            )

        # Insert into journal as pending
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO journal (supertick_id, actor_id, intent, params_json, status, result_json, llm_input, llm_output, submitted_at)
            VALUES (?, ?, ?, ?, 'pending', NULL, ?, ?, ?)
            """,
            (
                submission.supertick_id,
                agent_id,
                intent,
                json.dumps({"params": params}),
                submission.llm_input,
                submission.llm_output,
                int(time.time())
            )
        )
        conn.commit()
        conn.close()

        # Check if we can auto-advance the tick
        can_advance, reason = game_engine.can_advance_tick(namespace)
        if can_advance:
            # All agents submitted - trigger merge and advance
            merge_results = game_engine.merge_and_advance_tick(namespace)
            return ActionResponse(
                success=True,
                message=f"Action '{intent}' submitted. Tick advanced: {merge_results['tick']} → {merge_results['tick'] + 1}. {reason}"
            )
        else:
            return ActionResponse(
                success=True,
                message=f"Action '{intent}' submitted for agent {agent_id} at supertick {submission.supertick_id}. {reason}"
            )

    except db_manager.NamespaceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

"""
Monument Game Engine
Handles BSP tick loop: COLLECT → MERGE → BROADCAST
"""

import json
import sqlite3
import time
from typing import List, Dict, Tuple, Optional

from monument.server.db import db_manager


def can_advance_tick(namespace: str) -> Tuple[bool, str]:
    """
    Check if we can advance to the next tick.

    Returns:
        (can_advance, reason)
    """
    conn = db_manager.get_connection(namespace)
    cursor = conn.cursor()

    # Get current state
    cursor.execute("SELECT key, value FROM meta")
    meta = {row[0]: row[1] for row in cursor.fetchall()}

    supertick_id = int(meta.get('supertick_id', 0))
    epoch = int(meta.get('epoch', 0))
    phase = meta.get('phase', 'SETUP')

    # Check if already at epoch limit
    if supertick_id >= epoch:
        conn.close()
        return False, f"Reached epoch limit ({epoch} ticks). Set new epoch to continue."

    # Check if in SETUP (no agents registered yet)
    if phase == 'SETUP':
        cursor.execute("SELECT COUNT(*) FROM actors WHERE eliminated_at IS NULL")
        agent_count = cursor.fetchone()[0]
        if agent_count == 0:
            conn.close()
            return False, "No agents registered yet"

    # Count registered agents
    cursor.execute("SELECT COUNT(*) FROM actors WHERE eliminated_at IS NULL")
    total_agents = cursor.fetchone()[0]

    # Count submitted actions for current tick
    cursor.execute(
        "SELECT COUNT(DISTINCT actor_id) FROM journal WHERE supertick_id = ? AND status = 'pending'",
        (supertick_id,)
    )
    submitted_agents = cursor.fetchone()[0]

    conn.close()

    if submitted_agents < total_agents:
        return False, f"Waiting for agents: {submitted_agents}/{total_agents} submitted"

    return True, f"All {total_agents} agents submitted"


def merge_and_advance_tick(namespace: str) -> Dict:
    """
    Execute MERGE phase and advance to next tick.

    Returns:
        Summary of merge results
    """
    conn = db_manager.get_connection(namespace)
    cursor = conn.cursor()

    # Get current state
    cursor.execute("SELECT key, value FROM meta")
    meta = {row[0]: row[1] for row in cursor.fetchall()}

    current_tick = int(meta.get('supertick_id', 0))
    width = int(meta.get('width', 64))
    height = int(meta.get('height', 64))

    # Gather pending actions
    cursor.execute(
        "SELECT actor_id, intent, params_json FROM journal WHERE supertick_id = ? AND status = 'pending'",
        (current_tick,)
    )
    pending_actions = cursor.fetchall()

    # Process actions
    results = {
        'tick': current_tick,
        'total_actions': len(pending_actions),
        'success': 0,
        'conflict_lost': 0,
        'invalid': 0,
        'no_op': 0
    }

    # Group actions by type for conflict detection
    moves = []  # (actor_id, dest_x, dest_y, params_json)
    paints = []  # (actor_id, tile_x, tile_y, color, params_json)
    speaks = []  # (actor_id, message, params_json)

    for actor_id, intent, params_json in pending_actions:
        params = json.loads(params_json)
        params_str = params.get('params', '')

        if intent == 'MOVE':
            # Get current position
            cursor.execute("SELECT x, y, facing FROM actors WHERE id = ?", (actor_id,))
            row = cursor.fetchone()
            if not row:
                continue
            x, y, facing = row

            # Parse direction
            direction = params_str.strip().upper()

            # Validate direction
            if direction not in ['N', 'S', 'E', 'W']:
                # Invalid direction - mark as invalid
                cursor.execute(
                    "UPDATE journal SET status = 'rejected', result_json = ? WHERE supertick_id = ? AND actor_id = ?",
                    (json.dumps({'outcome': 'INVALID', 'reason': f'Invalid direction "{params_str}". Must be N, S, E, or W'}), current_tick, actor_id)
                )
                results['invalid'] += 1
                continue

            dest_x, dest_y = x, y
            new_facing = direction

            if direction == 'N':
                dest_y = max(0, y - 1)
            elif direction == 'S':
                dest_y = min(height - 1, y + 1)
            elif direction == 'E':
                dest_x = min(width - 1, x + 1)
            elif direction == 'W':
                dest_x = max(0, x - 1)

            moves.append((actor_id, dest_x, dest_y, new_facing, params_json))

        elif intent == 'PAINT':
            # Get current position - agents can only paint their current tile
            cursor.execute("SELECT x, y FROM actors WHERE id = ?", (actor_id,))
            row = cursor.fetchone()
            if not row:
                continue
            tile_x, tile_y = row

            # Parse color only
            color = params_str.strip()
            if color:
                paints.append((actor_id, tile_x, tile_y, color, params_json))

        elif intent == 'SPEAK':
            speaks.append((actor_id, params_str, params_json))

    # Resolve MOVE conflicts (deterministic by actor_id)
    move_destinations = {}  # (dest_x, dest_y) -> [actor_ids]
    for actor_id, dest_x, dest_y, new_facing, params_json in moves:
        key = (dest_x, dest_y)
        if key not in move_destinations:
            move_destinations[key] = []
        move_destinations[key].append((actor_id, new_facing, params_json))

    for (dest_x, dest_y), actors_list in move_destinations.items():
        if len(actors_list) == 1:
            # No conflict
            actor_id, new_facing, params_json = actors_list[0]
            # Apply move
            cursor.execute(
                "UPDATE actors SET x = ?, y = ?, facing = ? WHERE id = ?",
                (dest_x, dest_y, new_facing, actor_id)
            )
            # Mark as committed
            cursor.execute(
                "UPDATE journal SET status = 'committed', result_json = ? WHERE supertick_id = ? AND actor_id = ?",
                (json.dumps({'outcome': 'SUCCESS', 'reason': f'Moved to ({dest_x}, {dest_y})'}), current_tick, actor_id)
            )
            results['success'] += 1
        else:
            # Conflict - sort by actor_id for determinism
            sorted_actors = sorted(actors_list, key=lambda x: x[0])
            winner = sorted_actors[0]
            losers = sorted_actors[1:]

            # Winner moves
            actor_id, new_facing, params_json = winner
            cursor.execute(
                "UPDATE actors SET x = ?, y = ?, facing = ? WHERE id = ?",
                (dest_x, dest_y, new_facing, actor_id)
            )
            cursor.execute(
                "UPDATE journal SET status = 'committed', result_json = ? WHERE supertick_id = ? AND actor_id = ?",
                (json.dumps({'outcome': 'SUCCESS', 'reason': f'Won conflict, moved to ({dest_x}, {dest_y})'}), current_tick, actor_id)
            )
            results['success'] += 1

            # Losers stay in place
            for actor_id, new_facing, params_json in losers:
                cursor.execute(
                    "UPDATE journal SET status = 'rejected', result_json = ? WHERE supertick_id = ? AND actor_id = ?",
                    (json.dumps({'outcome': 'CONFLICT_LOST', 'reason': f'Lost move conflict to {winner[0]}'}), current_tick, actor_id)
                )
                results['conflict_lost'] += 1

    # Resolve PAINT conflicts (deterministic by actor_id)
    paint_tiles = {}  # (tile_x, tile_y) -> [actor_ids]
    for actor_id, tile_x, tile_y, color, params_json in paints:
        key = (tile_x, tile_y)
        if key not in paint_tiles:
            paint_tiles[key] = []
        paint_tiles[key].append((actor_id, color, params_json))

    for (tile_x, tile_y), actors_list in paint_tiles.items():
        # Get current tile color
        cursor.execute("SELECT color FROM tiles WHERE x = ? AND y = ?", (tile_x, tile_y))
        row = cursor.fetchone()
        current_color = row[0] if row else "#FFFFFF"

        if len(actors_list) == 1:
            # No conflict
            actor_id, color, params_json = actors_list[0]

            if color == current_color:
                # NO_OP - already that color
                cursor.execute(
                    "UPDATE journal SET status = 'committed', result_json = ? WHERE supertick_id = ? AND actor_id = ?",
                    (json.dumps({'outcome': 'NO_OP', 'reason': f'Tile already {color}'}), current_tick, actor_id)
                )
                results['no_op'] += 1
            else:
                # Apply paint
                cursor.execute(
                    "UPDATE tiles SET color = ? WHERE x = ? AND y = ?",
                    (color, tile_x, tile_y)
                )
                # Record in tile_history
                cursor.execute(
                    "INSERT INTO tile_history (x, y, supertick_id, actor_id, action_type, old_color, new_color, created_at) VALUES (?, ?, ?, ?, 'PAINT', ?, ?, ?)",
                    (tile_x, tile_y, current_tick, actor_id, current_color, color, int(time.time()))
                )
                cursor.execute(
                    "UPDATE journal SET status = 'committed', result_json = ? WHERE supertick_id = ? AND actor_id = ?",
                    (json.dumps({'outcome': 'SUCCESS', 'reason': f'Painted ({tile_x}, {tile_y}) {color}'}), current_tick, actor_id)
                )
                results['success'] += 1
        else:
            # Conflict - sort by actor_id for determinism
            sorted_actors = sorted(actors_list, key=lambda x: x[0])
            winner = sorted_actors[0]
            losers = sorted_actors[1:]

            # Winner paints
            actor_id, color, params_json = winner
            cursor.execute(
                "UPDATE tiles SET color = ? WHERE x = ? AND y = ?",
                (color, tile_x, tile_y)
            )
            cursor.execute(
                "INSERT INTO tile_history (x, y, supertick_id, actor_id, action_type, old_color, new_color, created_at) VALUES (?, ?, ?, ?, 'PAINT', ?, ?, ?)",
                (tile_x, tile_y, current_tick, actor_id, current_color, color, int(time.time()))
            )
            cursor.execute(
                "UPDATE journal SET status = 'committed', result_json = ? WHERE supertick_id = ? AND actor_id = ?",
                (json.dumps({'outcome': 'SUCCESS', 'reason': f'Won conflict, painted ({tile_x}, {tile_y}) {color}'}), current_tick, actor_id)
            )
            results['success'] += 1

            # Losers don't paint
            for actor_id, color, params_json in losers:
                cursor.execute(
                    "UPDATE journal SET status = 'rejected', result_json = ? WHERE supertick_id = ? AND actor_id = ?",
                    (json.dumps({'outcome': 'CONFLICT_LOST', 'reason': f'Lost paint conflict to {winner[0]}'}), current_tick, actor_id)
                )
                results['conflict_lost'] += 1

    # Process SPEAK actions (no conflicts)
    for actor_id, message, params_json in speaks:
        # Insert into chat
        cursor.execute(
            "INSERT INTO chat (supertick_id, from_id, message, created_at) VALUES (?, ?, ?, ?)",
            (current_tick, actor_id, message, int(time.time()))
        )
        cursor.execute(
            "UPDATE journal SET status = 'committed', result_json = ? WHERE supertick_id = ? AND actor_id = ?",
            (json.dumps({'outcome': 'SUCCESS', 'reason': 'Message sent'}), current_tick, actor_id)
        )
        results['success'] += 1

    # Process WAIT and SKIP actions
    cursor.execute(
        "UPDATE journal SET status = 'committed', result_json = ? WHERE supertick_id = ? AND intent IN ('WAIT', 'SKIP') AND status = 'pending'",
        (json.dumps({'outcome': 'SUCCESS', 'reason': 'Waited'}), current_tick)
    )

    # Advance tick
    next_tick = current_tick + 1
    cursor.execute("UPDATE meta SET value = ? WHERE key = 'supertick_id'", (str(next_tick),))

    # Update phase if needed
    epoch = int(meta.get('epoch', 0))
    if next_tick >= epoch:
        cursor.execute("UPDATE meta SET value = 'PAUSED' WHERE key = 'phase'")
        results['paused'] = True
        results['reason'] = f'Reached epoch limit ({epoch} ticks)'
    else:
        cursor.execute("UPDATE meta SET value = 'COLLECT' WHERE key = 'phase'")
        results['paused'] = False

    conn.commit()
    conn.close()

    return results

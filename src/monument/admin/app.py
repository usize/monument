"""
Monument Admin Panel (Streamlit)
Allows namespace creation, world generation, and agent registration.
"""

import json
import math
import streamlit as st
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# Add parent to path so we can import monument modules
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from monument.server.db import db_manager


st.set_page_config(
    page_title="Monument Admin",
    page_icon="🗿",
    layout="wide"
)

st.title("🗿 Monument Admin Panel")


# ============================================================================
# Rendering Function
# ============================================================================
def get_world_state_at_tick(conn, supertick_id: int):
    """
    Reconstruct world tile state at a specific supertick.

    Args:
        conn: Database connection
        supertick_id: The supertick to reconstruct

    Returns:
        dict mapping (x, y) -> color
    """
    # Start with initial blank state
    cursor = conn.execute("SELECT value FROM meta WHERE key='width'")
    width = int(cursor.fetchone()[0])
    cursor = conn.execute("SELECT value FROM meta WHERE key='height'")
    height = int(cursor.fetchone()[0])

    # Initialize all tiles as white
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


def get_actor_positions_at_tick(conn, supertick_id: int, current_tick: int):
    """
    Get actor positions at a specific supertick from actor_history.

    Args:
        conn: Database connection
        supertick_id: The supertick to get positions for
        current_tick: The current supertick (unused, kept for API compatibility)

    Returns:
        dict mapping actor_id -> (x, y)
    """
    # Get list of all active actors
    cursor = conn.execute("SELECT id FROM actors WHERE eliminated_at IS NULL")
    actor_ids = [row[0] for row in cursor.fetchall()]

    positions = {}

    # For each actor, get their most recent position at or before the target tick
    for actor_id in actor_ids:
        cursor = conn.execute(
            """
            SELECT x, y FROM actor_history
            WHERE actor_id = ? AND supertick_id <= ?
            ORDER BY supertick_id DESC, id DESC
            LIMIT 1
            """,
            (actor_id, supertick_id)
        )
        row = cursor.fetchone()
        if row:
            positions[actor_id] = (row[0], row[1])
        else:
            # Fallback to current position if no history (shouldn't happen with new schema)
            cursor = conn.execute("SELECT x, y FROM actors WHERE id = ?", (actor_id,))
            row = cursor.fetchone()
            if row:
                positions[actor_id] = (row[0], row[1])

    return positions


def get_chat_messages_at_tick(conn, supertick_id: int):
    """
    Get chat messages from a specific supertick.

    Args:
        conn: Database connection
        supertick_id: The supertick to get messages for

    Returns:
        list of (from_id, message) tuples
    """
    cursor = conn.execute(
        """
        SELECT from_id, message
        FROM chat
        WHERE supertick_id = ?
        ORDER BY id ASC
        """,
        (supertick_id,)
    )
    return cursor.fetchall()


def get_agent_decisions_at_tick(conn, supertick_id: int):
    """
    Get agent decisions (actions + LLM context) from the audit table for a specific supertick.

    Args:
        conn: Database connection
        supertick_id: The supertick to get decisions for

    Returns:
        list of dicts with actor_id, action_type, params_json, result_json, llm_input, llm_output
    """
    cursor = conn.execute(
        """
        SELECT actor_id, action_type, params_json, result_json, llm_input, llm_output
        FROM audit
        WHERE supertick_id = ?
        ORDER BY actor_id ASC
        """,
        (supertick_id,)
    )
    rows = cursor.fetchall()
    return [
        {
            'actor_id': row[0],
            'action_type': row[1],
            'params_json': row[2],
            'result_json': row[3],
            'llm_input': row[4],
            'llm_output': row[5],
        }
        for row in rows
    ]


def render_world(conn, tile_size: int = 8, supertick_id: int = None, current_tick: int = None):
    """
    Render the world as a PIL image with tiles and agent names.

    Args:
        conn: Database connection
        tile_size: Pixels per tile (default 8)
        supertick_id: Optional supertick to render (default: current state)
        current_tick: Current supertick (needed for historical position reconstruction)

    Returns:
        PIL Image
    """
    # Get world dimensions
    cursor = conn.execute("SELECT value FROM meta WHERE key='width'")
    width = int(cursor.fetchone()[0])
    cursor = conn.execute("SELECT value FROM meta WHERE key='height'")
    height = int(cursor.fetchone()[0])

    # Create image
    img_width = width * tile_size
    img_height = height * tile_size
    img = Image.new('RGB', (img_width, img_height), color='white')
    draw = ImageDraw.Draw(img)

    # Draw tiles - either from history or current state
    if supertick_id is not None:
        # Reconstruct historical state
        tiles = get_world_state_at_tick(conn, supertick_id)
        for (x, y), color in tiles.items():
            x1 = x * tile_size
            y1 = y * tile_size
            x2 = x1 + tile_size
            y2 = y1 + tile_size
            draw.rectangle([x1, y1, x2, y2], fill=color)
    else:
        # Use current state
        cursor = conn.execute("SELECT x, y, color FROM tiles")
        for row in cursor.fetchall():
            x, y, color = row
            x1 = x * tile_size
            y1 = y * tile_size
            x2 = x1 + tile_size
            y2 = y1 + tile_size
            draw.rectangle([x1, y1, x2, y2], fill=color)

    # Draw agents - use historical positions if viewing a past tick
    if supertick_id is not None and current_tick is not None:
        actor_positions = get_actor_positions_at_tick(conn, supertick_id, current_tick)
        actors = [(actor_id, x, y) for actor_id, (x, y) in actor_positions.items()]
    else:
        cursor = conn.execute("SELECT id, x, y FROM actors WHERE eliminated_at IS NULL")
        actors = cursor.fetchall()

    # Try to load a font, fall back to default if not available
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", size=max(8, tile_size - 2))
    except:
        font = ImageFont.load_default()

    for actor_id, x, y in actors:
        center_x = (x * tile_size) + (tile_size // 2)
        center_y = (y * tile_size) + (tile_size // 2)

        # Draw a small circle for the actor
        circle_radius = min(tile_size // 3, 3)
        draw.ellipse(
            [center_x - circle_radius, center_y - circle_radius,
             center_x + circle_radius, center_y + circle_radius],
            fill='red',
            outline='black'
        )

        # Draw agent name if tile_size is large enough
        if tile_size >= 12:
            # Use textbbox to get text dimensions
            bbox = draw.textbbox((0, 0), actor_id, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]

            text_x = center_x - (text_width // 2)
            text_y = center_y + circle_radius + 2

            # Draw text background for readability
            padding = 1
            draw.rectangle(
                [text_x - padding, text_y - padding,
                 text_x + text_width + padding, text_y + text_height + padding],
                fill='white',
                outline='black'
            )

            # Draw text
            draw.text((text_x, text_y), actor_id, fill='black', font=font)

    return img

# Sidebar for navigation
page = st.sidebar.radio("Navigation", ["Create Namespace", "Manage World"])

# ============================================================================
# PAGE: Create Namespace
# ============================================================================
if page == "Create Namespace":
    st.header("Create New Namespace")

    with st.form("create_namespace"):
        namespace = st.text_input(
            "Namespace ID",
            help="Must match pattern: ^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$"
        )

        col1, col2 = st.columns(2)
        with col1:
            width = st.number_input("World Width", min_value=8, max_value=256, value=64)
        with col2:
            height = st.number_input("World Height", min_value=8, max_value=256, value=64)

        goal = st.text_area("Initial Goal", placeholder="e.g., Create a beautiful sunset scene")

        epoch = st.number_input(
            "Initial Epoch (ticks to run)",
            min_value=1,
            value=10,
            help="Simulation will auto-advance for this many ticks, then pause"
        )

        submitted = st.form_submit_button("Create Namespace")

        if submitted:
            if not namespace:
                st.error("Namespace cannot be empty")
            else:
                try:
                    # Validate namespace
                    db_manager.validate_namespace(namespace)

                    # Check if already exists
                    db_path = db_manager.get_db_path(namespace)
                    if db_path.exists():
                        st.error(f"Namespace '{namespace}' already exists")
                    else:
                        # Create and initialize
                        conn = db_manager.get_connection(namespace)
                        db_manager.init_world(conn, width, height, goal, epoch)
                        conn.close()

                        st.success(f"✅ Created namespace '{namespace}' with {width}×{height} world")
                        st.info("Navigate to 'Manage World' to register agents")

                except db_manager.NamespaceError as e:
                    st.error(str(e))
                except Exception as e:
                    st.error(f"Error creating namespace: {e}")


# ============================================================================
# PAGE: Manage World
# ============================================================================
elif page == "Manage World":
    st.header("Manage World")

    # List available namespaces
    data_dir = Path(__file__).parent.parent.parent.parent / "data" / "sims"
    if not data_dir.exists():
        st.warning("No namespaces found. Create one first!")
    else:
        db_files = list(data_dir.glob("*.db"))
        if not db_files:
            st.warning("No namespaces found. Create one first!")
        else:
            namespaces = [db_file.stem for db_file in db_files]
            selected_namespace = st.selectbox("Select Namespace", namespaces)

            if selected_namespace:
                conn = db_manager.get_connection(selected_namespace)

                # Get world info
                cursor = conn.execute("SELECT key, value FROM meta")
                meta = {row[0]: row[1] for row in cursor.fetchall()}

                # Display world info
                st.subheader("World Information")
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("Size", f"{meta.get('width', '?')}×{meta.get('height', '?')}")
                with col2:
                    current_tick = int(meta.get('supertick_id', 0))
                    epoch = int(meta.get('epoch', 0))
                    st.metric("Supertick", f"{current_tick}/{epoch}")
                with col3:
                    st.metric("Phase", meta.get('phase', 'UNKNOWN'))
                with col4:
                    # Count submitted actions for current tick
                    cursor_temp = conn.execute(
                        "SELECT COUNT(DISTINCT actor_id) FROM journal WHERE supertick_id = ? AND status = 'pending'",
                        (current_tick,)
                    )
                    submitted = cursor_temp.fetchone()[0]
                    total = db_manager.get_registered_actor_count(conn)
                    st.metric("Submissions", f"{submitted}/{total}")

                st.text(f"Goal: {meta.get('goal', 'None')}")

                # Epoch management
                st.subheader("Epoch Control")
                with st.form("epoch_control"):
                    new_epoch = st.number_input(
                        "Set new epoch (number of ticks to run)",
                        min_value=current_tick,
                        value=max(current_tick + 10, epoch),
                        help="Simulation will auto-advance until reaching this tick number"
                    )
                    if st.form_submit_button("Update Epoch & Resume"):
                        cursor_update = conn.cursor()
                        cursor_update.execute("UPDATE meta SET value = ? WHERE key = 'epoch'", (str(new_epoch),))
                        # Resume if paused
                        if meta.get('phase') == 'PAUSED':
                            cursor_update.execute("UPDATE meta SET value = 'COLLECT' WHERE key = 'phase'")
                        conn.commit()
                        st.success(f"Epoch updated to {new_epoch}. Simulation will run until tick {new_epoch}.")
                        st.rerun()

                # World Canvas Viewer
                st.subheader("World Canvas")

                col_size, col_tick = st.columns([2, 3])

                with col_size:
                    tile_size = st.slider(
                        "Tile size (pixels)",
                        min_value=4,
                        max_value=32,
                        value=12,
                        help="Adjust zoom level"
                    )

                with col_tick:
                    # Supertick selector for historical viewing (only show if there's history)
                    if current_tick > 0:
                        view_tick = st.slider(
                            "View supertick",
                            min_value=0,
                            max_value=current_tick,
                            value=current_tick,
                            help="Slide to view previous world states"
                        )
                    else:
                        view_tick = 0
                        st.caption("No history yet (tick 0)")

                # Render world at selected supertick
                if view_tick == current_tick:
                    world_img = render_world(conn, tile_size=tile_size)
                    st.image(world_img, caption=f"{selected_namespace} - Tick {current_tick} (Current)", use_container_width=False)
                else:
                    world_img = render_world(conn, tile_size=tile_size, supertick_id=view_tick, current_tick=current_tick)
                    st.image(world_img, caption=f"{selected_namespace} - Tick {view_tick} (Historical)", use_container_width=False)
                    st.info(f"📜 Viewing historical state at tick {view_tick}. Current tick is {current_tick}.")

                # Chat messages for selected tick
                st.subheader(f"Chat Messages (Tick {view_tick})")
                chat_messages = get_chat_messages_at_tick(conn, view_tick)
                if chat_messages:
                    for from_id, message in chat_messages:
                        st.markdown(f"**{from_id}:** {message}")
                else:
                    st.caption("No messages this tick.")

                # Agent decisions for selected tick
                st.subheader(f"Agent Decisions (Tick {view_tick})")
                decisions = get_agent_decisions_at_tick(conn, view_tick)
                if decisions:
                    for decision in decisions:
                        actor_id = decision['actor_id']
                        action_type = decision['action_type']
                        params = json.loads(decision['params_json']) if decision['params_json'] else {}
                        result = json.loads(decision['result_json']) if decision['result_json'] else {}

                        outcome = result.get('outcome', 'UNKNOWN')
                        reason = result.get('reason', '')
                        params_str = params.get('params', '')

                        # Color-code by outcome
                        if outcome == 'SUCCESS':
                            icon = "✅"
                        elif outcome == 'CONFLICT_LOST':
                            icon = "⚔️"
                        elif outcome == 'NO_OP':
                            icon = "➖"
                        else:
                            icon = "❌"

                        with st.expander(f"{icon} **{actor_id}**: {action_type} {params_str} → {outcome}"):
                            st.markdown(f"**Result:** {reason}")

                            # LLM Context (nested expanders)
                            if decision['llm_input'] or decision['llm_output']:
                                st.markdown("---")
                                st.markdown("**LLM Decision Context:**")

                                if decision['llm_input']:
                                    with st.expander("📥 LLM Input (Prompt)"):
                                        try:
                                            llm_input = json.loads(decision['llm_input'])
                                            if 'system_prompt' in llm_input:
                                                st.markdown("**System Prompt:**")
                                                st.code(llm_input['system_prompt'], language=None)
                                            if 'user_prompt' in llm_input:
                                                st.markdown("**User Prompt:**")
                                                st.code(llm_input['user_prompt'], language=None)
                                        except json.JSONDecodeError:
                                            st.code(decision['llm_input'], language=None)

                                if decision['llm_output']:
                                    with st.expander("📤 LLM Output (Response)"):
                                        st.code(decision['llm_output'], language=None)
                            else:
                                st.caption("No LLM context recorded (action may have been submitted manually)")
                else:
                    st.caption("No decisions recorded for this tick.")

                # Agent registration
                st.subheader("Register Agents")

                # Show current actors
                cursor = conn.execute("SELECT id, secret, x, y, facing, scopes, custom_instructions, llm_model FROM actors WHERE eliminated_at IS NULL")
                actors = cursor.fetchall()

                if actors:
                    st.write(f"**Registered Agents ({len(actors)}):**")
                    for actor in actors:
                        actor_id, secret, x, y, facing, scopes_json, custom_instructions, llm_model = actor

                        with st.expander(f"🤖 {actor_id} at ({x}, {y})"):
                            scopes = json.loads(scopes_json)

                            # Agent details
                            col1, col2 = st.columns(2)
                            with col1:
                                st.text(f"Position: ({x}, {y})")
                                st.text(f"Facing: {facing}")
                            with col2:
                                st.text(f"Secret: {secret}")
                                if st.button("📋 Copy Secret", key=f"copy_{actor_id}"):
                                    st.code(secret, language=None)

                            llm_model_input = st.text_input(
                                "LLM Model (optional override)",
                                value=llm_model or "",
                                key=f"llm_model_{actor_id}"
                            )

                            # Custom instructions editor
                            st.write("**Custom Instructions (Identity & Objectives):**")
                            new_instructions = st.text_area(
                                "Instructions for this agent in this world",
                                value=custom_instructions,
                                height=150,
                                key=f"instructions_{actor_id}",
                                help="Define this agent's identity, personality, and specific objectives for this world"
                            )

                            # Scopes editor
                            st.write("**Allowed Actions (Scopes):**")
                            all_scopes = ["MOVE", "PAINT", "SPEAK", "WAIT", "SKIP"]

                            # Create checkboxes for each scope
                            new_scopes = []
                            cols = st.columns(len(all_scopes))
                            for i, scope in enumerate(all_scopes):
                                with cols[i]:
                                    if st.checkbox(scope, value=(scope in scopes), key=f"scope_{actor_id}_{scope}"):
                                        new_scopes.append(scope)

                            # Update buttons
                            col_save_inst, col_update_scopes = st.columns(2)
                            with col_save_inst:
                                if st.button("💾 Save Instructions", key=f"save_inst_{actor_id}"):
                                    db_manager.update_actor_instructions(conn, actor_id, new_instructions)
                                    st.success(f"Updated instructions for {actor_id}")
                                    st.rerun()

                            with col_update_scopes:
                                if st.button("💾 Update Scopes", key=f"update_{actor_id}"):
                                    db_manager.update_actor_scopes(conn, actor_id, new_scopes)
                                    st.success(f"Updated scopes for {actor_id}")
                                    st.rerun()

                            if st.button("💾 Save LLM Model", key=f"save_llm_{actor_id}"):
                                db_manager.update_actor_llm_model(conn, actor_id, llm_model_input.strip())
                                st.success(f"Updated LLM model for {actor_id}")
                                st.rerun()

                            col_regen, col_delete = st.columns(2)
                            with col_regen:
                                if st.button("🔄 New Secret", key=f"regen_{actor_id}"):
                                    new_secret = db_manager.regenerate_actor_secret(conn, actor_id)
                                    st.success(f"New secret: {new_secret}")
                                    st.rerun()

                            with col_delete:
                                if st.button("🗑️ Delete", key=f"delete_{actor_id}"):
                                    db_manager.unregister_actor(conn, actor_id)
                                    st.success(f"Removed {actor_id}")
                                    st.rerun()

                # Add new agents
                with st.form("register_agents"):
                    num_agents = st.number_input(
                        "Number of agents to register",
                        min_value=1,
                        max_value=100,
                        value=1
                    )

                    agent_id_prefix = st.text_input(
                        "Agent ID prefix",
                        value="agent",
                        help="Agents will be named: prefix_0, prefix_1, etc."
                    )

                    custom_secret = st.text_input(
                        "Custom Secret (optional)",
                        value="",
                        help="Leave empty to auto-generate. If registering multiple agents, all will get unique auto-generated secrets."
                    )

                    st.write("**Default Scopes (allowed actions):**")
                    default_scopes = []
                    scope_cols = st.columns(5)
                    all_scopes = ["MOVE", "PAINT", "SPEAK", "WAIT", "SKIP"]
                    for i, scope in enumerate(all_scopes):
                        with scope_cols[i]:
                            if st.checkbox(scope, value=True, key=f"reg_scope_{scope}"):
                                default_scopes.append(scope)

                    default_instructions = st.text_area(
                        "Default Custom Instructions (optional)",
                        value="",
                        height=120,
                        help="These instructions will be applied to all registered agents. Leave empty for no instructions. You can edit individual agents later."
                    )

                    default_llm_model = st.text_input(
                        "Default LLM Model (optional, applies to these agents)",
                        value="",
                        help="Set a model identifier per agent (e.g., local model path or API model). Leave blank to use agent defaults."
                    )

                    register_button = st.form_submit_button("Register Agents (Grid Layout)")

                    if register_button:
                        try:
                            width = int(meta.get('width', 64))
                            height = int(meta.get('height', 64))

                            # Calculate grid layout
                            grid_cols = math.ceil(math.sqrt(num_agents))
                            grid_rows = math.ceil(num_agents / grid_cols)

                            x_spacing = width / (grid_cols + 1)
                            y_spacing = height / (grid_rows + 1)

                            # Validate at least one scope is selected
                            if not default_scopes:
                                st.error("Please select at least one scope")
                            else:
                                # Register agents in grid
                                registered_secrets = []
                                for i in range(num_agents):
                                    agent_id = f"{agent_id_prefix}_{i}"
                                    row = i // grid_cols
                                    col = i % grid_cols

                                    x = min(width - 1, max(0, int(round((col + 1) * x_spacing) - 1)))
                                    y = min(height - 1, max(0, int(round((row + 1) * y_spacing) - 1)))

                                    # Use custom secret only if registering a single agent
                                    agent_secret = custom_secret if (num_agents == 1 and custom_secret) else None

                                    secret = db_manager.register_actor(
                                        conn, agent_id, x, y, "N",
                                        scopes=default_scopes,
                                        secret=agent_secret,
                                        custom_instructions=default_instructions,
                                        llm_model=default_llm_model.strip()
                                    )
                                    registered_secrets.append((agent_id, secret))

                                st.success(f"✅ Registered {num_agents} agents in grid layout")

                                # Show secrets
                                st.write("**Agent Secrets:**")
                                for agent_id, secret in registered_secrets:
                                    st.code(f"{agent_id}: {secret}", language=None)

                                st.rerun()

                        except Exception as e:
                            st.error(f"Error registering agents: {e}")

                conn.close()

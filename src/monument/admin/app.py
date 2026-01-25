"""
Monument Admin Panel (Streamlit)
Allows namespace creation, world generation, and agent registration.
"""

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


def render_world(conn, tile_size: int = 8, supertick_id: int = None):
    """
    Render the world as a PIL image with tiles and agent names.

    Args:
        conn: Database connection
        tile_size: Pixels per tile (default 8)
        supertick_id: Optional supertick to render (default: current state)

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

    # Draw agents (always shows current positions)
    # Note: We don't track actor position history yet, so historical views
    # show current agent positions overlaid on historical tile state
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
                    # Supertick selector for historical viewing
                    view_tick = st.slider(
                        "View supertick",
                        min_value=0,
                        max_value=current_tick,
                        value=current_tick,
                        help="Slide to view previous world states"
                    )

                # Render world at selected supertick
                if view_tick == current_tick:
                    world_img = render_world(conn, tile_size=tile_size)
                    st.image(world_img, caption=f"{selected_namespace} - Tick {current_tick} (Current)", use_container_width=False)
                else:
                    world_img = render_world(conn, tile_size=tile_size, supertick_id=view_tick)
                    st.image(world_img, caption=f"{selected_namespace} - Tick {view_tick} (Historical)", use_container_width=False)
                    st.info(f"📜 Viewing historical state at tick {view_tick}. Current tick is {current_tick}.")

                # Agent registration
                st.subheader("Register Agents")

                # Show current actors
                cursor = conn.execute("SELECT id, secret, x, y, facing, scopes, custom_instructions FROM actors WHERE eliminated_at IS NULL")
                actors = cursor.fetchall()

                if actors:
                    st.write(f"**Registered Agents ({len(actors)}):**")
                    for actor in actors:
                        actor_id, secret, x, y, facing, scopes_json, custom_instructions = actor

                        with st.expander(f"🤖 {actor_id} at ({x}, {y})"):
                            import json
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

                    register_button = st.form_submit_button("Register Agents (Grid Layout)")

                    if register_button:
                        try:
                            width = int(meta.get('width', 64))
                            height = int(meta.get('height', 64))

                            # Calculate grid layout
                            grid_cols = math.ceil(math.sqrt(num_agents))
                            grid_rows = math.ceil(num_agents / grid_cols)

                            x_spacing = width // (grid_cols + 1)
                            y_spacing = height // (grid_rows + 1)

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

                                    x = (col + 1) * x_spacing
                                    y = (row + 1) * y_spacing

                                    # Use custom secret only if registering a single agent
                                    agent_secret = custom_secret if (num_agents == 1 and custom_secret) else None

                                    secret = db_manager.register_actor(
                                        conn, agent_id, x, y, "N",
                                        scopes=default_scopes,
                                        secret=agent_secret,
                                        custom_instructions=default_instructions
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

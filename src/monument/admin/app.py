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
def render_world(conn, tile_size: int = 8):
    """
    Render the world as a PIL image with tiles and agent names.

    Args:
        conn: Database connection
        tile_size: Pixels per tile (default 8)

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

    # Draw tiles
    cursor = conn.execute("SELECT x, y, color FROM tiles")
    for row in cursor.fetchall():
        x, y, color = row
        x1 = x * tile_size
        y1 = y * tile_size
        x2 = x1 + tile_size
        y2 = y1 + tile_size
        draw.rectangle([x1, y1, x2, y2], fill=color)

    # Draw agents
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
                        db_manager.init_world(conn, width, height, goal)
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
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("Size", f"{meta.get('width', '?')}×{meta.get('height', '?')}")
                with col2:
                    st.metric("Supertick", meta.get('supertick_id', '0'))
                with col3:
                    st.metric("Phase", meta.get('phase', 'UNKNOWN'))

                st.text(f"Goal: {meta.get('goal', 'None')}")

                # World Canvas Viewer
                st.subheader("World Canvas")
                tile_size = st.slider(
                    "Tile size (pixels)",
                    min_value=4,
                    max_value=32,
                    value=12,
                    help="Adjust zoom level"
                )

                world_img = render_world(conn, tile_size=tile_size)
                st.image(world_img, caption=f"{selected_namespace} - {meta.get('width')}×{meta.get('height')}", use_container_width=False)

                # Agent registration
                st.subheader("Register Agents")

                # Show current actors
                cursor = conn.execute("SELECT id, x, y, facing, points FROM actors WHERE eliminated_at IS NULL")
                actors = cursor.fetchall()

                if actors:
                    st.write(f"**Registered Agents ({len(actors)}):**")
                    for actor in actors:
                        col_info, col_delete = st.columns([4, 1])
                        with col_info:
                            st.text(f"• {actor[0]} at ({actor[1]}, {actor[2]}) facing {actor[3]} — {actor[4]} points")
                        with col_delete:
                            if st.button(f"🗑️", key=f"delete_{actor[0]}"):
                                db_manager.unregister_actor(conn, actor[0])
                                st.success(f"Removed {actor[0]}")
                                st.rerun()

                # Add new agents
                with st.form("register_agents"):
                    num_agents = st.number_input(
                        "Number of agents to register",
                        min_value=1,
                        max_value=100,
                        value=4
                    )

                    agent_id_prefix = st.text_input(
                        "Agent ID prefix",
                        value="agent",
                        help="Agents will be named: prefix_0, prefix_1, etc."
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

                            # Register agents in grid
                            for i in range(num_agents):
                                agent_id = f"{agent_id_prefix}_{i}"
                                row = i // grid_cols
                                col = i % grid_cols

                                x = (col + 1) * x_spacing
                                y = (row + 1) * y_spacing

                                db_manager.register_actor(conn, agent_id, x, y, "N")

                            st.success(f"✅ Registered {num_agents} agents in grid layout")
                            st.rerun()

                        except Exception as e:
                            st.error(f"Error registering agents: {e}")

                conn.close()

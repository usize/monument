"""
GIF exporter for Monument simulations.
Reads a data.json from export_sim output and produces a replay GIF.
"""

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from PIL import Image, ImageDraw, ImageFont


def load_font(size: int) -> ImageFont.ImageFont:
    """
    Try a handful of system fonts for decent readability; fall back to default.
    """
    font_candidates = [
        "Inter-Regular.ttf",
        "Inter-Regular.otf",
        "SFNSDisplay.ttf",
        "Helvetica.ttc",
        "Arial.ttf",
        "DejaVuSans.ttf",
    ]
    for name in font_candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


TITLE_FONT = load_font(26)
SECTION_FONT = load_font(18)
BODY_FONT = load_font(15)
SMALL_FONT = load_font(13)

CANVAS_WIDTH = 1280
CANVAS_HEIGHT = 720
BACKGROUND_COLOR = (11, 13, 18)
PANEL_COLOR = (23, 27, 38)
PANEL_DARK = (31, 36, 50)
TEXT_COLOR = (234, 237, 243)
MUTED_TEXT = (156, 167, 190)
ACCENT_COLOR = (255, 193, 94)
MAX_ACTIONS_DISPLAY = 5
MAX_CHAT_LINES = 5


def normalize_color(color: str) -> str:
    if not color:
        return "#FFFFFF"
    color = color.strip()
    if not color.startswith("#"):
        return "#FFFFFF"
    hex_part = color[1:]
    if len(hex_part) == 3:
        return "#" + "".join(ch * 2 for ch in hex_part)
    if len(hex_part) == 6:
        return "#" + hex_part
    if len(hex_part) < 6:
        hex_part = (hex_part + "0" * 6)[:6]
        return "#" + hex_part
    return "#FFFFFF"


def load_data(data_path: Path) -> Dict[str, Any]:
    with data_path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def safe_text(text: str, limit: int = 100) -> str:
    if not text:
        return ""
    text = text.replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def layout_frame(
    state: Dict[str, Any],
    tick: Dict[str, Any],
    visible_actions: List[Dict[str, Any]] | None = None,
    width: int = CANVAS_WIDTH,
    height: int = CANVAS_HEIGHT,
) -> Image.Image:
    img = Image.new("RGB", (width, height), color=BACKGROUND_COLOR)
    draw = ImageDraw.Draw(img)

    padding = 26
    grid_panel_width = int(width * 0.52)
    grid_panel_height = int(height * 0.68)
    grid_panel_x = padding
    grid_panel_y = padding * 2.2

    # Grid background
    draw.rounded_rectangle(
        [grid_panel_x - 12, grid_panel_y - 12, grid_panel_x + grid_panel_width, grid_panel_y + grid_panel_height],
        radius=18,
        fill=PANEL_COLOR
    )

    world_w = state["width"]
    world_h = state["height"]
    inner_width = grid_panel_width - 24
    inner_height = grid_panel_height - 24
    cell_size = max(4, min(inner_width // world_w, inner_height // world_h))
    grid_origin_x = grid_panel_x + 12
    grid_origin_y = grid_panel_y + 12

    # Draw tiles
    for idx, color in enumerate(tick["tiles"]):
        x = idx % world_w
        y = idx // world_w
        cx = grid_origin_x + x * cell_size
        cy = grid_origin_y + y * cell_size
        draw.rectangle(
            [cx, cy, cx + cell_size - 1, cy + cell_size - 1],
            fill=normalize_color(color),
            outline=None
        )

    # Draw agents
    label_font = BODY_FONT if cell_size >= 12 else SMALL_FONT
    for actor_id, actor in tick["actors"].items():
        cx = grid_origin_x + actor["x"] * cell_size
        cy = grid_origin_y + actor["y"] * cell_size
        draw.rectangle(
            [cx, cy, cx + cell_size - 1, cy + cell_size - 1],
            fill=(8, 9, 12)
        )
        draw.text(
            (cx + 2, cy + 2),
            actor_id,
            fill=ACCENT_COLOR,
            font=label_font
        )

    # Actions panel: widen viewpoint for longer snippets
    actions_panel_x = grid_panel_x + grid_panel_width + padding
    actions_panel_width = width - actions_panel_x - padding
    actions_panel_top = grid_panel_y
    actions_panel_bottom = grid_panel_y + grid_panel_height - 8
    draw.rounded_rectangle(
        [actions_panel_x - 10, actions_panel_top - 12, actions_panel_x + actions_panel_width, actions_panel_bottom],
        radius=18,
        fill=PANEL_COLOR
    )
    draw.text(
        (actions_panel_x, actions_panel_top - 32),
        f"Tick {tick['id']}  ·  Goal: {safe_text(state.get('goal'), 80)}",
        fill=MUTED_TEXT,
        font=SMALL_FONT
    )
    draw.text((actions_panel_x, actions_panel_top - 8), "Recent Actions", fill=TEXT_COLOR, font=SECTION_FONT)

    if visible_actions is None:
        visible_actions = tick["actions"][-MAX_ACTIONS_DISPLAY:]

    card_y = actions_panel_top + 20
    card_height = 78
    for action in reversed(visible_actions):
        if card_y + card_height > actions_panel_bottom - 10:
            break
        params = action.get("params") or {}
        param_str = params.get("params", "")
        result = action.get("result") or {}
        outcome = result.get("outcome", "UNKNOWN")
        reason = result.get("reason", "")

        draw.rounded_rectangle(
            [actions_panel_x, card_y, actions_panel_x + actions_panel_width - 20, card_y + card_height],
            radius=12,
            fill=PANEL_DARK
        )
        text_x = actions_panel_x + 14
        draw.text(
            (text_x, card_y + 8),
            f"{action['actor_id']} • {action['action_type']} {param_str}",
            fill=TEXT_COLOR,
            font=BODY_FONT
        )
        outcome_line = outcome
        if reason:
            outcome_line += f" — {reason}"
        draw.text(
            (text_x, card_y + 32),
            outcome_line,
            fill=MUTED_TEXT,
            font=SMALL_FONT
        )
        snippet = safe_text(action.get("llm_output") or "", 80)
        if snippet:
            draw.text(
                (text_x, card_y + 50),
                f"“{snippet}”",
                fill=(184, 192, 211),
                font=SMALL_FONT
            )
        card_y += card_height + 10

    if not visible_actions:
        draw.text(
            (actions_panel_x, actions_panel_top + 30),
            "No actions yet",
            fill=MUTED_TEXT,
            font=BODY_FONT
        )

    # Chat panel (bottom)
    chat_panel_y = grid_panel_y + grid_panel_height + padding
    chat_panel_height = height - chat_panel_y - padding
    draw.rounded_rectangle(
        [padding, chat_panel_y - 10, width - padding, chat_panel_y + chat_panel_height],
        radius=18,
        fill=PANEL_COLOR
    )
    draw.text((padding + 16, chat_panel_y - 4), "Chat", fill=TEXT_COLOR, font=SECTION_FONT)
    chat_lines = list(reversed(tick["chats"][-MAX_CHAT_LINES:]))
    if chat_lines:
        line_y = chat_panel_y + 26
        for chat in chat_lines:
            msg = safe_text(chat.get("message", ""), 110)
            draw.text(
                (padding + 24, line_y),
                f"[tick {chat.get('tick')}] {chat.get('from_id')}: {msg}",
                fill=MUTED_TEXT,
                font=BODY_FONT
            )
            line_y += 24
    else:
        draw.text(
            (padding + 24, chat_panel_y + 26),
            "No chat messages yet",
            fill=MUTED_TEXT,
            font=BODY_FONT
        )

    return img


def build_frames(data_path: Path) -> List[Image.Image]:
    data = load_data(data_path)
    width = int(data["meta"].get("width", 64))
    height = int(data["meta"].get("height", 64))
    base_color = data["meta"].get("default_color", "#FFFFFF")

    ticks = sorted(data["ticks"], key=lambda t: t["supertick_id"])
    tiles = [base_color] * (width * height)
    actors = {}
    actions = []
    chats = []
    frames = []

    state = {
        "namespace": data["namespace"],
        "width": width,
        "height": height,
        "goal": data["meta"].get("goal", "None"),
    }

    if not ticks:
        frames.append(layout_frame(state, {"id": 0, "tiles": tiles[:], "actors": {}, "actions": [], "chats": []}))
        return frames

    for tick in ticks:
        tick_id = tick["supertick_id"]
        for update in tick.get("tile_updates", []):
            x = update["x"]
            y = update["y"]
            idx = y * width + x
            tiles[idx] = update.get("new_color", base_color)

        for pos in tick.get("actor_positions", []):
            actors[pos["actor_id"]] = pos

        for action in tick.get("actions", []):
            actions.append(action)

        for chat in tick.get("chat", []):
            chat_entry = dict(chat)
            if "tick" not in chat_entry:
                chat_entry["tick"] = tick_id
            chats.append(chat_entry)

        tick_snapshot = {
            "id": tick_id,
            "tiles": tiles[:],
            "actors": {k: dict(v) for k, v in actors.items()},
            "actions": actions[:],
            "chats": chats[:],
        }

        action_count = len(actions)
        if action_count == 0:
            frames.append(layout_frame(state, tick_snapshot))
        else:
            chunks = []
            start = action_count
            while start > 0:
                end = start
                start = max(0, start - MAX_ACTIONS_DISPLAY)
                chunks.append(actions[start:end])

            for chunk in chunks:
                frames.append(layout_frame(state, tick_snapshot, visible_actions=chunk))

    return frames


def export_gif(data_path: Path, output_path: Path) -> None:
    frames = build_frames(data_path)
    frames[0].save(
        output_path,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        loop=0,
        duration=1000,
        disposal=2,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a replay GIF from export_sim data.json")
    parser.add_argument(
        "data",
        help="Path to data.json produced by export_sim (e.g., exports/<ns>/data.json)",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Output GIF path (default: same folder as data.json, named replay.gif)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    data_path = Path(args.data)
    if not data_path.exists():
        raise SystemExit(f"data.json not found: {data_path}")
    output_path = Path(args.output) if args.output else data_path.with_name("replay.gif")
    export_gif(data_path, output_path)
    print(f"Saved replay GIF to {output_path}")


if __name__ == "__main__":
    main()

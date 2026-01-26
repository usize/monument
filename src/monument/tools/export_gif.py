"""
GIF exporter for Monument simulations.
Reads a data.json from export_sim output and produces a replay GIF.
"""

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from PIL import Image, ImageDraw, ImageFont

BASE_FONT = ImageFont.load_default()
TITLE_FONT = ImageFont.load_default()


def load_data(data_path: Path) -> Dict[str, Any]:
    with data_path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def safe_text(text: str, limit: int = 60) -> str:
    if not text:
        return ""
    text = text.replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def layout_frame(state: Dict[str, Any], tick: Dict[str, Any], width: int = 1024, height: int = 576) -> Image.Image:
    img = Image.new("RGB", (width, height), color=(15, 17, 21))
    draw = ImageDraw.Draw(img)

    padding = 20
    text_color = (228, 231, 236)
    accent = (255, 178, 62)

    # Title
    draw.text((padding, padding), f"Monument Replay — {state['namespace']} | Tick {tick['id']}", fill=text_color, font=TITLE_FONT)

    # Grid area
    grid_width = int(width * 0.5)
    grid_height = int(height * 0.7)
    grid_x = padding
    grid_y = padding * 3

    world_w = state["width"]
    world_h = state["height"]
    cell_size = min((grid_width - padding * 2) // world_w, (grid_height - padding * 2) // world_h)
    if cell_size < 4:
        cell_size = 4

    # Draw tiles
    for idx, color in enumerate(tick["tiles"]):
        x = idx % world_w
        y = idx // world_w
        cx = grid_x + x * cell_size
        cy = grid_y + y * cell_size
        draw.rectangle(
            [cx, cy, cx + cell_size - 1, cy + cell_size - 1],
            fill=color or "#111111",
            outline=None
        )

    # Draw agents
    for actor_id, actor in tick["actors"].items():
        cx = grid_x + actor["x"] * cell_size
        cy = grid_y + actor["y"] * cell_size
        draw.rectangle(
            [cx, cy, cx + cell_size - 1, cy + cell_size - 1],
            fill=(0, 0, 0, 120)
        )
        draw.text(
            (cx + 1, cy + 1),
            actor_id,
            fill=accent,
            font=BASE_FONT
        )

    # Actions panel
    panel_x = grid_x + grid_width + padding
    panel_y = grid_y
    panel_width = width - panel_x - padding
    draw.text((panel_x, panel_y), "Recent actions", fill=text_color, font=TITLE_FONT)
    panel_y += 20
    actions = tick["actions"][-5:]
    if actions:
        for action in reversed(actions):
            params = action.get("params") or {}
            param_str = params.get("params", "")
            result = action.get("result") or {}
            outcome = result.get("outcome", "UNKNOWN")
            reason = result.get("reason", "")
            draw.text(
                (panel_x, panel_y),
                f"{action['actor_id']}: {action['action_type']} {param_str} → {outcome}",
                fill=text_color,
                font=BASE_FONT
            )
            panel_y += 16
            truncated = safe_text(action.get("llm_output") or "", 80)
            if truncated:
                draw.text(
                    (panel_x + 16, panel_y),
                    f"“{truncated}”",
                    fill=(158, 167, 191),
                    font=BASE_FONT
                )
                panel_y += 16
            if reason:
                draw.text(
                    (panel_x + 16, panel_y),
                    reason,
                    fill=(140, 150, 170),
                    font=BASE_FONT
                )
                panel_y += 16
            panel_y += 8
    else:
        draw.text((panel_x, panel_y), "No actions yet", fill=(140, 150, 170), font=BASE_FONT)
        panel_y += 16

    # Chat panel (bottom)
    chat_y = grid_y + grid_height + padding
    draw.text((grid_x, chat_y), "Chat", fill=text_color, font=TITLE_FONT)
    chat_y += 20
    chats = tick["chats"][-3:]
    if chats:
        for chat in chats:
            draw.text(
                (grid_x, chat_y),
                f"[tick {chat['tick']}] {chat['from_id']}: {safe_text(chat['message'], 90)}",
                fill=(158, 167, 191),
                font=BASE_FONT
            )
            chat_y += 16
    else:
        draw.text((grid_x, chat_y), "No chat messages yet", fill=(140, 150, 170), font=BASE_FONT)

    # Goal
    goal_text = safe_text(state.get("goal"), 120)
    draw.text(
        (panel_x, chat_y),
        f"Goal: {goal_text}",
        fill=text_color,
        font=BASE_FONT
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
        frames.append(
            layout_frame(
                state,
                {
                    "id": 0,
                    "tiles": tiles[:],
                    "actors": {},
                    "actions": [],
                    "chats": [],
                },
            )
        )
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

        frames.append(
            layout_frame(
                state,
                {
                    "id": tick_id,
                    "tiles": tiles[:],
                    "actors": {k: dict(v) for k, v in actors.items()},
                    "actions": actions[:],
                    "chats": chats[:],
                },
            )
        )

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

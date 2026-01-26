"""
Simulation exporter for Monument.
Produces a static bundle (data.json + index.html) from a namespace DB.
"""

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

from monument.server.db import db_manager


def _safe_json_load(value: str) -> Any:
    if value is None or value == "":
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def collect_ticks(conn) -> Dict[str, Dict[int, List[Dict[str, Any]]]]:
    buckets: Dict[str, Dict[int, List[Dict[str, Any]]]] = {
        "actions": defaultdict(list),
        "tile_updates": defaultdict(list),
        "actor_positions": defaultdict(list),
        "chat": defaultdict(list),
        "scoring": defaultdict(list),
    }

    # Actions + LLM context
    cursor = conn.execute(
        """
        SELECT supertick_id, actor_id, action_type, params_json, result_json,
               context_hash, llm_input, llm_output, created_at
        FROM audit
        ORDER BY supertick_id ASC, id ASC
        """
    )
    for row in cursor.fetchall():
        tick = row["supertick_id"]
        buckets["actions"][tick].append(
            {
                "actor_id": row["actor_id"],
                "action_type": row["action_type"],
                "params": _safe_json_load(row["params_json"]),
                "result": _safe_json_load(row["result_json"]),
                "context_hash": row["context_hash"],
                "llm_input": _safe_json_load(row["llm_input"]),
                "llm_output": row["llm_output"],
                "created_at": row["created_at"],
            }
        )

    # Tile updates
    cursor = conn.execute(
        """
        SELECT supertick_id, x, y, old_color, new_color, actor_id, action_type, created_at
        FROM tile_history
        ORDER BY supertick_id ASC, id ASC
        """
    )
    for row in cursor.fetchall():
        tick = row["supertick_id"]
        buckets["tile_updates"][tick].append(
            {
                "x": row["x"],
                "y": row["y"],
                "old_color": row["old_color"],
                "new_color": row["new_color"],
                "actor_id": row["actor_id"],
                "action_type": row["action_type"],
                "created_at": row["created_at"],
            }
        )

    # Actor positions
    cursor = conn.execute(
        """
        SELECT supertick_id, actor_id, x, y, facing, created_at
        FROM actor_history
        ORDER BY supertick_id ASC, id ASC
        """
    )
    for row in cursor.fetchall():
        tick = row["supertick_id"]
        buckets["actor_positions"][tick].append(
            {
                "actor_id": row["actor_id"],
                "x": row["x"],
                "y": row["y"],
                "facing": row["facing"],
                "created_at": row["created_at"],
            }
        )

    # Chat
    cursor = conn.execute(
        """
        SELECT supertick_id, from_id, message, created_at
        FROM chat
        ORDER BY supertick_id ASC, id ASC
        """
    )
    for row in cursor.fetchall():
        tick = row["supertick_id"]
        buckets["chat"][tick].append(
            {
                "from_id": row["from_id"],
                "message": row["message"],
                "created_at": row["created_at"],
            }
        )

    # Scoring/adjudication
    cursor = conn.execute(
        """
        SELECT supertick_id, selected_tiles_json, contributions_json, rationale, feedback, created_at
        FROM scoring_rounds
        ORDER BY supertick_id ASC, id ASC
        """
    )
    for row in cursor.fetchall():
        tick = row["supertick_id"]
        buckets["scoring"][tick].append(
            {
                "selected_tiles": _safe_json_load(row["selected_tiles_json"]),
                "contributions": _safe_json_load(row["contributions_json"]),
                "rationale": row["rationale"],
                "feedback": row["feedback"],
                "created_at": row["created_at"],
            }
        )

    return buckets


def build_export_payload(namespace: str) -> Dict[str, Any]:
    conn = db_manager.get_connection(namespace)
    meta = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM meta")}

    # Agents
    agents = []
    cursor = conn.execute(
        """
        SELECT id, x, y, facing, scopes, custom_instructions, llm_model, eliminated_at
        FROM actors
        ORDER BY id ASC
        """
    )
    for row in cursor.fetchall():
        agents.append(
            {
                "id": row["id"],
                "position": {"x": row["x"], "y": row["y"], "facing": row["facing"]},
                "scopes": json.loads(row["scopes"]),
                "custom_instructions": row["custom_instructions"],
                "llm_model": row["llm_model"],
                "eliminated_at": row["eliminated_at"],
            }
        )

    # Gather per-tick buckets
    buckets = collect_ticks(conn)
    conn.close()

    all_ticks = set()
    for cat in buckets.values():
        all_ticks.update(cat.keys())

    max_tick = meta.get("supertick_id")
    if max_tick is not None:
        try:
            all_ticks.add(int(max_tick))
        except ValueError:
            pass

    tick_ids = sorted(all_ticks)
    ticks_payload = []
    for tick_id in tick_ids:
        ticks_payload.append(
            {
                "supertick_id": tick_id,
                "actions": buckets["actions"].get(tick_id, []),
                "tile_updates": buckets["tile_updates"].get(tick_id, []),
                "actor_positions": buckets["actor_positions"].get(tick_id, []),
                "chat": buckets["chat"].get(tick_id, []),
                "scoring": buckets["scoring"].get(tick_id, []),
            }
        )

    return {
        "namespace": namespace,
        "generated_at": int(time.time()),
        "meta": meta,
        "agents": agents,
        "ticks": ticks_payload,
    }


VIEWER_HTML = """<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Monument Replay</title>
    <style>
      :root {
        color-scheme: light dark;
        font-family: "Inter", system-ui, sans-serif;
      }
      body {
        margin: 0;
        padding: 1rem;
        background: #0f1115;
        color: #e4e7ec;
        display: flex;
        flex-direction: column;
        gap: 1rem;
      }
      h1, h2, h3 {
        margin: 0 0 0.5rem 0;
      }
      .summary {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 1rem;
        margin-bottom: 1rem;
      }
      .summary span {
        background: #1b1f29;
        padding: 0.35rem 0.75rem;
        border-radius: 0.5rem;
        font-size: 0.9rem;
      }
      .viewer {
        display: grid;
        grid-template-columns: 1fr;
        gap: 1rem;
      }
      .world-container {
        background: #11131a;
        border-radius: 0.75rem;
        padding: 1rem;
        box-shadow: 0 0 25px rgba(0,0,0,0.3);
      }
      canvas {
        border: 1px solid #2a2f3a;
        border-radius: 0.5rem;
        background: #050607;
        width: min(100%, 650px);
        height: auto;
        display: block;
        margin: 0 auto;
      }
      .controls {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 0.75rem;
        margin-top: 0.75rem;
      }
      .controls button {
        background: #2d3344;
        color: inherit;
        border: none;
        padding: 0.35rem 0.65rem;
        border-radius: 0.45rem;
        cursor: pointer;
      }
      .controls .auto-play {
        margin-left: auto;
        display: flex;
        align-items: center;
        gap: 0.35rem;
        font-size: 0.85rem;
      }
      .zoom-control {
        display: flex;
        align-items: center;
        gap: 0.35rem;
        font-size: 0.85rem;
      }
      .controls button:disabled {
        opacity: 0.4;
        cursor: not-allowed;
      }
      .controls input[type="range"] {
        flex: 1;
      }
      .panel {
        background: #11131a;
        border-radius: 0.75rem;
        padding: 1rem;
      }
      .panel h3 {
        margin-bottom: 0.75rem;
      }
      .log-entry {
        border-bottom: 1px solid #1f2533;
        padding: 0.4rem 0;
      }
      .log-entry:last-child {
        border-bottom: none;
      }
      .log-entry.action-entry {
        cursor: pointer;
      }
      .log-entry.action-entry:hover {
        background: rgba(255,255,255,0.02);
      }
      .log-entry span.meta {
        color: #9ea7bf;
        font-size: 0.85rem;
        margin-right: 0.5rem;
      }
      .agents-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 1rem;
      }
      .agent-card {
        border: 1px solid #1f2533;
        border-radius: 0.65rem;
        padding: 0.75rem;
        background: #0c0f16;
        font-size: 0.8rem;
      }
      .agent-card h4 {
        margin: 0 0 0.3rem 0;
        font-size: 0.95rem;
      }
      .agent-card p {
        font-size: 0.75rem;
      }
      .mono {
        font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
        font-size: 0.9rem;
      }
      .grid-meta {
        display: flex;
        justify-content: space-between;
        margin-top: 0.35rem;
        font-size: 0.85rem;
        color: #9ea7bf;
      }
      pre {
        background: rgba(255,255,255,0.04);
        padding: 0.5rem;
        border-radius: 0.5rem;
        overflow-x: auto;
        white-space: pre-wrap;
      }
    </style>
  </head>
  <body>
    <div class="viewer">
      <div class="summary" id="summary">
        <h1>Monument Replay</h1>
      </div>
      <div class="panel">
        <h3>Cumulative Chat</h3>
        <div id="chatLog"></div>
      </div>

      <div class="panel">
        <h3>Agent Details</h3>
        <div class="agents-grid" id="agentsGrid"></div>
      </div>

      <div class="world-container">
        <h2 id="tickLabel">Loading…</h2>
        <canvas id="worldCanvas"></canvas>
        <div class="grid-meta">
          <span id="gridSize"></span>
          <span id="agentCount"></span>
        </div>
        <div class="controls">
          <button id="prevBtn">Prev</button>
          <input type="range" id="tickSlider" min="0" max="0" value="0" />
          <button id="nextBtn">Next</button>
          <div class="zoom-control">
            <label for="zoomSlider">Zoom</label>
            <input type="range" id="zoomSlider" min="0.5" max="2" step="0.1" value="1" />
            <span id="zoomValue">1.0x</span>
          </div>
          <div class="auto-play">
            <label for="autoPlayToggle">Auto-play</label>
            <input type="checkbox" id="autoPlayToggle" />
          </div>
        </div>
      </div>

      <div class="panel">
        <h3>Actions (cumulative)</h3>
        <p class="meta">Click any action to inspect its LLM prompt/response.</p>
        <div id="actionsLog"></div>
      </div>

    </div>

    <script>
      const BASE_DIMENSION = 600;
      function preprocess(data) {
        const width = parseInt(data.meta.width || "64", 10);
        const height = parseInt(data.meta.height || "64", 10);
        const sortedTicks = [...data.ticks].sort((a, b) => a.supertick_id - b.supertick_id);
        const baseColor = data.meta.default_color || "#FFFFFF";
        const tiles = new Array(width * height).fill(baseColor);
        const actors = {};
        const actions = [];
        const chats = [];
        const processed = [];

        if (sortedTicks.length === 0) {
          processed.push({
            id: 0,
            tiles: tiles.slice(),
            actors: JSON.parse(JSON.stringify(actors)),
            actions: [],
            chats: [],
          });
          return { width, height, ticks: processed, agents: data.agents, namespace: data.namespace };
        }

        for (const tick of sortedTicks) {
          (tick.tile_updates || []).forEach(update => {
            if (typeof update.x === "number" && typeof update.y === "number") {
              const idx = update.y * width + update.x;
              tiles[idx] = update.new_color || baseColor;
            }
          });

          (tick.actor_positions || []).forEach(pos => {
            actors[pos.actor_id] = { ...pos };
          });

          (tick.actions || []).forEach(action => {
            actions.push({
              tick: tick.supertick_id,
              actor_id: action.actor_id,
              action_type: action.action_type,
              params: action.params,
              result: action.result,
              llm_input: action.llm_input,
              llm_output: action.llm_output,
            });
          });

          (tick.chat || []).forEach(message => {
            chats.push({
              tick: tick.supertick_id,
              from_id: message.from_id,
              message: message.message,
              created_at: message.created_at,
            });
          });

          processed.push({
            id: tick.supertick_id,
            tiles: tiles.slice(),
            actors: JSON.parse(JSON.stringify(actors)),
            actions: actions.slice(),
            chats: chats.slice(),
          });
        }

        return {
          width,
          height,
          ticks: processed,
          agents: data.agents,
          namespace: data.namespace,
          goal: data.meta.goal || "None",
        };
      }

      function drawWorld(canvas, state, tickInfo, zoomFactor) {
        const { width, height } = tickInfo;
        const ctx = canvas.getContext("2d");
        const maxDim = BASE_DIMENSION * zoomFactor;
        const scale = Math.max(3, Math.floor(maxDim / Math.max(width, height)));
        canvas.width = width * scale;
        canvas.height = height * scale;

        // Draw tiles
        state.tiles.forEach((color, idx) => {
          const x = idx % width;
          const y = Math.floor(idx / width);
          ctx.fillStyle = color || "#1a1a1a";
          ctx.fillRect(x * scale, y * scale, scale, scale);
        });

        // Draw agents
        ctx.font = `${Math.max(9, Math.floor(scale * 0.7))}px monospace`;
        ctx.textBaseline = "top";
        ctx.lineWidth = 2;
        Object.entries(state.actors).forEach(([actorId, actor]) => {
          const x = actor.x * scale;
          const y = actor.y * scale;
          ctx.fillStyle = "rgba(0, 0, 0, 0.4)";
          ctx.fillRect(x, y, scale, scale);
          ctx.fillStyle = "#ffdb6d";
          ctx.fillText(actorId, x + 2, y + 2);
        });
      }

      function renderActions(container, actions) {
        container.innerHTML = "";
        if (!actions.length) {
          container.textContent = "No actions yet.";
          return;
        }
        [...actions].reverse().slice(0, 200).forEach(entry => {
          const div = document.createElement("div");
          div.className = "log-entry action-entry";
          const params = entry.params && entry.params.params ? entry.params.params : "";
          const outcome = entry.result && entry.result.outcome ? entry.result.outcome : "UNKNOWN";
          const reason = entry.result && entry.result.reason ? entry.result.reason : "";
          div.innerHTML = `<span class="meta">[tick ${entry.tick}]</span><strong>${entry.actor_id}</strong>: ${entry.action_type} ${params} → <em>${outcome}</em> ${reason ? " – " + reason : ""}`;
          if (entry.llm_input || entry.llm_output) {
            const details = document.createElement("div");
            details.style.display = "none";
            details.style.marginTop = "0.35rem";
            details.style.paddingLeft = "0.5rem";
            details.style.borderLeft = "2px solid #252b3b";
            const llmInput = entry.llm_input ? JSON.stringify(entry.llm_input, null, 2) : "N/A";
            const llmOutput = entry.llm_output || "N/A";
            details.innerHTML = `
              <div><strong>LLM Input:</strong></div>
              <pre class="mono">${llmInput}</pre>
              <div><strong>LLM Output:</strong></div>
              <pre class="mono">${llmOutput}</pre>
            `;
            div.appendChild(details);
            div.addEventListener("click", () => {
              details.style.display = details.style.display === "none" ? "block" : "none";
            });
          }
          container.appendChild(div);
        });
      }

      function renderChats(container, chats) {
        container.innerHTML = "";
        if (!chats.length) {
          container.textContent = "No chat messages yet.";
          return;
        }
        chats.slice(-200).forEach(entry => {
          const div = document.createElement("div");
            div.className = "log-entry chat-entry";
          div.innerHTML = `<span class="meta">[tick ${entry.tick}]</span><strong>${entry.from_id}</strong>: ${entry.message}`;
          container.appendChild(div);
        });
      }

      function renderAgents(grid, agents) {
        grid.innerHTML = "";
        if (!agents.length) {
          grid.textContent = "No agents registered.";
          return;
        }
        agents.forEach(agent => {
          const card = document.createElement("div");
          card.className = "agent-card";
          const instructions = agent.custom_instructions || "No instructions.";
          card.innerHTML = `
            <h4>${agent.id}</h4>
            <div class="mono">Scopes: ${agent.scopes.join(", ")}</div>
            <div class="mono">LLM: ${agent.llm_model || "default"}</div>
            <p>${instructions.replace(/\\n/g, "<br />")}</p>
          `;
          grid.appendChild(card);
        });
      }

      function initViewer(data) {
        const state = preprocess(data);
        const summary = document.getElementById("summary");
        summary.innerHTML = `
          <h1>Monument Replay</h1>
          <span>Namespace: ${state.namespace}</span>
          <span>Total ticks: ${state.ticks.length}</span>
          <span>Agents: ${state.agents.length}</span>
          <span>Goal: ${state.goal}</span>
        `;

        const canvas = document.getElementById("worldCanvas");
        const actionsLog = document.getElementById("actionsLog");
        const chatLog = document.getElementById("chatLog");
        const agentsGrid = document.getElementById("agentsGrid");
        const tickLabel = document.getElementById("tickLabel");
        const gridSize = document.getElementById("gridSize");
        const agentCount = document.getElementById("agentCount");
        const slider = document.getElementById("tickSlider");
        const prevBtn = document.getElementById("prevBtn");
        const nextBtn = document.getElementById("nextBtn");
        const zoomSlider = document.getElementById("zoomSlider");
        const zoomValue = document.getElementById("zoomValue");
        const autoPlayToggle = document.getElementById("autoPlayToggle");

        slider.max = Math.max(0, state.ticks.length - 1);
        slider.value = 0;
        gridSize.textContent = `${state.width}×${state.height}`;
        agentCount.textContent = `${state.agents.length} agents`;
        let currentZoom = 1;
        let currentIndex = 0;
        let autoPlayInterval = null;

        function render(index) {
          const tickState = state.ticks[index];
          currentIndex = index;
          tickLabel.textContent = `Tick ${tickState.id} (${index + 1}/${state.ticks.length})`;
          drawWorld(canvas, { tiles: tickState.tiles, actors: tickState.actors }, state, currentZoom);
          renderActions(actionsLog, tickState.actions);
          renderChats(chatLog, tickState.chats);
          slider.value = index;
          prevBtn.disabled = index === 0;
          nextBtn.disabled = index === state.ticks.length - 1;
        }

        slider.addEventListener("input", (e) => render(Number(e.target.value)));
        prevBtn.addEventListener("click", () => {
          const value = Math.max(0, Number(slider.value) - 1);
          render(value);
        });
        nextBtn.addEventListener("click", () => {
          const value = Math.min(state.ticks.length - 1, Number(slider.value) + 1);
          render(value);
        });
        zoomSlider.addEventListener("input", (e) => {
          currentZoom = Number(e.target.value);
          zoomValue.textContent = `${currentZoom.toFixed(1)}x`;
          render(currentIndex);
        });
        autoPlayToggle.addEventListener("change", (e) => {
          if (e.target.checked) {
            if (autoPlayInterval) {
              clearInterval(autoPlayInterval);
            }
            autoPlayInterval = setInterval(() => {
              if (currentIndex < state.ticks.length - 1) {
                render(currentIndex + 1);
              } else {
                clearInterval(autoPlayInterval);
                autoPlayToggle.checked = false;
              }
            }, 1200);
          } else if (autoPlayInterval) {
            clearInterval(autoPlayInterval);
            autoPlayInterval = null;
          }
        });

        render(currentIndex);
        renderAgents(agentsGrid, state.agents);
      }

      fetch("data.json")
        .then(resp => resp.json())
        .then(initViewer)
        .catch(error => {
          document.body.innerHTML = `<p>Failed to load data.json: ${error}</p>`;
        });
    </script>
  </body>
</html>
"""


def export_namespace(namespace: str, output_dir: Path) -> None:
    data = build_export_payload(namespace)
    output_dir.mkdir(parents=True, exist_ok=True)

    data_path = output_dir / "data.json"
    data_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    index_path = output_dir / "index.html"
    index_path.write_text(VIEWER_HTML, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export a Monument namespace to a static bundle.")
    parser.add_argument("namespace", help="Namespace to export")
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Output directory (default: exports/<namespace>)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    namespace = args.namespace
    output_dir = Path(args.output) if args.output else Path("exports") / namespace
    db_manager.validate_namespace(namespace)
    export_namespace(namespace, output_dir)
    print(f"Exported namespace '{namespace}' to {output_dir}")


if __name__ == "__main__":
    main()

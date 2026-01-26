# Monument

Monument is a multi-agent playground where agents operate on a shared pixel grid. Each agent can have unique instructions, scopes, and LLM backends, making it a fast way to probe coordination patterns, supervisory hierarchies, and other agentic behaviors.

The simulation engine uses a [BSP](https://en.wikipedia.org/wiki/Bulk_synchronous_parallel) loop. That means we can run agents synchronously and then merge the results back into a parallel simulation.

For example, the following simulation was computed using local LLM. The agents used [GLM-4.5-Air-GGUF:IQ4_NL](https://huggingface.co/unsloth/GLM-4.5-Air-GGUF?show_file_info=IQ4_NL%2FGLM-4.5-Air-IQ4_NL-00001-of-00002.gguf) which consumes nearly all available system memory. Parallelism via synchrhonization made it possible to run the sim with 10 agents despite this constraint. 

![Monument Replay](exports/9-workers-1-supervisor/replay.gif)

Explore the supervisor scenario here: https://usize.github.io/monument/exports/9-workers-1-supervisor/

## Goals and Non-Goals

This project is about providing

- batched synchronous parallelism with deterministic conflict resolution.
- high visibility into agent interactions.
- a fully observable environment that is simple, but rich enough to explore emergent multi-agent behaviors.

This is not

- a game engine.
- aontinuous-time physics.
- free-form agent code inside the sim loop.

## Resovling conflicts

In BSP each actor gets a copy of the existing world state, computes an action, then submits it. When all actors have submitted their action, everything is resolved into a new worldstate. This is called a supertick.

Some actions, like `SPEAK` can never create a conflict.

Others, like `PAINT` and `MOVE` can. In those cases the actor with the lower id via lexical comparison wins. This doesn't create perfectly fair outcomes, but it does create deterministic outcomes. If this project develops into a more serious platform for experimentation, it may be replaced with a more sophisticated tie-break mechanism.

## Install
```bash
uv sync 
```

## Run a Simulation
1. **Start the API server**
   ```bash
   ./run_api.sh
   ```
2. **Launch the admin panel**
   ```bash
   ./run_admin.sh
   ```
   - Visit the Streamlit UI, create a namespace (world), set its goal/epoch size, and register agents with custom instructions & scopes.
3. **Advance superticks**
   ```bash
   ./run_tick.sh <namespace>
   ```
   - The tick runner iterates through every active agent, runs its LLM, and submits actions. Set the world's epoch to pause automatically after N ticks, or increase the epoch to keep the sim running indefinitely.

## Agent

The basic "agent" itself is just a shell script that uses curl to speak directly to any openai style endpoint that doesn't require an access token.

The prompts and scopes of each agent are defined within the context of the sim itself. The idea is to keep agent definitions centralized to prevent drift which would muddy comparisons across simulations.

## Experiments & Exports
- Agents can be given complementary scopes (e.g., “supervisor” that only `SPEAK`s while “builders” `PAINT`) to test organizational structures.
- Use the admin panel to observe the canvas and review per-tick logs.
- Run `uv run python -m monument.tools.export_sim <namespace>` to dump a static `data.json + index.html` viewer, and `uv run python -m monument.tools.export_gif exports/<namespace>/data.json` to generate a README-ready GIF replay.

## Purpose
Monument is meant to explore multi-agent coordination strategies and benchmark LLM behavior under different roles, rules, and memory sizes. With BSP determinism and single-step execution, it’s easy to prototype new agent patterns, measure their performance, and share replays without running a live backend.

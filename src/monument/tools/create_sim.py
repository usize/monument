"""
YAML-based simulation creator for Monument.
Reads a YAML configuration file and creates a simulation database.
"""

import argparse
import json
import math
import random
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

from monument.server.db import db_manager

# Valid scopes for agents
VALID_SCOPES = {"MOVE", "PAINT", "SPEAK", "WAIT", "SKIP", "SUPERVISOR"}

# Valid facing directions
VALID_FACING = {"N", "S", "E", "W"}


class ConfigError(Exception):
    """Invalid configuration error."""
    pass


def validate_namespace(namespace: str) -> None:
    """Validate namespace format."""
    pattern = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
    if not pattern.match(namespace):
        raise ConfigError(
            f"Invalid namespace '{namespace}'. "
            f"Must match pattern: ^[a-zA-Z0-9][a-zA-Z0-9_-]{{0,63}}$"
        )


def validate_world_config(world: Dict[str, Any]) -> None:
    """Validate world configuration."""
    width = world.get("width", 64)
    height = world.get("height", 64)

    if not isinstance(width, int) or width < 8 or width > 256:
        raise ConfigError(f"World width must be an integer between 8 and 256, got {width}")

    if not isinstance(height, int) or height < 8 or height > 256:
        raise ConfigError(f"World height must be an integer between 8 and 256, got {height}")

    epoch = world.get("epoch", 10)
    if not isinstance(epoch, int) or epoch < 1:
        raise ConfigError(f"Epoch must be a positive integer, got {epoch}")


def parse_position(
    position: Any,
    width: int,
    height: int,
    occupied: set
) -> Tuple[int, int]:
    """
    Parse position specification.

    Args:
        position: Can be "center", "random", or {"x": int, "y": int}
        width: World width
        height: World height
        occupied: Set of already occupied positions

    Returns:
        (x, y) tuple
    """
    if position == "center":
        x = width // 2
        y = height // 2
        # If center is occupied, find nearest free spot
        if (x, y) in occupied:
            x, y = find_free_position(x, y, width, height, occupied)
        return (x, y)

    elif position == "random":
        return random_free_position(width, height, occupied)

    elif isinstance(position, dict):
        x = position.get("x")
        y = position.get("y")

        if x is None or y is None:
            raise ConfigError(f"Position dict must have 'x' and 'y' keys, got {position}")

        if not isinstance(x, int) or not isinstance(y, int):
            raise ConfigError(f"Position x and y must be integers, got x={x}, y={y}")

        if x < 0 or x >= width or y < 0 or y >= height:
            raise ConfigError(f"Position ({x}, {y}) is out of bounds for {width}x{height} world")

        return (x, y)

    else:
        raise ConfigError(f"Invalid position format: {position}. Use 'center', 'random', or {{x: int, y: int}}")


def random_free_position(width: int, height: int, occupied: set) -> Tuple[int, int]:
    """Find a random unoccupied position."""
    max_attempts = width * height
    for _ in range(max_attempts):
        x = random.randint(0, width - 1)
        y = random.randint(0, height - 1)
        if (x, y) not in occupied:
            return (x, y)

    # If all random attempts fail, find any free position
    for x in range(width):
        for y in range(height):
            if (x, y) not in occupied:
                return (x, y)

    raise ConfigError("No free positions available in the world")


def find_free_position(
    start_x: int,
    start_y: int,
    width: int,
    height: int,
    occupied: set
) -> Tuple[int, int]:
    """Find nearest free position using spiral search."""
    for radius in range(1, max(width, height)):
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                if abs(dx) != radius and abs(dy) != radius:
                    continue  # Only check perimeter
                x = start_x + dx
                y = start_y + dy
                if 0 <= x < width and 0 <= y < height and (x, y) not in occupied:
                    return (x, y)

    raise ConfigError("No free positions available near the requested location")


def calculate_grid_positions(
    count: int,
    width: int,
    height: int,
    occupied: set
) -> List[Tuple[int, int]]:
    """
    Calculate grid positions for bulk agent placement.
    Same algorithm as admin UI.
    """
    positions = []

    grid_cols = math.ceil(math.sqrt(count))
    grid_rows = math.ceil(count / grid_cols)

    x_spacing = width / (grid_cols + 1)
    y_spacing = height / (grid_rows + 1)

    for i in range(count):
        row = i // grid_cols
        col = i % grid_cols

        x = min(width - 1, max(0, int(round((col + 1) * x_spacing) - 1)))
        y = min(height - 1, max(0, int(round((row + 1) * y_spacing) - 1)))

        # If position is occupied, find nearest free spot
        if (x, y) in occupied:
            x, y = find_free_position(x, y, width, height, occupied)

        positions.append((x, y))
        occupied.add((x, y))

    return positions


def validate_scopes(scopes: List[str], agent_id: str) -> None:
    """Validate that all scopes are valid."""
    for scope in scopes:
        if scope not in VALID_SCOPES:
            raise ConfigError(
                f"Invalid scope '{scope}' for agent '{agent_id}'. "
                f"Valid scopes: {', '.join(sorted(VALID_SCOPES))}"
            )


def validate_facing(facing: str, agent_id: str) -> None:
    """Validate facing direction."""
    if facing not in VALID_FACING:
        raise ConfigError(
            f"Invalid facing '{facing}' for agent '{agent_id}'. "
            f"Valid directions: {', '.join(sorted(VALID_FACING))}"
        )


def process_individual_agent(
    agent_config: Dict[str, Any],
    width: int,
    height: int,
    occupied: set
) -> Dict[str, Any]:
    """Process an individual agent definition."""
    agent_id = agent_config.get("id")
    if not agent_id:
        raise ConfigError("Individual agent definition must have an 'id' field")

    # Parse position
    position = agent_config.get("position", "random")
    x, y = parse_position(position, width, height, occupied)
    occupied.add((x, y))

    # Validate facing
    facing = agent_config.get("facing", "N")
    validate_facing(facing, agent_id)

    # Validate scopes
    scopes = agent_config.get("scopes", ["MOVE", "PAINT", "SPEAK", "WAIT", "SKIP"])
    validate_scopes(scopes, agent_id)

    # Get optional fields
    instructions = agent_config.get("instructions", "")
    llm_model = agent_config.get("llm_model", "")
    llm_base_url = agent_config.get("llm_base_url", "")
    llm_api_key = agent_config.get("llm_api_key", "")
    secret = agent_config.get("secret")  # Can be pre-specified

    return {
        "id": agent_id,
        "x": x,
        "y": y,
        "facing": facing,
        "scopes": scopes,
        "instructions": instructions.strip() if isinstance(instructions, str) else "",
        "llm_model": llm_model,
        "llm_base_url": llm_base_url,
        "llm_api_key": llm_api_key,
        "secret": secret,
    }


def process_bulk_agents(
    agent_config: Dict[str, Any],
    width: int,
    height: int,
    occupied: set
) -> List[Dict[str, Any]]:
    """Process a bulk agent definition."""
    prefix = agent_config.get("prefix")
    count = agent_config.get("count")

    if not prefix:
        raise ConfigError("Bulk agent definition must have a 'prefix' field")
    if not count or not isinstance(count, int) or count < 1:
        raise ConfigError(f"Bulk agent 'count' must be a positive integer, got {count}")

    # Validate facing
    facing = agent_config.get("facing", "N")
    validate_facing(facing, f"{prefix}_*")

    # Validate scopes
    scopes = agent_config.get("scopes", ["MOVE", "PAINT", "SPEAK", "WAIT", "SKIP"])
    validate_scopes(scopes, f"{prefix}_*")

    # Get layout type
    layout = agent_config.get("layout", "grid")
    if layout not in ("grid", "random"):
        raise ConfigError(f"Invalid layout '{layout}'. Use 'grid' or 'random'")

    # Calculate positions
    if layout == "grid":
        positions = calculate_grid_positions(count, width, height, occupied)
    else:  # random
        positions = []
        for _ in range(count):
            pos = random_free_position(width, height, occupied)
            positions.append(pos)
            occupied.add(pos)

    # Get optional fields
    instructions = agent_config.get("instructions", "")
    llm_model = agent_config.get("llm_model", "")
    llm_base_url = agent_config.get("llm_base_url", "")
    llm_api_key = agent_config.get("llm_api_key", "")

    # Create agent entries
    agents = []
    for i, (x, y) in enumerate(positions):
        agents.append({
            "id": f"{prefix}_{i}",
            "x": x,
            "y": y,
            "facing": facing,
            "scopes": scopes,
            "instructions": instructions.strip() if isinstance(instructions, str) else "",
            "llm_model": llm_model,
            "llm_base_url": llm_base_url,
            "llm_api_key": llm_api_key,
            "secret": None,  # Bulk agents always get auto-generated secrets
        })

    return agents


def load_and_validate_config(config_path: Path) -> Dict[str, Any]:
    """Load and validate YAML configuration."""
    if not config_path.exists():
        raise ConfigError(f"Configuration file not found: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if not config:
        raise ConfigError("Configuration file is empty")

    # Validate required fields
    if "namespace" not in config:
        raise ConfigError("Configuration must have a 'namespace' field")

    validate_namespace(config["namespace"])

    # Validate world configuration
    world = config.get("world", {})
    validate_world_config(world)

    # Validate agents exist
    agents = config.get("agents", [])
    if not agents:
        raise ConfigError("Configuration must have at least one agent in the 'agents' list")

    return config


def process_agents(
    agents_config: List[Dict[str, Any]],
    width: int,
    height: int
) -> List[Dict[str, Any]]:
    """Process all agent configurations."""
    processed_agents = []
    occupied = set()

    for agent_config in agents_config:
        if "id" in agent_config:
            # Individual agent
            agent = process_individual_agent(agent_config, width, height, occupied)
            processed_agents.append(agent)
        elif "prefix" in agent_config:
            # Bulk agents
            agents = process_bulk_agents(agent_config, width, height, occupied)
            processed_agents.extend(agents)
        else:
            raise ConfigError(
                "Agent definition must have either 'id' (individual) or 'prefix' (bulk) field"
            )

    return processed_agents


def create_simulation(
    config: Dict[str, Any],
    force: bool = False
) -> Dict[str, str]:
    """
    Create a simulation from configuration.

    Returns:
        Dict mapping agent_id -> secret
    """
    namespace = config["namespace"]
    world = config.get("world", {})

    # Check if namespace already exists
    db_path = db_manager.get_db_path(namespace)
    if db_path.exists():
        if force:
            db_path.unlink()
        else:
            raise ConfigError(
                f"Namespace '{namespace}' already exists. Use --force to overwrite."
            )

    # Extract world parameters
    width = world.get("width", 64)
    height = world.get("height", 64)
    goal = world.get("goal", "")
    epoch = world.get("epoch", 10)

    # Process agents
    agents_config = config.get("agents", [])
    agents = process_agents(agents_config, width, height)

    # Create database and initialize world
    conn = db_manager.get_connection(namespace)
    db_manager.init_world(conn, width, height, goal, epoch)

    # Register all agents
    secrets = {}
    for agent in agents:
        secret = db_manager.register_actor(
            conn,
            actor_id=agent["id"],
            x=agent["x"],
            y=agent["y"],
            facing=agent["facing"],
            scopes=agent["scopes"],
            secret=agent["secret"],
            custom_instructions=agent["instructions"],
            llm_model=agent["llm_model"],
            llm_base_url=agent["llm_base_url"],
            llm_api_key=agent["llm_api_key"],
        )
        secrets[agent["id"]] = secret

    conn.close()

    return secrets


def format_secrets_output(secrets: Dict[str, str]) -> str:
    """Format secrets for stdout output."""
    lines = []
    for agent_id, secret in sorted(secrets.items()):
        lines.append(f"{agent_id}: {secret}")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a Monument simulation from a YAML configuration file."
    )
    parser.add_argument(
        "config",
        help="Path to YAML configuration file"
    )
    parser.add_argument(
        "--secrets-file",
        "-s",
        default=None,
        help="Write secrets to this JSON file instead of stdout"
    )
    parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Overwrite existing namespace if it exists"
    )
    return parser.parse_args()


def main():
    args = parse_args()
    config_path = Path(args.config)

    try:
        # Load and validate configuration
        config = load_and_validate_config(config_path)

        # Create simulation
        secrets = create_simulation(config, force=args.force)

        namespace = config["namespace"]
        world = config.get("world", {})
        width = world.get("width", 64)
        height = world.get("height", 64)

        print(f"Created simulation '{namespace}' ({width}x{height}) with {len(secrets)} agents")

        # Output secrets
        if args.secrets_file:
            secrets_path = Path(args.secrets_file)
            secrets_path.write_text(json.dumps(secrets, indent=2), encoding="utf-8")
            print(f"Secrets written to: {secrets_path}")
        else:
            print("\nAgent secrets:")
            print(format_secrets_output(secrets))

    except ConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)
    except db_manager.NamespaceError as e:
        print(f"Namespace error: {e}", file=sys.stderr)
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"YAML parsing error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

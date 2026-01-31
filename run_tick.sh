#!/bin/bash
# BSP Tick Runner - Execute superticks for all agents in a namespace
# Queries agents from DB, runs agent.py for each, handles retries

set -e

# ============================================================================
# Configuration
# ============================================================================
MAX_RETRIES="${MAX_RETRIES:-3}"
RETRY_DELAY="${RETRY_DELAY:-2}"
DATA_DIR="${MONUMENT_DATA_DIR:-$(dirname "$0")/data/sims}"

# ============================================================================
# Usage
# ============================================================================
usage() {
    cat << EOF
BSP Tick Runner - Execute superticks for all agents

Usage:
  $0 <namespace> [options]

Options:
  -e, --epoch             Run until epoch is reached (not just one tick)
  -r, --retries <n>       Max retries per agent (default: $MAX_RETRIES)
  -d, --delay <s>         Delay between retries in seconds (default: $RETRY_DELAY)
  --data-dir <path>       Path to data/sims directory
  -v, --verbose           Verbose output (passed to agent.py)
  -h, --help              Show this help

Environment Variables:
  MAX_RETRIES             Max retries per agent
  RETRY_DELAY             Delay between retries (seconds)
  MONUMENT_DATA_DIR       Path to data/sims directory
  LLM_API_URL             LLM API URL (passed to agent.py)
  LLM_MODEL               LLM model name (passed to agent.py)
  MONUMENT_API_URL        Monument API URL (passed to agent.py)

Examples:
  $0 my-world                    # Run one tick
  $0 my-world --epoch            # Run until epoch reached
  $0 my-world -e -r 5 -d 3 -v    # Run epoch with custom retries

EOF
    exit 1
}

# ============================================================================
# Parse Arguments
# ============================================================================
VERBOSE=""
NAMESPACE=""
RUN_EPOCH=false

while [[ $# -gt 0 ]]; do
    case $1 in
        -e|--epoch) RUN_EPOCH=true; shift ;;
        -r|--retries) MAX_RETRIES="$2"; shift 2 ;;
        -d|--delay) RETRY_DELAY="$2"; shift 2 ;;
        --data-dir) DATA_DIR="$2"; shift 2 ;;
        -v|--verbose) VERBOSE="-v"; shift ;;
        -h|--help) usage ;;
        -*) echo "Unknown option: $1"; usage ;;
        *) NAMESPACE="$1"; shift ;;
    esac
done

if [[ -z "$NAMESPACE" ]]; then
    echo "Error: namespace is required"
    usage
fi

# ============================================================================
# Helpers
# ============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() {
    echo "[runner] $*"
}

# ============================================================================
# Functions
# ============================================================================

get_meta() {
    local key="$1"
    sqlite3 "$DB_PATH" "SELECT value FROM meta WHERE key='$key';"
}

run_single_tick() {
    local current_tick="$1"

    log "--- Tick $current_tick ---"

    # Query agents
    AGENTS=$(sqlite3 "$DB_PATH" "SELECT id, secret FROM actors WHERE eliminated_at IS NULL ORDER BY id;")

    if [[ -z "$AGENTS" ]]; then
        log "No active agents found"
        return 0
    fi

    # Count agents
    AGENT_COUNT=$(echo "$AGENTS" | wc -l | tr -d ' ')
    log "Processing $AGENT_COUNT agent(s)..."

    # Track results for this tick
    local tick_success=0
    local tick_skip=0
    local tick_fail=0

    # Process each agent
    while IFS='|' read -r AGENT_ID SECRET; do
        # Skip agents that already submitted for this tick
        SUBMITTED=$(sqlite3 "$DB_PATH" "SELECT 1 FROM journal WHERE supertick_id = $current_tick AND actor_id = '$AGENT_ID' LIMIT 1;")
        if [[ -n "$SUBMITTED" ]]; then
            log "  ⏭️  $AGENT_ID: Already submitted, skipping"
            tick_skip=$((tick_skip + 1))
            continue
        fi

        ATTEMPT=0
        AGENT_DONE=false

        while [[ $ATTEMPT -lt $MAX_RETRIES ]] && [[ "$AGENT_DONE" == "false" ]]; do
            ATTEMPT=$((ATTEMPT + 1))

            if [[ $ATTEMPT -gt 1 ]]; then
                log "  ⏳ $AGENT_ID: Retry $ATTEMPT/$MAX_RETRIES (waiting ${RETRY_DELAY}s)..."
                sleep "$RETRY_DELAY"
            fi

            # Run agent.py
            set +e
            python3 "$SCRIPT_DIR/src/monument/agent/agent.py" "$NAMESPACE" "$AGENT_ID" "$SECRET" $VERBOSE
            EXIT_CODE=$?
            set -e

            case $EXIT_CODE in
                0)  # Success
                    log "  ✅ $AGENT_ID: Action submitted"
                    tick_success=$((tick_success + 1))
                    AGENT_DONE=true
                    ;;
                1)  # Transient failure - retry
                    log "  ⚠️  $AGENT_ID: Transient failure (attempt $ATTEMPT/$MAX_RETRIES)"
                    ;;
                2)  # Already submitted - skip
                    log "  ⏭️  $AGENT_ID: Already submitted"
                    tick_skip=$((tick_skip + 1))
                    AGENT_DONE=true
                    ;;
                3)  # Permanent failure - bail
                    log "  ❌ $AGENT_ID: Permanent failure"
                    tick_fail=$((tick_fail + 1))
                    AGENT_DONE=true
                    ;;
                *)  # Unknown - treat as transient
                    log "  ⚠️  $AGENT_ID: Unknown exit code $EXIT_CODE (attempt $ATTEMPT/$MAX_RETRIES)"
                    ;;
            esac
        done

        # Check if we exhausted retries
        if [[ "$AGENT_DONE" == "false" ]]; then
            log "  ❌ $AGENT_ID: Exhausted retries"
            tick_fail=$((tick_fail + 1))
        fi

    done <<< "$AGENTS"

    # Update global counters
    SUCCESS_COUNT=$((SUCCESS_COUNT + tick_success))
    SKIP_COUNT=$((SKIP_COUNT + tick_skip))
    FAIL_COUNT=$((FAIL_COUNT + tick_fail))

    log "Tick $current_tick: ✅ $tick_success  ⏭️ $tick_skip  ❌ $tick_fail"

    # Return failure if any agent failed
    if [[ $tick_fail -gt 0 ]]; then
        return 1
    fi
    return 0
}

# ============================================================================
# Main
# ============================================================================

DB_PATH="$DATA_DIR/${NAMESPACE}.db"

if [[ ! -f "$DB_PATH" ]]; then
    echo "Error: Database not found: $DB_PATH" >&2
    echo "Make sure the namespace exists and DATA_DIR is correct." >&2
    exit 1
fi

# Global counters
SUCCESS_COUNT=0
SKIP_COUNT=0
FAIL_COUNT=0
TICK_COUNT=0

if [[ "$RUN_EPOCH" == "true" ]]; then
    log "=== Running Epoch for namespace: $NAMESPACE ==="
else
    log "=== Running Single Tick for namespace: $NAMESPACE ==="
fi

log "Database: $DB_PATH"
log "Max retries: $MAX_RETRIES, Retry delay: ${RETRY_DELAY}s"

# Get initial state
CURRENT_TICK=$(get_meta "supertick_id")
EPOCH=$(get_meta "epoch")
PHASE=$(get_meta "phase")

if [[ -z "$CURRENT_TICK" ]]; then
    echo "Error: Could not read current supertick from meta table" >&2
    exit 1
fi

log "Starting at tick $CURRENT_TICK, epoch $EPOCH, phase $PHASE"

if [[ "$PHASE" == "PAUSED" ]]; then
    log "⚠️  Simulation is PAUSED. Set new epoch via admin UI or update meta table."
    exit 0
fi

# Main loop
while true; do
    # Refresh current tick (may have advanced after previous tick)
    CURRENT_TICK=$(get_meta "supertick_id")
    EPOCH=$(get_meta "epoch")
    PHASE=$(get_meta "phase")

    # Check if we've reached the epoch
    if [[ "$CURRENT_TICK" -ge "$EPOCH" ]]; then
        log "Reached epoch limit ($EPOCH)"
        break
    fi

    # Check if paused
    if [[ "$PHASE" == "PAUSED" ]]; then
        log "Simulation paused at tick $CURRENT_TICK"
        break
    fi

    # Run one tick
    echo ""
    set +e
    run_single_tick "$CURRENT_TICK"
    TICK_RESULT=$?
    set -e

    TICK_COUNT=$((TICK_COUNT + 1))

    # If not running epoch mode, exit after one tick
    if [[ "$RUN_EPOCH" != "true" ]]; then
        break
    fi

    # Small delay between ticks to avoid hammering
    sleep 0.5
done

echo ""
log "============================================="
log "=== Run Complete ==="
log "Ticks executed: $TICK_COUNT"
log "Total: ✅ $SUCCESS_COUNT  ⏭️ $SKIP_COUNT  ❌ $FAIL_COUNT"

FINAL_TICK=$(get_meta "supertick_id")
FINAL_PHASE=$(get_meta "phase")
log "Final state: tick $FINAL_TICK, phase $FINAL_PHASE"
log "============================================="

if [[ $FAIL_COUNT -gt 0 ]]; then
    exit 1
fi
exit 0

#!/bin/bash
# BSP Tick Runner - Execute one full supertick for all agents in a namespace
# Queries agents from DB, runs agent.sh for each, handles retries

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
BSP Tick Runner - Execute one supertick for all agents

Usage:
  $0 <namespace> [options]

Options:
  -r, --retries <n>     Max retries per agent (default: $MAX_RETRIES)
  -d, --delay <s>       Delay between retries in seconds (default: $RETRY_DELAY)
  --data-dir <path>     Path to data/sims directory
  -v, --verbose         Verbose output (passed to agent.sh)
  -h, --help            Show this help

Environment Variables:
  MAX_RETRIES           Max retries per agent
  RETRY_DELAY           Delay between retries (seconds)
  MONUMENT_DATA_DIR     Path to data/sims directory
  LLM_API_URL           LLM API URL (passed to agent.sh)
  LLM_MODEL             LLM model name (passed to agent.sh)
  MONUMENT_API_URL      Monument API URL (passed to agent.sh)

Examples:
  $0 my-world
  $0 my-world -r 5 -d 3 -v
  MAX_RETRIES=10 $0 my-world

EOF
    exit 1
}

# ============================================================================
# Parse Arguments
# ============================================================================
VERBOSE=""
NAMESPACE=""

while [[ $# -gt 0 ]]; do
    case $1 in
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
# Main
# ============================================================================

DB_PATH="$DATA_DIR/${NAMESPACE}.db"

if [[ ! -f "$DB_PATH" ]]; then
    echo "Error: Database not found: $DB_PATH" >&2
    echo "Make sure the namespace exists and DATA_DIR is correct." >&2
    exit 1
fi

log "=== Starting Tick for namespace: $NAMESPACE ==="
log "Database: $DB_PATH"
log "Max retries: $MAX_RETRIES, Retry delay: ${RETRY_DELAY}s"

# Query agents and current supertick
CURRENT_TICK=$(sqlite3 "$DB_PATH" "SELECT value FROM meta WHERE key='supertick_id';")
if [[ -z "$CURRENT_TICK" ]]; then
    echo "Error: Could not read current supertick from meta table" >&2
    exit 1
fi

AGENTS=$(sqlite3 "$DB_PATH" "SELECT id, secret FROM actors WHERE eliminated_at IS NULL ORDER BY id;")

if [[ -z "$AGENTS" ]]; then
    log "No active agents found in namespace $NAMESPACE"
    exit 0
fi

# Count agents
AGENT_COUNT=$(echo "$AGENTS" | wc -l | tr -d ' ')
log "Found $AGENT_COUNT agent(s)"

# Track results
SUCCESS_COUNT=0
SKIP_COUNT=0
FAIL_COUNT=0

# Process each agent
while IFS='|' read -r AGENT_ID SECRET; do
    # Skip agents that already submitted for this tick
    SUBMITTED=$(sqlite3 "$DB_PATH" "SELECT 1 FROM journal WHERE supertick_id = $CURRENT_TICK AND actor_id = '$AGENT_ID' LIMIT 1;")
    if [[ -n "$SUBMITTED" ]]; then
        log "⏭️  $AGENT_ID: Already submitted for supertick $CURRENT_TICK, skipping"
        SKIP_COUNT=$((SKIP_COUNT + 1))
        continue
    fi

    log "--- Processing agent: $AGENT_ID ---"

    ATTEMPT=0
    AGENT_DONE=false

    while [[ $ATTEMPT -lt $MAX_RETRIES ]] && [[ "$AGENT_DONE" == "false" ]]; do
        ATTEMPT=$((ATTEMPT + 1))

        if [[ $ATTEMPT -gt 1 ]]; then
            log "Retry $ATTEMPT/$MAX_RETRIES for $AGENT_ID (waiting ${RETRY_DELAY}s)..."
            sleep "$RETRY_DELAY"
        fi

        # Run agent.sh
        set +e
        "$SCRIPT_DIR/src/monument/agent/agent.sh" "$NAMESPACE" "$AGENT_ID" "$SECRET" $VERBOSE
        EXIT_CODE=$?
        set -e

        case $EXIT_CODE in
            0)  # Success
                log "✅ $AGENT_ID: Action submitted successfully"
                SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
                AGENT_DONE=true
                ;;
            1)  # Transient failure - retry
                log "⚠️  $AGENT_ID: Transient failure (attempt $ATTEMPT/$MAX_RETRIES)"
                ;;
            2)  # Already submitted - skip
                log "⏭️  $AGENT_ID: Already submitted, skipping"
                SKIP_COUNT=$((SKIP_COUNT + 1))
                AGENT_DONE=true
                ;;
            3)  # Permanent failure - bail
                log "❌ $AGENT_ID: Permanent failure, aborting tick"
                FAIL_COUNT=$((FAIL_COUNT + 1))
                echo ""
                log "=== Tick FAILED ==="
                log "Succeeded: $SUCCESS_COUNT, Skipped: $SKIP_COUNT, Failed: $FAIL_COUNT"
                exit 1
                ;;
            *)  # Unknown - treat as transient
                log "⚠️  $AGENT_ID: Unknown exit code $EXIT_CODE (attempt $ATTEMPT/$MAX_RETRIES)"
                ;;
        esac
    done

    # Check if we exhausted retries
    if [[ "$AGENT_DONE" == "false" ]]; then
        log "❌ $AGENT_ID: Exhausted retries, aborting tick"
        FAIL_COUNT=$((FAIL_COUNT + 1))
        echo ""
        log "=== Tick FAILED ==="
        log "Succeeded: $SUCCESS_COUNT, Skipped: $SKIP_COUNT, Failed: $FAIL_COUNT"
        exit 1
    fi

done <<< "$AGENTS"

echo ""
log "=== Tick COMPLETE ==="
log "Succeeded: $SUCCESS_COUNT, Skipped: $SKIP_COUNT, Failed: $FAIL_COUNT"
exit 0

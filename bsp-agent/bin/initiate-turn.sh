#!/bin/bash
# Process unread emails from approved contacts using bsp-agent AI assistant
# This script loops continuously until there are no more unread emails to process

set -e

# Maximum time to allow vibe to run (seconds)
VIBE_TIMEOUT="${VIBE_TIMEOUT:-300}"

# Change to script directory so paths work from anywhere
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Function to log with timestamp
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

log "=== BSP Simulation Turn Start ==="

# Load simulation turn instructions
INSTRUCTIONS_FILE="../instructions.md"
if [ ! -f "$INSTRUCTIONS_FILE" ]; then
    log "ERROR: instructions.md not found at $INSTRUCTIONS_FILE"
    exit 1
fi

INSTRUCTIONS=$(cat "$INSTRUCTIONS_FILE")
log "Loaded instructions from $INSTRUCTIONS_FILE"

TURN_CONTEXT=$(./utils/actions.sh context ${MONUMENT_NAMESPACE} ${MONUMENT_AGENT_NAME} ${MONUMENT_AGENT_SECRET}) 
log "Loaded agent context"

# Main processing loop
while true; do
    log "Starting turn"

        # Build prompt for bsp-agent
        prompt="$INSTRUCTIONS\n$TURN_CONTEXT"
        set +e
        vibe "$prompt" --auto-approve --max-turns 10 &
        vibe_pid=$!
        set -e

        start_time=$(date +%s)
        timed_out=false

        while kill -0 "$vibe_pid" 2>/dev/null; do
            now=$(date +%s)
            elapsed=$((now - start_time))
            if [ "$elapsed" -ge "$VIBE_TIMEOUT" ]; then
                timed_out=true
                log "  WARNING: vibe running longer than ${VIBE_TIMEOUT}s, terminating..."
                kill "$vibe_pid" 2>/dev/null || true
                sleep 5
                kill -9 "$vibe_pid" 2>/dev/null || true
                break
            fi
            sleep 1
        done

        set +e
        wait "$vibe_pid"
        status=$?
        set -e

    log "BSP Simulation Step Complete"
done

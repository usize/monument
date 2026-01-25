#!/bin/bash
# Signal that the agent has completed its turn
# Call this after successfully submitting your action

SENTINEL_FILE="${MONUMENT_SENTINEL_FILE:-/tmp/monument-turn-complete}"

echo "Turn complete - signaling done"
touch "$SENTINEL_FILE"

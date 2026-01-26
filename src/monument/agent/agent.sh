#!/bin/bash
# Simple BSP Agent - Single turn execution
# Fetches context, asks LLM for action, submits action, exits
#
# Exit codes:
#   0 = Success (action submitted)
#   1 = Transient failure (retry recommended)
#   2 = Already submitted (skip to next agent)
#   3 = Permanent failure (bail out)

set -e

# ============================================================================
# Configuration (env vars or defaults)
# ============================================================================
MONUMENT_API_URL="${MONUMENT_API_URL:-http://localhost:8000}"
LLM_API_URL="${LLM_API_URL:-http://localhost:8080/v1}"
LLM_MODEL="${LLM_MODEL:-unsloth/GLM-4.5-Air-GGUF:IQ4_NL}"
LLM_TEMPERATURE="${LLM_TEMPERATURE:-0.7}"
HISTORY_LENGTH="${MONUMENT_HISTORY_LENGTH:-20}"
CHAT_LENGTH="${MONUMENT_CHAT_LENGTH:-}"

# ============================================================================
# Usage
# ============================================================================
usage() {
    cat << EOF
BSP Agent - Simple single-turn agent

Usage:
  $0 <namespace> <agent-id> <secret>
  $0 -n <namespace> -a <agent-id> -s <secret> [options]

Options:
  -n, --namespace <ns>      Simulation namespace
  -a, --agent <id>          Agent ID
  -s, --secret <secret>     Agent secret
  --history-length <n>      Number of past actions (and chat messages) to include (default: 20)
  --chat-length <n>         Override number of chat messages (default: history length)
  -m, --model <model>       LLM model name (default: \$LLM_MODEL)
  -u, --llm-url <url>       LLM API URL (default: \$LLM_API_URL)
  --api-url <url>           Monument API URL (default: \$MONUMENT_API_URL)
  -v, --verbose             Verbose output
  -h, --help                Show this help

Environment Variables:
  MONUMENT_API_URL          Monument API URL (default: http://localhost:8000)
  LLM_API_URL               LLM API URL (default: http://localhost:8080/v1)
  LLM_MODEL                 LLM model name
  LLM_TEMPERATURE           LLM temperature (default: 0.7)
  MONUMENT_NAMESPACE        Simulation namespace
  MONUMENT_AGENT_NAME       Agent ID
  MONUMENT_AGENT_SECRET     Agent secret

Examples:
  $0 my-world agent_0 abc123
  $0 -n my-world -a agent_0 -s abc123 -m "devstral-small-latest"
  LLM_MODEL="gpt-4" $0 my-world agent_0 abc123

EOF
    exit 1
}

# ============================================================================
# Parse Arguments
# ============================================================================
VERBOSE=false
NAMESPACE="${MONUMENT_NAMESPACE:-}"
AGENT_ID="${MONUMENT_AGENT_NAME:-}"
SECRET="${MONUMENT_AGENT_SECRET:-}"

# Positional args shortcut
if [[ $# -ge 3 && ! "$1" =~ ^- ]]; then
    NAMESPACE="$1"
    AGENT_ID="$2"
    SECRET="$3"
    shift 3
fi

# Parse remaining options
while [[ $# -gt 0 ]]; do
    case $1 in
        -n|--namespace) NAMESPACE="$2"; shift 2 ;;
        -a|--agent) AGENT_ID="$2"; shift 2 ;;
        -s|--secret) SECRET="$2"; shift 2 ;;
        --history-length) HISTORY_LENGTH="$2"; shift 2 ;;
        --chat-length) CHAT_LENGTH="$2"; shift 2 ;;
        -m|--model) LLM_MODEL="$2"; shift 2 ;;
        -u|--llm-url) LLM_API_URL="$2"; shift 2 ;;
        --api-url) MONUMENT_API_URL="$2"; shift 2 ;;
        -v|--verbose) VERBOSE=true; shift ;;
        -h|--help) usage ;;
        *) echo "Unknown option: $1"; usage ;;
    esac
done

# Validate required args
if [[ -z "$NAMESPACE" || -z "$AGENT_ID" || -z "$SECRET" ]]; then
    echo "Error: namespace, agent-id, and secret are required"
    usage
fi

CHAT_LENGTH="${CHAT_LENGTH:-$HISTORY_LENGTH}"

# ============================================================================
# Helpers
# ============================================================================
log() {
    if [[ "$VERBOSE" == true ]]; then
        echo "[agent] $*" >&2
    fi
}

# Exit codes
EXIT_SUCCESS=0
EXIT_TRANSIENT=1
EXIT_ALREADY_SUBMITTED=2
EXIT_PERMANENT=3

error_transient() {
    echo "[error] $*" >&2
    exit $EXIT_TRANSIENT
}

error_permanent() {
    echo "[error] $*" >&2
    exit $EXIT_PERMANENT
}

# ============================================================================
# Main
# ============================================================================

log "Starting turn for $AGENT_ID in $NAMESPACE (history=$HISTORY_LENGTH, chat=$CHAT_LENGTH)"
log "LLM: $LLM_API_URL model=$LLM_MODEL"

# 1. Fetch context from Monument API
log "Fetching context..."
CONTEXT_URL="${MONUMENT_API_URL}/sim/${NAMESPACE}/agent/${AGENT_ID}/context?history_length=${HISTORY_LENGTH}&chat_length=${CHAT_LENGTH}"
CONTEXT_RESPONSE=$(curl -s -w "\n%{http_code}" -H "X-Agent-Secret: $SECRET" "$CONTEXT_URL")

CONTEXT_HTTP_CODE=$(echo "$CONTEXT_RESPONSE" | tail -1)
CONTEXT_BODY=$(echo "$CONTEXT_RESPONSE" | sed '$d')

if [[ "$CONTEXT_HTTP_CODE" == "401" ]]; then
    error_permanent "Authentication failed for agent $AGENT_ID"
elif [[ "$CONTEXT_HTTP_CODE" == "404" ]]; then
    error_permanent "Agent $AGENT_ID not found in namespace $NAMESPACE"
elif [[ "$CONTEXT_HTTP_CODE" != "200" ]]; then
    error_transient "Failed to fetch context (HTTP $CONTEXT_HTTP_CODE)"
fi

SUPERTICK=$(echo "$CONTEXT_BODY" | jq -r '.supertick_id')
CONTEXT_HASH=$(echo "$CONTEXT_BODY" | jq -r '.context_hash')
HUD=$(echo "$CONTEXT_BODY" | jq -r '.hud')

log "Supertick: $SUPERTICK, Hash: $CONTEXT_HASH"

# 2. Build prompt for LLM
SYSTEM_PROMPT="You are an agent in a BSP (Batched Synchronous Parallel) simulation. You must respond with exactly ONE action.

Available actions:
- MOVE N (move north)
- MOVE S (move south)
- MOVE E (move east)
- MOVE W (move west)
- PAINT #RRGGBB (paint your current tile with a hex color)
- SPEAK <message> (send a chat message)
- WAIT (do nothing)

IMPORTANT: Your response must contain your chosen action. State your reasoning briefly, then output your action on its own line starting with ACTION:"

USER_PROMPT="Here is your current context:

$HUD

Based on your identity, objectives, and the current world state, decide on your next action. Think briefly about your strategy, then output your action.

Format your response as:
<brief reasoning>
ACTION: <your action>"

# 3. Call LLM API
log "Calling LLM..."
LLM_PAYLOAD=$(jq -n \
    --arg model "$LLM_MODEL" \
    --arg system "$SYSTEM_PROMPT" \
    --arg user "$USER_PROMPT" \
    --argjson temp "$LLM_TEMPERATURE" \
    '{
        model: $model,
        temperature: $temp,
        max_tokens: 512,
        messages: [
            {role: "system", content: $system},
            {role: "user", content: $user}
        ]
    }')

LLM_RESPONSE=$(curl -s -X POST "${LLM_API_URL}/chat/completions" \
    -H "Content-Type: application/json" \
    -d "$LLM_PAYLOAD")

if [[ -z "$LLM_RESPONSE" ]]; then
    error_transient "Empty response from LLM API (is the server running?)"
fi

# 4. Extract action from response
LLM_CONTENT=$(echo "$LLM_RESPONSE" | jq -r '.choices[0].message.content // empty')

if [[ -z "$LLM_CONTENT" ]]; then
    error_transient "Empty content in LLM response"
fi

log "LLM response: $LLM_CONTENT"

# Parse ACTION: line (case insensitive, flexible whitespace)
ACTION=$(echo "$LLM_CONTENT" | grep -iE '^ACTION:' | head -1 | sed 's/^[Aa][Cc][Tt][Ii][Oo][Nn]:\s*//' | xargs)

# Fallback: try to find action pattern anywhere in response
if [[ -z "$ACTION" ]]; then
    ACTION=$(echo "$LLM_CONTENT" | grep -oE '(MOVE [NSEW]|PAINT #[0-9A-Fa-f]{6}|SPEAK .+|WAIT)' | head -1)
fi

if [[ -z "$ACTION" ]]; then
    echo "Warning: Could not parse action from LLM response, defaulting to WAIT" >&2
    echo "LLM said: $LLM_CONTENT" >&2
    ACTION="WAIT"
fi

log "Parsed action: $ACTION"

# 5. Submit action to Monument API (include LLM context for audit trail)
log "Submitting action..."

# Build the full LLM input for traceability
LLM_INPUT=$(jq -n \
    --arg system "$SYSTEM_PROMPT" \
    --arg user "$USER_PROMPT" \
    '{system_prompt: $system, user_prompt: $user}' | jq -c .)

ACTION_PAYLOAD=$(jq -n \
    --arg ns "$NAMESPACE" \
    --argjson tick "$SUPERTICK" \
    --arg hash "$CONTEXT_HASH" \
    --arg action "$ACTION" \
    --arg llm_input "$LLM_INPUT" \
    --arg llm_output "$LLM_CONTENT" \
    '{
        namespace: $ns,
        supertick_id: $tick,
        context_hash: $hash,
        action: $action,
        llm_input: $llm_input,
        llm_output: $llm_output
    }')

SUBMIT_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST \
    -H "Content-Type: application/json" \
    -H "X-Agent-Secret: $SECRET" \
    -d "$ACTION_PAYLOAD" \
    "${MONUMENT_API_URL}/sim/${NAMESPACE}/agent/${AGENT_ID}/action")

HTTP_CODE=$(echo "$SUBMIT_RESPONSE" | tail -1)
SUBMIT_BODY=$(echo "$SUBMIT_RESPONSE" | sed '$d')

if [[ "$HTTP_CODE" == "200" ]]; then
    echo "[$AGENT_ID] Action submitted: $ACTION"
    MESSAGE=$(echo "$SUBMIT_BODY" | jq -r '.message // empty')
    [[ -n "$MESSAGE" ]] && log "Server: $MESSAGE"
    exit $EXIT_SUCCESS
else
    DETAIL=$(echo "$SUBMIT_BODY" | jq -r '.detail // empty')
    echo "[$AGENT_ID] Action rejected (HTTP $HTTP_CODE): $ACTION" >&2
    [[ -n "$DETAIL" ]] && echo "  Reason: $DETAIL" >&2

    # Check for "already submitted" - this is not a real failure
    if [[ "$DETAIL" == *"already submitted"* ]]; then
        echo "[$AGENT_ID] Already submitted for this tick, skipping" >&2
        exit $EXIT_ALREADY_SUBMITTED
    fi

    # Auth failures are permanent
    if [[ "$HTTP_CODE" == "401" ]]; then
        exit $EXIT_PERMANENT
    fi

    # Permission/scope errors are permanent
    if [[ "$HTTP_CODE" == "403" ]]; then
        exit $EXIT_PERMANENT
    fi

    # Context hash mismatch means tick advanced - could retry but likely permanent for this tick
    if [[ "$DETAIL" == *"Context hash mismatch"* ]] || [[ "$DETAIL" == *"Supertick mismatch"* ]]; then
        exit $EXIT_PERMANENT
    fi

    # Other 400 errors might be transient (malformed action from LLM, etc)
    exit $EXIT_TRANSIENT
fi

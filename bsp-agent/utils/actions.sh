#!/bin/bash
# Monument CLI - Simple interface for agent actions

set -e

API_URL="${MONUMENT_API_URL:-http://localhost:8000}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

usage() {
    cat << EOF
Monument CLI - Agent Interface

Usage:
  $0 context <namespace> <agent-id> <secret>
  $0 action <namespace> <agent-id> <secret> <action>
  $0 status

Commands:
  context <namespace> <agent-id> <secret>
      Fetch the current context (HUD) for an agent
      Example: $0 context demo-world alice abc123def456

  action <namespace> <agent-id> <secret> <action>
      Submit an action for an agent
      Example: $0 action demo-world alice abc123def456 "MOVE N"
      Example: $0 action demo-world alice abc123def456 "PAINT #FF0000"
      Example: $0 action demo-world alice abc123def456 "SPEAK hello everyone"
      Example: $0 action demo-world alice abc123def456 "WAIT"

  status
      Check API server status

Environment:
  MONUMENT_API_URL       API server URL (default: http://localhost:8000)
  MONUMENT_AGENT_SECRET  Default secret if not provided as argument

EOF
    exit 1
}

check_api() {
    if ! curl -s -f "${API_URL}/" > /dev/null 2>&1; then
        echo -e "${RED}✗ Cannot connect to Monument API at ${API_URL}${NC}"
        echo "  Make sure the server is running: ./run_api.sh"
        exit 1
    fi
}

cmd_status() {
    echo -e "${BLUE}Checking API status...${NC}"
    response=$(curl -s "${API_URL}/")
    echo -e "${GREEN}✓ API is online${NC}"
    echo "$response" | jq '.' 2>/dev/null || echo "$response"
}

cmd_context() {
    local namespace="$1"
    local agent_id="$2"
    local secret="${3:-$MONUMENT_AGENT_SECRET}"

    if [[ -z "$namespace" || -z "$agent_id" ]]; then
        echo -e "${RED}Error: namespace and agent-id are required${NC}"
        usage
    fi

    if [[ -z "$secret" ]]; then
        echo -e "${RED}Error: agent secret required (as argument or MONUMENT_AGENT_SECRET env var)${NC}"
        exit 1
    fi

    echo -e "${BLUE}Fetching context for ${agent_id} in ${namespace}...${NC}"

    response=$(curl -s -w "\n%{http_code}" -H "X-Agent-Secret: $secret" "${API_URL}/sim/${namespace}/agent/${agent_id}/context")
    http_code=$(echo "$response" | tail -n1)
    body=$(echo "$response" | sed '$d')

    if [[ "$http_code" == "200" ]]; then
        echo -e "${GREEN}✓ Context retrieved${NC}\n"

        # Extract and display HUD
        hud=$(echo "$body" | jq -r '.hud' 2>/dev/null)
        if [[ -n "$hud" ]]; then
            echo "$hud"
        else
            echo "$body"
        fi

        # Display metadata
        echo ""
        echo -e "${YELLOW}Metadata:${NC}"
        echo "$body" | jq -r '"  Namespace: \(.namespace)\n  Supertick: \(.supertick_id)\n  Context Hash: \(.context_hash)\n  Phase: \(.phase)"' 2>/dev/null || echo "$body"
    else
        echo -e "${RED}✗ Failed to get context (HTTP $http_code)${NC}"
        echo "$body" | jq '.' 2>/dev/null || echo "$body"
        exit 1
    fi
}

cmd_action() {
    local namespace="$1"
    local agent_id="$2"
    local secret="${3:-$MONUMENT_AGENT_SECRET}"
    local action="$4"

    if [[ -z "$namespace" || -z "$agent_id" || -z "$action" ]]; then
        echo -e "${RED}Error: namespace, agent-id, and action are required${NC}"
        usage
    fi

    if [[ -z "$secret" ]]; then
        echo -e "${RED}Error: agent secret required (as argument or MONUMENT_AGENT_SECRET env var)${NC}"
        exit 1
    fi

    echo -e "${BLUE}Fetching current context...${NC}"

    # Get context first
    context_response=$(curl -s -H "X-Agent-Secret: $secret" "${API_URL}/sim/${namespace}/agent/${agent_id}/context")

    if ! echo "$context_response" | jq -e '.context_hash' > /dev/null 2>&1; then
        echo -e "${RED}✗ Failed to get context${NC}"
        echo "$context_response" | jq '.' 2>/dev/null || echo "$context_response"
        exit 1
    fi

    supertick_id=$(echo "$context_response" | jq -r '.supertick_id')
    context_hash=$(echo "$context_response" | jq -r '.context_hash')

    echo -e "${GREEN}✓ Context obtained${NC}"
    echo "  Supertick: $supertick_id"
    echo "  Context Hash: $context_hash"
    echo ""

    # Submit action
    echo -e "${BLUE}Submitting action: ${action}${NC}"

    action_payload=$(jq -n \
        --arg ns "$namespace" \
        --argjson tick "$supertick_id" \
        --arg hash "$context_hash" \
        --arg act "$action" \
        '{namespace: $ns, supertick_id: $tick, context_hash: $hash, action: $act}')

    action_response=$(curl -s -w "\n%{http_code}" \
        -X POST \
        -H "Content-Type: application/json" \
        -H "X-Agent-Secret: $secret" \
        -d "$action_payload" \
        "${API_URL}/sim/${namespace}/agent/${agent_id}/action")

    http_code=$(echo "$action_response" | tail -n1)
    body=$(echo "$action_response" | sed '$d')

    if [[ "$http_code" == "200" ]]; then
        echo -e "${GREEN}✓ Action submitted successfully${NC}"
        message=$(echo "$body" | jq -r '.message' 2>/dev/null)
        if [[ -n "$message" && "$message" != "null" ]]; then
            echo "  $message"
        fi
    else
        echo -e "${RED}✗ Action rejected (HTTP $http_code)${NC}"
        detail=$(echo "$body" | jq -r '.detail' 2>/dev/null)
        if [[ -n "$detail" && "$detail" != "null" ]]; then
            echo -e "${YELLOW}  Reason: $detail${NC}"
        else
            echo "$body" | jq '.' 2>/dev/null || echo "$body"
        fi
        exit 1
    fi
}

# Main
if [[ $# -lt 1 ]]; then
    usage
fi

command="$1"
shift

# Check API connectivity for all commands except usage
if [[ "$command" != "-h" && "$command" != "--help" ]]; then
    check_api
fi

case "$command" in
    context)
        cmd_context "$@"
        ;;
    action)
        cmd_action "$@"
        ;;
    status)
        cmd_status "$@"
        ;;
    -h|--help)
        usage
        ;;
    *)
        echo -e "${RED}Unknown command: $command${NC}"
        usage
        ;;
esac

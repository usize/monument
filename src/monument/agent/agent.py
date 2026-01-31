#!/usr/bin/env python3
"""
BSP Agent - Single turn execution
Fetches context, asks LLM for action, submits action, exits.

Exit codes:
  0 = Success (action submitted)
  1 = Transient failure (retry recommended)
  2 = Already submitted (skip to next agent)
  3 = Permanent failure (bail out)
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Optional, Tuple

# Exit codes
EXIT_SUCCESS = 0
EXIT_TRANSIENT = 1
EXIT_ALREADY_SUBMITTED = 2
EXIT_PERMANENT = 3


def log(message: str, verbose: bool) -> None:
    if verbose:
        print(f"[agent] {message}", file=sys.stderr)


def error_transient(message: str) -> None:
    print(f"[error] {message}", file=sys.stderr)
    sys.exit(EXIT_TRANSIENT)


def error_permanent(message: str) -> None:
    print(f"[error] {message}", file=sys.stderr)
    sys.exit(EXIT_PERMANENT)


def http_request(
    url: str,
    method: str = "GET",
    data: Optional[dict] = None,
    headers: Optional[dict] = None,
) -> Tuple[int, str]:
    """
    Make an HTTP request and return (status_code, response_body).
    """
    headers = headers or {}
    req_data = None

    if data is not None:
        req_data = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=req_data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = response.read().decode("utf-8")
            return response.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8") if e.fp else ""
        return e.code, body
    except urllib.error.URLError as e:
        raise ConnectionError(f"Connection failed: {e.reason}")


def fetch_context(
    api_url: str,
    namespace: str,
    agent_id: str,
    secret: str,
    history_length: int,
    chat_length: int,
) -> dict:
    """
    Fetch agent context from Monument API.
    """
    url = f"{api_url}/sim/{namespace}/agent/{agent_id}/context?history_length={history_length}&chat_length={chat_length}"
    headers = {"X-Agent-Secret": secret}

    status, body = http_request(url, headers=headers)

    if status == 401:
        error_permanent(f"Authentication failed for agent {agent_id}")
    elif status == 404:
        error_permanent(f"Agent {agent_id} not found in namespace {namespace}")
    elif status != 200:
        error_transient(f"Failed to fetch context (HTTP {status}): {body}")

    return json.loads(body)


def call_llm(
    llm_url: str,
    model: str,
    api_key: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
) -> str:
    """
    Call LLM API and return the response content.
    """
    url = f"{llm_url}/chat/completions"
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "temperature": temperature,
        "max_tokens": 512,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    status, body = http_request(url, method="POST", data=payload, headers=headers)

    if status != 200:
        raise RuntimeError(f"LLM API error (HTTP {status}): {body}")

    response = json.loads(body)
    content = response.get("choices", [{}])[0].get("message", {}).get("content", "")

    if not content:
        raise RuntimeError("Empty content in LLM response")

    return content


def parse_action(llm_content: str) -> Optional[str]:
    """
    Parse action from LLM response.
    Returns None if no valid action found.
    """
    # Try to find ACTION: line (case insensitive)
    match = re.search(r"^ACTION:\s*(.+)$", llm_content, re.MULTILINE | re.IGNORECASE)
    if match:
        return match.group(1).strip()

    # Fallback: try to find action pattern anywhere
    patterns = [
        r"(MOVE [NSEW])",
        r"(PAINT #[0-9A-Fa-f]{6})",
        r"(PAINT #[0-9A-Fa-f]{3})",
        r"(SPEAK .+)",
        r"(WAIT)",
        r"(SKIP)",
    ]

    for pattern in patterns:
        match = re.search(pattern, llm_content)
        if match:
            return match.group(1).strip()

    return None


def submit_action(
    api_url: str,
    namespace: str,
    agent_id: str,
    secret: str,
    supertick_id: int,
    context_hash: str,
    action: str,
    llm_input: dict,
    llm_output: str,
) -> Tuple[bool, str]:
    """
    Submit action to Monument API.
    Returns (success, message).
    """
    url = f"{api_url}/sim/{namespace}/agent/{agent_id}/action"
    headers = {"X-Agent-Secret": secret}

    payload = {
        "namespace": namespace,
        "supertick_id": supertick_id,
        "context_hash": context_hash,
        "action": action,
        "llm_input": json.dumps(llm_input),
        "llm_output": llm_output,
    }

    status, body = http_request(url, method="POST", data=payload, headers=headers)

    if status == 200:
        response = json.loads(body)
        return True, response.get("message", "")

    # Parse error detail
    try:
        detail = json.loads(body).get("detail", body)
    except json.JSONDecodeError:
        detail = body

    return False, f"HTTP {status}: {detail}"


def build_system_prompt() -> str:
    return """You are an agent in a BSP (Batched Synchronous Parallel) simulation. You must respond with exactly ONE action.

Available actions:
- MOVE N (move north)
- MOVE S (move south)
- MOVE E (move east)
- MOVE W (move west)
- PAINT #RRGGBB (paint your current tile with a hex color)
- SPEAK <message> (send a chat message)
- WAIT (do nothing)
- SKIP (explicitly skip this tick)

IMPORTANT: Your response must contain your chosen action. State your reasoning briefly, then output your action on its own line starting with ACTION:"""


def build_user_prompt(hud: str) -> str:
    return f"""Here is your current context:

{hud}

Based on your identity, objectives, and the current world state, decide on your next action. Think briefly about your strategy, then output your action.

Format your response as:
<brief reasoning>
ACTION: <your action>"""


def main():
    parser = argparse.ArgumentParser(
        description="BSP Agent - Simple single-turn agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Environment Variables:
  MONUMENT_API_URL          Monument API URL (default: http://localhost:8000)
  LLM_API_URL               LLM API URL (default: http://localhost:8080/v1)
  LLM_MODEL                 LLM model name
  LLM_API_KEY               LLM API key (for authenticated APIs)
  LLM_TEMPERATURE           LLM temperature (default: 0.7)
  MAX_LLM_RETRIES           Max retries for LLM calls (default: 3)
  LLM_RETRY_DELAY           Seconds between retries (default: 2)

Note: Per-agent LLM settings from the simulation override these defaults.

Examples:
  %(prog)s my-world agent_0 abc123
  %(prog)s -n my-world -a agent_0 -s abc123 -m gpt-4
""",
    )

    # Positional arguments (optional, for backwards compatibility)
    parser.add_argument("namespace_pos", nargs="?", help=argparse.SUPPRESS)
    parser.add_argument("agent_pos", nargs="?", help=argparse.SUPPRESS)
    parser.add_argument("secret_pos", nargs="?", help=argparse.SUPPRESS)

    # Named arguments
    parser.add_argument("-n", "--namespace", help="Simulation namespace")
    parser.add_argument("-a", "--agent", help="Agent ID")
    parser.add_argument("-s", "--secret", help="Agent secret")
    parser.add_argument(
        "--history-length",
        type=int,
        default=int(os.environ.get("MONUMENT_HISTORY_LENGTH", "20")),
        help="Number of past actions to include (default: 20)",
    )
    parser.add_argument(
        "--chat-length",
        type=int,
        default=None,
        help="Number of chat messages (default: history-length)",
    )
    parser.add_argument("-m", "--model", help="LLM model name")
    parser.add_argument("-u", "--llm-url", help="LLM API URL")
    parser.add_argument("-k", "--llm-api-key", help="LLM API key")
    parser.add_argument("--api-url", help="Monument API URL")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")

    args = parser.parse_args()

    # Resolve arguments (positional -> named -> env)
    namespace = args.namespace or args.namespace_pos or os.environ.get("MONUMENT_NAMESPACE")
    agent_id = args.agent or args.agent_pos or os.environ.get("MONUMENT_AGENT_NAME")
    secret = args.secret or args.secret_pos or os.environ.get("MONUMENT_AGENT_SECRET")

    if not all([namespace, agent_id, secret]):
        parser.error("namespace, agent-id, and secret are required")

    # Configuration with defaults
    api_url = args.api_url or os.environ.get("MONUMENT_API_URL", "http://localhost:8000")
    llm_url = args.llm_url or os.environ.get("LLM_API_URL", "http://localhost:8080/v1")
    llm_model = args.model or os.environ.get("LLM_MODEL", "unsloth/GLM-4.5-Air-GGUF:IQ4_NL")
    llm_api_key = args.llm_api_key or os.environ.get("LLM_API_KEY", "")
    llm_temperature = float(os.environ.get("LLM_TEMPERATURE", "0.7"))
    max_retries = int(os.environ.get("MAX_LLM_RETRIES", "3"))
    retry_delay = int(os.environ.get("LLM_RETRY_DELAY", "2"))
    history_length = args.history_length
    chat_length = args.chat_length or history_length
    verbose = args.verbose

    log(f"Starting turn for {agent_id} in {namespace}", verbose)
    log(f"LLM defaults: {llm_url} model={llm_model} api_key={'(set)' if llm_api_key else '(none)'}", verbose)

    # 1. Fetch context
    log("Fetching context...", verbose)
    try:
        context = fetch_context(api_url, namespace, agent_id, secret, history_length, chat_length)
    except ConnectionError as e:
        error_transient(str(e))

    supertick_id = context["supertick_id"]
    context_hash = context["context_hash"]
    hud = context["hud"]

    # Apply per-agent LLM overrides
    llm_config = context.get("llm_config") or {}
    if llm_config.get("model"):
        log(f"Using per-agent LLM model: {llm_config['model']}", verbose)
        llm_model = llm_config["model"]
    if llm_config.get("base_url"):
        log(f"Using per-agent LLM base URL: {llm_config['base_url']}", verbose)
        llm_url = llm_config["base_url"]
    if llm_config.get("api_key"):
        log("Using per-agent LLM API key", verbose)
        llm_api_key = llm_config["api_key"]

    log(f"Final LLM config: {llm_url} model={llm_model}", verbose)
    log(f"Supertick: {supertick_id}, Hash: {context_hash}", verbose)

    # 2. Build prompts
    system_prompt = build_system_prompt()
    user_prompt = build_user_prompt(hud)

    # 3. Call LLM with retry logic
    action = None
    llm_content = ""

    for attempt in range(1, max_retries + 1):
        log(f"Calling LLM (attempt {attempt}/{max_retries})...", verbose)

        try:
            llm_content = call_llm(
                llm_url, llm_model, llm_api_key, system_prompt, user_prompt, llm_temperature
            )
        except (ConnectionError, RuntimeError) as e:
            print(f"[attempt {attempt}] LLM error: {e}", file=sys.stderr)
            if attempt < max_retries:
                time.sleep(retry_delay)
                continue
            error_transient(f"LLM failed after {max_retries} attempts: {e}")

        log(f"LLM response ({len(llm_content)} chars): {llm_content[:200]}...", verbose)

        # 4. Parse action
        action = parse_action(llm_content)

        if action:
            log(f"Parsed action: {action}", verbose)
            break

        print(f"[attempt {attempt}] Could not parse action from LLM response", file=sys.stderr)
        print(f"LLM said: {llm_content}", file=sys.stderr)

        if attempt < max_retries:
            print(f"Retrying in {retry_delay}s...", file=sys.stderr)
            time.sleep(retry_delay)

    if not action:
        error_transient(f"Could not parse valid action from LLM after {max_retries} attempts")

    # 5. Submit action
    log("Submitting action...", verbose)

    llm_input = {"system_prompt": system_prompt, "user_prompt": user_prompt}

    success, message = submit_action(
        api_url,
        namespace,
        agent_id,
        secret,
        supertick_id,
        context_hash,
        action,
        llm_input,
        llm_content,
    )

    if success:
        print(f"[{agent_id}] Action submitted: {action}")
        log(f"Server: {message}", verbose)
        sys.exit(EXIT_SUCCESS)

    # Handle submission failure
    print(f"[{agent_id}] Action rejected: {action}", file=sys.stderr)
    print(f"  Reason: {message}", file=sys.stderr)

    if "already submitted" in message.lower():
        print(f"[{agent_id}] Already submitted for this tick, skipping", file=sys.stderr)
        sys.exit(EXIT_ALREADY_SUBMITTED)

    if "HTTP 401" in message:
        sys.exit(EXIT_PERMANENT)

    if "HTTP 403" in message:
        sys.exit(EXIT_PERMANENT)

    if "Context hash mismatch" in message or "Supertick mismatch" in message:
        sys.exit(EXIT_PERMANENT)

    # Other errors are transient
    sys.exit(EXIT_TRANSIENT)


if __name__ == "__main__":
    main()

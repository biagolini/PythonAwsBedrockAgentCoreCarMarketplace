"""Chat with the agent deployed on AgentCore Runtime, from the terminal.

Every message in one run of this script shares the same runtimeSessionId, so the
agent keeps the conversation context (same microVM). Start the script again for
a new session.

Usage:
  AWS_PROFILE=<your-profile> python scripts/invoke_runtime.py --arn <runtime-arn>
  AWS_PROFILE=<your-profile> python scripts/invoke_runtime.py --arn <runtime-arn> "one question"
"""

import argparse
import json
import time
import uuid

import boto3


def read_body(response) -> str:
    """Join the response body, whether plain JSON or a server sent event stream."""
    raw = response["response"].read().decode("utf-8")
    if "text/event-stream" in response.get("contentType", ""):
        chunks = [line[len("data: "):] for line in raw.splitlines() if line.startswith("data: ")]
        raw = "".join(chunks)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(data, dict):
        return data.get("response") or data.get("error") or json.dumps(data, indent=2)
    return str(data)


def ask(client, arn: str, session_id: str, prompt: str) -> None:
    started = time.perf_counter()
    response = client.invoke_agent_runtime(
        agentRuntimeArn=arn,
        runtimeSessionId=session_id,
        qualifier="DEFAULT",
        payload=json.dumps({"prompt": prompt}).encode("utf-8"),
    )
    elapsed = time.perf_counter() - started
    print(f"\nassistant ({elapsed:.1f}s): {read_body(response)}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--arn", required=True, help="AgentCore Runtime ARN")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("prompt", nargs="?", help="Single question; omit for an interactive chat")
    args = parser.parse_args()

    client = boto3.client("bedrock-agentcore", region_name=args.region)
    # Session ids must have at least 33 characters.
    session_id = f"demo-session-{uuid.uuid4()}"
    print(f"Session: {session_id}")

    if args.prompt:
        ask(client, args.arn, session_id, args.prompt)
        return

    print("Type a question, or 'exit' to quit.")
    while True:
        try:
            prompt = input("you: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if prompt.lower() in {"exit", "quit"}:
            break
        if prompt:
            ask(client, args.arn, session_id, prompt)


if __name__ == "__main__":
    main()

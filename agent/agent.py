"""Dealership sales assistant: a single agent that works with or without tools.

Scenario: an online used car marketplace runs one AI sales assistant per
dealership. The agents are written in LangGraph and run as containers on a self
managed EKS cluster. This file is that agent, unchanged in spirit: a LangGraph
ReAct agent on an Amazon Bedrock model. The only AgentCore specific piece is
BedrockAgentCoreApp, which serves the /invocations and /ping routes the Runtime
expects. That is the point: the same container moves to a managed runtime
without a rewrite.

Graceful fallback: if GATEWAY_URL is set and reachable, the agent loads every
tool exposed by that AgentCore Gateway over MCP, signing with the Runtime
execution role (SigV4). If GATEWAY_URL is unset, or the Gateway is unreachable
or not authorized, the agent logs it and answers without tools instead of
breaking. One image covers both the no-tools and the with-tools demo; only the
GATEWAY_URL environment variable (and the runtime's InvokeGateway permission)
decide which path runs.

Conversation context: each Runtime session runs in its own microVM, so an in
process checkpointer keyed by the session id keeps the conversation while the
session is alive. Durable, cross session memory is AgentCore Memory's job.
"""

import asyncio
import logging
import os
from contextlib import AsyncExitStack

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from langchain_aws import ChatBedrockConverse
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dealership-assistant")

REGION = os.environ.get("AWS_REGION", "us-east-1")
# Read the model from the environment, with a safe default so the container
# always starts. Set BEDROCK_MODEL_ID on the Runtime to override it.
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.amazon.nova-2-lite-v1:0")
GATEWAY_URL = os.environ.get("GATEWAY_URL", "").strip()

SYSTEM_PROMPT = (
    "You are the AI sales assistant of a dealership listed on an online used car "
    "marketplace. You help buyers find a vehicle that fits their needs and budget, "
    "explain financing in plain words, and answer questions about the dealership. "
    "Use the available tools when they can ground your answer; when a tool returns "
    "a source, mention it. If you do not know something, say so and offer to have "
    "a salesperson follow up. Never invent prices, vehicle availability, or "
    "financing rates. Be concise and friendly."
)

app = BedrockAgentCoreApp()

# One model client and one checkpointer per microVM, reused across invocations.
model = ChatBedrockConverse(model=MODEL_ID, region_name=REGION)
checkpointer = MemorySaver()


async def load_gateway_tools(stack: AsyncExitStack):
    """Open an MCP session to the Gateway and return its tools, or [] if unset."""
    if not GATEWAY_URL:
        return []

    from langchain_mcp_adapters.tools import load_mcp_tools
    from mcp import ClientSession
    from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client

    # Fail soft: if the Gateway is missing, unreachable, or not authorized, log it
    # and answer without tools instead of failing every question, including a
    # simple "hello" that never needed a tool.
    # The Gateway connection gets its own exit stack, so a failed connection is
    # closed right here and never leaks into the rest of the invocation.
    gateway = AsyncExitStack()
    try:
        read_stream, write_stream, _ = await gateway.enter_async_context(
            aws_iam_streamablehttp_client(
                endpoint=GATEWAY_URL,
                aws_service="bedrock-agentcore",
                aws_region=REGION,
            )
        )
        session = await gateway.enter_async_context(ClientSession(read_stream, write_stream))
        await session.initialize()
        tools = await load_mcp_tools(session)
    except BaseException as exc:  # includes ExceptionGroup raised by the MCP client
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        logger.exception("Could not load tools from the Gateway, answering without tools")
        try:
            await gateway.aclose()
        except BaseException:
            logger.warning("Ignoring an error while closing the failed Gateway connection")
        return []

    stack.push_async_callback(gateway.aclose)
    logger.info("Loaded %d Gateway tools: %s", len(tools), [t.name for t in tools])
    return tools


def answer_text(message) -> str:
    content = message.content
    if isinstance(content, list):  # some models return content blocks
        return " ".join(part.get("text", "") for part in content if isinstance(part, dict)).strip()
    return content


async def handle(payload, context):
    prompt = payload.get("prompt", "").strip()
    if not prompt:
        return {"error": 'Send a JSON payload like {"prompt": "your question"}.'}

    session_id = getattr(context, "session_id", None) or "default"
    logger.info("Invocation for session %s", session_id)

    async with AsyncExitStack() as stack:
        tools = await load_gateway_tools(stack)
        agent = create_react_agent(model, tools, prompt=SYSTEM_PROMPT, checkpointer=checkpointer)
        result = await agent.ainvoke(
            {"messages": [("user", prompt)]},
            config={"configurable": {"thread_id": session_id}},
        )

    return {"response": answer_text(result["messages"][-1]), "session_id": session_id}


@app.entrypoint
def invoke(payload, context):
    return asyncio.run(handle(payload, context))


if __name__ == "__main__":
    app.run()

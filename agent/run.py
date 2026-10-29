"""Tool-calling agent loop (SPEC §15). It knows only the gateway's MCP endpoint (invariant 12).

  python -m agent.run --task "Buy a wireless mouse under ₹2,000" [--backend muse|weak] [--eval]

Tool schemas come from the gateway's MCP `tools/list` and are converted to OpenAI function tools.
At most 25 tool calls per run. The LLM transcript is written to a local file; the gateway receipts
every tool call itself.
"""

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

import anyio
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from agent.prompts import SYSTEM_PROMPT
from intentlock.config import Settings
from intentlock.llm.client import LLMClient

MAX_TOOL_CALLS = 25


@dataclass
class AgentRun:
    messages: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finished: bool = False


def to_openai_tool(tool: Any) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.inputSchema,
        },
    }


def _assistant_message(reply: dict[str, Any]) -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": reply["content"]}
    if reply["tool_calls"]:
        msg["tool_calls"] = [
            {
                "id": c["id"],
                "type": "function",
                "function": {"name": c["name"], "arguments": c["arguments"]},
            }
            for c in reply["tool_calls"]
        ]
    return msg


async def run_agent(
    task: str,
    *,
    gateway_url: str,
    llm: LLMClient,
    model: str | None = None,
    max_tool_calls: int = MAX_TOOL_CALLS,
    transcript_path: str | Path | None = None,
) -> AgentRun:
    run = AgentRun()
    async with streamable_http_client(f"{gateway_url.rstrip('/')}/mcp") as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = [to_openai_tool(t) for t in (await session.list_tools()).tools]
            run.messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": task},
            ]
            while len(run.tool_calls) < max_tool_calls:
                reply = await anyio.to_thread.run_sync(
                    partial(llm.chat, list(run.messages), tools, model=model)
                )
                run.messages.append(_assistant_message(reply))
                if not reply["tool_calls"]:
                    run.finished = True
                    break
                for call in reply["tool_calls"]:
                    text = await _call(session, call)
                    run.tool_calls.append(
                        {"name": call["name"], "arguments": call["arguments"], "result": text}
                    )
                    run.messages.append(
                        {"role": "tool", "tool_call_id": call["id"], "content": text}
                    )
                    if len(run.tool_calls) >= max_tool_calls:
                        break
    if transcript_path:
        Path(transcript_path).parent.mkdir(parents=True, exist_ok=True)
        Path(transcript_path).write_text(json.dumps(run.messages, indent=1, ensure_ascii=False))
    return run


async def _call(session: ClientSession, call: dict[str, Any]) -> str:
    try:
        args = json.loads(call["arguments"] or "{}")
        if not isinstance(args, dict):
            raise ValueError("arguments must be a JSON object")
    except ValueError:
        return json.dumps({"error": "tool arguments were not a valid JSON object"})
    result = await session.call_tool(call["name"], args)
    return result.content[0].text if result.content else ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent")
    parser.add_argument("--task", required=True)
    parser.add_argument("--backend", choices=["muse", "weak"], default="muse")
    parser.add_argument(
        "--eval", action="store_true", help="use the contributor model (synthetic data only)"
    )
    parser.add_argument("--max-tool-calls", type=int, default=MAX_TOOL_CALLS)
    parser.add_argument("--transcript", default=None)
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    if args.backend == "weak":
        llm, model = LLMClient.weak(settings), None
    else:
        llm = LLMClient(settings)
        model = settings.model_id_eval if args.eval else settings.model_id
    run = asyncio.run(
        run_agent(
            args.task,
            gateway_url=settings.gateway_url,
            llm=llm,
            model=model,
            max_tool_calls=args.max_tool_calls,
            transcript_path=args.transcript,
        )
    )
    for c in run.tool_calls:
        print(f"-> {c['name']}({c['arguments']})\n   {c['result'][:200]}")
    print("\n" + (run.messages[-1].get("content") or "(no final message)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

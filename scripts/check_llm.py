"""Probe the configured OpenAI-compatible LLM endpoint and report what it supports.

    uv run python scripts/check_llm.py [--base-url URL] [--model ID] [--timeout S]

Checks: model listing, plain chat, response_format json_schema, json_object, and native tool
calling (vLLM needs --enable-auto-tool-choice --tool-call-parser hermes). Prints a capability
table and the LLM_STRUCTURED_OUTPUT value to use. Exit code 0 if reachable, 2 if not.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import openai
from openai import AsyncOpenAI

from support_assistant.config import get_settings
from support_assistant.llm import parse_json
from support_assistant.tools import GET_ORDER_STATUS, TOOL_SCHEMAS


@dataclass
class Probe:
    name: str
    status: str = "untested"  # ok | fail | unsupported | untested
    detail: str = ""
    ms: float | None = None


@dataclass
class Report:
    base_url: str
    model: str
    reachable: bool = False
    probes: list[Probe] = field(default_factory=list)

    def ok(self, name: str) -> bool:
        return any(p.name == name and p.status == "ok" for p in self.probes)

    def recommended_structured_output(self) -> str:
        if self.ok("json_schema"):
            return "json_schema"
        if self.ok("json_object"):
            return "json_object"
        return "none"


_SCHEMA = {
    "type": "object",
    "properties": {"intent": {"type": "string", "enum": ["order_status", "product_question"]}},
    "required": ["intent"],
    "additionalProperties": False,
}
_CLASSIFY = [
    {"role": "system", "content": 'Classify the message. Reply only with JSON {"intent": ...}.'},
    {"role": "user", "content": "Where is my order PN-10002?"},
]


async def _timed(probe: Probe, coro: Any) -> Any:
    start = time.perf_counter()
    try:
        return await coro
    finally:
        probe.ms = (time.perf_counter() - start) * 1000


async def probe(base_url: str, model: str, api_key: str, timeout_s: float) -> Report:
    report = Report(base_url=base_url, model=model)
    client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=timeout_s, max_retries=0)

    models = Probe("list_models")
    report.probes.append(models)
    try:
        listed = await _timed(models, client.models.list())
        ids = [m.id for m in listed.data]
        report.reachable = True
        models.status = "ok"
        models.detail = ", ".join(ids[:5]) + ("" if model in ids else f"  (!) {model} not listed")
    except (openai.APIConnectionError, openai.APITimeoutError) as exc:
        models.status, models.detail = "fail", f"unreachable: {type(exc).__name__}: {exc}"
        report.probes += [Probe(n) for n in ("chat", "json_schema", "json_object", "tool_calling")]
        return report
    except openai.APIError as exc:  # reachable, but /models not served or auth problem
        report.reachable = True
        models.status, models.detail = "fail", f"{type(exc).__name__}: {exc}"

    async def run(name: str, **kwargs: Any) -> tuple[Probe, Any]:
        p = Probe(name)
        report.probes.append(p)
        try:
            resp = await _timed(
                p,
                client.chat.completions.create(
                    model=model, temperature=0, max_tokens=150, **kwargs
                ),
            )
            return p, resp
        except openai.BadRequestError as exc:
            p.status, p.detail = "unsupported", str(exc)[:160]
        except openai.APIError as exc:
            p.status, p.detail = "fail", f"{type(exc).__name__}: {str(exc)[:160]}"
        return p, None

    p, resp = await run("chat", messages=[{"role": "user", "content": "Reply with the word OK."}])
    if resp is not None:
        text = resp.choices[0].message.content or ""
        p.status, p.detail = ("ok" if text.strip() else "fail"), repr(text[:60])

    for name, fmt in (
        (
            "json_schema",
            {
                "type": "json_schema",
                "json_schema": {"name": "r", "schema": _SCHEMA, "strict": True},
            },
        ),
        ("json_object", {"type": "json_object"}),
    ):
        p, resp = await run(name, messages=_CLASSIFY, response_format=fmt)
        if resp is not None:
            text = resp.choices[0].message.content or ""
            try:
                value = parse_json(text).get("intent")
                ok = value in _SCHEMA["properties"]["intent"]["enum"]
                p.status, p.detail = ("ok" if ok else "fail"), repr(text[:80])
            except (ValueError, AttributeError):
                p.status, p.detail = "fail", f"unparseable: {text[:80]!r}"

    p, resp = await run(
        "tool_calling",
        messages=[{"role": "user", "content": "Where is my order PN-10002?"}],
        tools=[TOOL_SCHEMAS[GET_ORDER_STATUS]],
        tool_choice="auto",
    )
    if resp is not None:
        calls = resp.choices[0].message.tool_calls or []
        if calls and calls[0].function.name == GET_ORDER_STATUS:
            args = json.loads(calls[0].function.arguments or "{}")
            p.status, p.detail = (
                ("ok" if args.get("order_id") == "PN-10002" else "fail"),
                f"args={args}",
            )
        else:
            content = resp.choices[0].message.content or ""
            p.status, p.detail = "fail", f"no tool call; content={content[:80]!r}"
    return report


def render(report: Report) -> str:
    lines = [f"endpoint: {report.base_url}", f"model:    {report.model}", ""]
    lines.append(f"{'capability':<14} {'status':<12} {'ms':>7}  detail")
    lines.append("-" * 78)
    for p in report.probes:
        ms = f"{p.ms:.0f}" if p.ms is not None else "-"
        lines.append(f"{p.name:<14} {p.status:<12} {ms:>7}  {p.detail}")
    lines.append("")
    if not report.reachable:
        lines.append("Endpoint unreachable: nothing verified. The app still runs in degraded mode")
        lines.append("(rule-based routing, templated tool answers, escalation).")
        return "\n".join(lines)
    lines.append(f"Recommended: LLM_STRUCTURED_OUTPUT={report.recommended_structured_output()}")
    if not report.ok("tool_calling"):
        lines.append(
            "Tool calling not working: start vLLM with --enable-auto-tool-choice "
            "--tool-call-parser hermes. Until then the tool agent falls back to "
            "deterministic calls."
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    s = get_settings()
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--base-url", default=s.llm_base_url)
    ap.add_argument("--model", default=s.model_for("agent"))
    ap.add_argument("--timeout", type=float, default=15.0)
    args = ap.parse_args(argv)
    report = asyncio.run(probe(args.base_url, args.model, s.llm_api_key, args.timeout))
    print(render(report))
    return 0 if report.reachable else 2


if __name__ == "__main__":
    sys.exit(main())

"""Explicit paid probe: hello, one read-only tool, restored protocol, hello.

Never updates production capability records or writes provider response bodies.
Run in the Compose Linux environment with its existing credential configuration.
"""
import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from backend.config.settings import load_settings
from backend.contracts.model_context import ModelCapabilities
from backend.contracts.modules.loop import PerceivedContext, ReasoningInput, ToolSpec
from backend.contracts.modules.perception import ContextAssembly, ContextItem, ContextLink
from backend.contracts.schemas.core import ModuleRequest, PortError
from backend.infrastructure.model.protocols import adapter_for
from backend.infrastructure.model.registry import ModelRegistry
from backend.infrastructure.model.relay_config import load_relay_config, relay_endpoint_for_protocol
from backend.infrastructure.observability.memory import MemoryEventSink


def request(run_id, items):
    return ModuleRequest(run_id=run_id, call_id=uuid4().hex, input=ReasoningInput(
        perceived=PerceivedContext(run_id=run_id, items=items, assembly=ContextAssembly()),
        tools=[ToolSpec(name="acknowledge_hello", description="收到问候后先调用本工具一次；它只记录问候，没有副作用。", parameters={"type": "object", "properties": {}, "additionalProperties": False}, read_only=True)],
    ))


async def probe(profile, facts, config):
    settings = load_settings().model_copy(deep=True)
    cfg = settings.model
    cfg.provider, cfg.name, cfg.protocol = "relay", profile.model_id, profile.protocol
    cfg.endpoint = relay_endpoint_for_protocol(config.endpoint, cfg.protocol).format(model=cfg.name)
    cfg.allowed_hosts = [urlparse(cfg.endpoint).hostname]
    cfg.thinking_mode = "low"
    cfg.relay_effort_supported = True
    cfg.decision_protocol = "text"
    cfg.timeout_seconds = 30
    cfg.modes["low"].output_tokens = 512
    settings.prompts = {key: "" for key in settings.prompts}
    settings.prompts["system"] = "收到用户问候时，必须先调用 acknowledge_hello 工具一次。收到工具结果后，只回复：你好。不要调用其他工具。"
    settings.model_capabilities[cfg.name] = ModelCapabilities(
        provider="relay", model_id=cfg.name, version_or_alias=cfg.name,
        raw_context_tokens=facts.get("raw_context_tokens"),
        input_limit_tokens=facts.get("input_limit_tokens"),
        max_generation_tokens=facts.get("max_generation_tokens"),
        conservative_context_tokens=profile.context_tokens,
        conservative_generation_tokens=profile.max_generation_tokens,
        generation_includes_thinking=True, image_transport="none",
        source=["probe"], checked_on="2026-11-21", notes="Explicit low-cost protocol probe settings.",
    )
    events = MemoryEventSink()
    run_id = "probe-" + uuid4().hex
    current = ContextItem(id="current-user", type="message", source="user", role="user", body={"goal": "你好"})
    result = {"model": cfg.name, "protocol": cfg.protocol, "effort": "low", "hello": False, "tools": False, "streaming": False}

    async def delta(text):
        # This probe checks stream completion; private model text is not logged.
        return

    try:
        async with adapter_for(settings, config.key, events) as model:
            decision = await model.decide_stream(request(run_id, [current]), delta)
            if decision.kind != "action" or decision.incomplete or len(decision.action_requests) != 1:
                result["error"] = "expected_single_greeting_tool"
                return result
            call = decision.action_requests[0]
            if call.tool_name != "acknowledge_hello" or call.arguments != {}:
                result["error"] = "unexpected_greeting_tool"
                return result
            checkpoint = model.export_run_state(run_id)
            # Exercise the same export/restore boundary used by Run checkpoints.
            model.close_run(run_id)
            model.restore_run_state(run_id, checkpoint)
            items = [current,
                ContextItem(id="calls", type="tool_call", source="reasoning", role="assistant", origin_run_id=run_id, body={"text": decision.commentary, "calls": [call.model_dump(mode="json")]}),
                ContextItem(id="result", type="tool_result", source="action", role="tool", origin_run_id=run_id, links=[ContextLink(relation="responds_to", target_id="calls")], body={"tool_name": call.tool_name, "action_id": call.action_id, "outcome": "ok", "result": {"acknowledged": True}}),
            ]
            answer = await model.decide_stream(request(run_id, items), delta)
            result["hello"] = answer.kind == "answer" and bool((answer.answer or "").strip()) and not answer.incomplete
            result["tools"] = result["hello"]
            result["streaming"] = result["hello"] and all(item.diagnostics and item.diagnostics.get("first_frame_ms") is not None for item in events.events)
            result["continuation_restored"] = result["hello"] and bool(checkpoint)
            result["status_codes"] = [item.diagnostics.get("http_status") for item in events.events if item.diagnostics]
    except PortError as exc:
        result["error"] = exc.error.code
        result["status_codes"] = [item.diagnostics.get("http_status") for item in events.events if item.diagnostics]
    return result


async def main(args):
    config = load_relay_config()
    registry = ModelRegistry(load_settings())
    selected = [p for p in registry.profiles() if p.provider == "relay" and p.model_id in args.models]
    if {p.model_id for p in selected} != set(args.models) or any(p.model_id not in registry.relay_capabilities for p in selected):
        raise ValueError("probe_requires_a_catalog_model_with_an_explicit_protocol")
    results = []
    semaphore = asyncio.Semaphore(1)

    async def one(profile):
        async with semaphore:
            value = await probe(profile, registry.relay_capabilities[profile.model_id], config)
            results.append(value)
            if args.output:
                args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(value, ensure_ascii=False), flush=True)

    await asyncio.gather(*(one(profile) for profile in selected))
    return 0 if all(r["hello"] and r["tools"] and r["streaming"] for r in results) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--output", type=Path)
    raise SystemExit(asyncio.run(main(parser.parse_args())))

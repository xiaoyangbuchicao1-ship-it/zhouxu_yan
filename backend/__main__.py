import argparse
import asyncio
import json
import sys
from pathlib import Path

from backend.bootstrap import open_harness
from backend.config.settings import load_settings
from backend.contracts.modules.loop import RunInput
from backend.contracts.schemas.core import PortError
from backend.infrastructure.observability.memory import MemoryEventSink


async def main():
    parser = argparse.ArgumentParser(
        description=load_settings().application["cli_description"]
    )
    parser.add_argument("question")
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    settings = load_settings(args.config)
    events = MemoryEventSink()
    try:
        async with open_harness(settings=settings, events=events) as harness:
            result = await harness.run(RunInput(text=args.question))
        print(
            json.dumps(
                {
                    "model": settings.model.name,
                    "thinking_mode": settings.model.thinking_mode,
                    "result": result.model_dump(),
                    "events": [e.model_dump() for e in events.events],
                },
                ensure_ascii=False,
            )
        )
        return 0 if result.status in ("completed", "needs_input") else 1
    except PortError as exc:
        print(exc.error.model_dump_json())
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main()))

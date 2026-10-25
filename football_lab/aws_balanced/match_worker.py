"""Persistent, process-isolated original agent. Files carry IPC, never stdout."""
import argparse
import asyncio
import json
from pathlib import Path
from .runtime import load_role, evaluate_native, native_report, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--directory", required=True)
    args = parser.parse_args()
    directory = Path(args.directory)
    namespace = load_role(args.profile, args.role)
    write_json(directory / "ready.json", native_report(namespace, []))

    async def run():
        index = 0
        while not (directory.parent / "stop").exists():
            request = directory / f"request-{index:04}.json"
            if not request.exists():
                await asyncio.sleep(.05)
                continue
            payload = json.loads(request.read_text(encoding="utf-8"))
            # A single persistent native Agent owns history for this match only.
            report = await evaluate_native(namespace, [payload])
            report["decisions"][0]["index"] = index
            write_json(directory / f"response-{index:04}.json", report)
            index += 1
    asyncio.run(run())


if __name__ == "__main__":
    main()

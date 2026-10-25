"""Run the original AWS balanced application using Qwen on local Ollama."""
import argparse
import json
from . import ROLES
from .profile import DEFAULT_CONFIG, prepare, verify


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("prepare", "verify", "serve", "evaluate", "worker"):
        child = commands.add_parser(command)
        child.add_argument("--runtime", default="results/aws-balanced-qwen-runtime")
        if command in {"prepare", "serve", "evaluate"}:
            child.add_argument("--config", default=str(DEFAULT_CONFIG))
        if command == "serve":
            child.add_argument("--base-port", type=int, default=8100)
        if command == "evaluate":
            source = child.add_mutually_exclusive_group()
            source.add_argument("--state", help="Raw AWS state, wrapped without local adaptation")
            source.add_argument("--payload", help="Original AWS envelope, forwarded unchanged to every role")
            child.add_argument("--team", type=int, choices=(0, 1), default=0)
            child.add_argument("--repeats", type=int, choices=range(1, 6), default=1)
            child.add_argument("--timeout", type=float, default=120, help="Parent-process diagnostic limit, not a server deadline")
            child.add_argument("--output-root", default="results")
        if command == "worker":
            child.add_argument("--role", choices=ROLES, required=True)
            action = child.add_mutually_exclusive_group(required=True)
            action.add_argument("--port", type=int)
            action.add_argument("--requests")
            child.add_argument("--output")
    args = parser.parse_args(argv)
    if args.command == "prepare":
        path = prepare(args.runtime, args.config)
        print(json.dumps({"runtime": str(path), **verify(path)}, indent=2))
    elif args.command == "verify":
        print(json.dumps(verify(args.runtime), indent=2))
    elif args.command == "serve":
        if not 1024 <= args.base_port <= 65531:
            parser.error("base-port must be 1024..65531")
        from .runtime import serve
        serve(prepare(args.runtime, args.config), args.base_port)
    elif args.command == "evaluate":
        if not 1 <= args.timeout <= 3600:
            parser.error("timeout must be 1..3600 seconds")
        from .runtime import evaluate
        path, report = evaluate(prepare(args.runtime, args.config), state=args.state, payload=args.payload,
                               team=args.team, repeats=args.repeats, timeout=args.timeout, output_root=args.output_root)
        print(json.dumps({"results": str(path), "status": report["status"]}, indent=2))
        raise SystemExit(0 if report["status"] == "COMPLETED" else 1)
    else:
        if args.requests and not args.output:
            parser.error("worker --requests requires --output")
        from .runtime import worker
        worker(args.runtime, args.role, port=args.port, requests=args.requests, output=args.output)


if __name__ == "__main__":
    main()

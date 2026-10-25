"""Command line for the native AWS/Qwen agents and their local environment."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    native_commands = ('prepare', 'verify', 'serve', 'evaluate')
    if argv and argv[0] in native_commands:
        from .aws_balanced.__main__ import main as native_main
        return native_main(argv)
    parser = argparse.ArgumentParser(description='Native AWS balanced agents with local Qwen')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in native_commands:
        sub.add_parser(name, help='Native AWS agent command; use --help for options')
    ui = sub.add_parser('ui', help='Open the native snapshot/match API and dashboard')
    ui.add_argument('--port', type=int, default=8770)
    ui.add_argument('--results', default='results')
    sub.add_parser('doctor', help='Check the native SDKs and local Qwen model')
    replay = sub.add_parser('verify-match', help='Verify recorded physics without model calls')
    replay.add_argument('run')
    match = sub.add_parser('run-match', help='Run the original five AWS agents in local 5v5 physics')
    match.add_argument('--config', default='configs/aws_balanced_qwen.yaml')
    match.add_argument('--duration', type=int, choices=(6, 12, 30, 60, 120), default=30)
    match.add_argument('--interval', type=int, choices=(1, 2, 3), default=2)
    match.add_argument('--team', type=int, choices=(0, 1), default=0)
    match.add_argument('--seed', type=int, default=42)
    match.add_argument('--opponent', choices=('RandomBaseline', 'DefensiveBaseline', 'AggressiveBaseline'), default='AggressiveBaseline')
    match.add_argument('--results', default='results')
    args = parser.parse_args(argv)
    if args.command == 'ui':
        from .observability.api import main as ui_main
        return ui_main(['--port', str(args.port), '--results', args.results])
    if args.command == 'doctor':
        from .doctor import doctor
        print(json.dumps(doctor(), indent=2))
    elif args.command == 'verify-match':
        from .aws_balanced.match import verify_match
        report = verify_match(args.run)
        print(json.dumps(report, indent=2))
        raise SystemExit(0 if report['verified'] else 1)
    elif args.command == 'run-match':
        from .aws_balanced.profile import prepare, settings
        from .aws_balanced.runtime import write_json
        from .aws_balanced.match import run_match
        config = settings(args.config)
        created = datetime.now(timezone.utc)
        run_id = created.strftime('aws-balanced-qwen-%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:8]
        path = Path(args.results).resolve() / run_id
        path.mkdir(parents=True)
        profile = prepare(path / 'runtime', args.config)
        meta = {'id': run_id, 'kind': 'match', 'created_at': created.isoformat(),
                'status': 'RUNNING', 'model': config['ollama']['model'], 'team': args.team}
        write_json(path / 'ui.json', meta)
        print(json.dumps({'run': str(path), 'status': 'RUNNING'}), flush=True)
        try:
            _, report = run_match(profile, path, team=args.team, duration=args.duration,
                                   interval=args.interval, seed=args.seed, opponent=args.opponent)
            meta.update(status=report['status'], error=report.get('error'))
        except BaseException as exc:
            meta.update(status='INTERRUPTED' if isinstance(exc, KeyboardInterrupt) else 'FAILED', error=str(exc))
            raise
        finally:
            write_json(path / 'ui.json', meta)
        print(json.dumps({'run': str(path), 'status': report['status'], 'score': report['score']}))
        raise SystemExit(0 if report['status'] == 'COMPLETED' else 1)

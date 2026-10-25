"""Native entrypoint loading and local supervision; no football decision policies."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import logging
import os
from pathlib import Path
import runpy
import subprocess
import sys
from time import monotonic, perf_counter, sleep
import uuid
import httpx
from . import COMMIT, DIRECTORIES, ROLES
from .profile import ROOT, verify


def load_role(profile, role):
    """One native profile per interpreter; serve/evaluate isolate roles in processes."""
    profile = Path(profile).resolve()
    verify(profile)
    os.environ["FOOTBALL_NATIVE_PROFILE"] = str(profile)
    lib = profile / "lib"
    # Never accidentally pick up the experimental lab or another native profile's libs.
    for name in ("agent_base", "fallback", "state", "parsing", "json_tolerant", "_bootstrap"):
        previous = sys.modules.get(name)
        if previous is not None and Path(previous.__file__).resolve().parent != lib:
            raise RuntimeError(f"Native import isolation conflict: {name}; use a fresh worker process")
    sys.path.insert(0, str(lib))
    entrypoint = profile / "ai-team-strands-balanced" / DIRECTORIES[ROLES.index(role)] / "src/main.py"
    return runpy.run_path(str(entrypoint), run_name=f"aws_balanced_{role}")


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.rows = []

    def emit(self, record):
        self.rows.append({"level": record.levelname, "message": record.getMessage()})


def native_report(namespace, rows):
    agent = namespace["agent"]
    return {"player_id": namespace["MY_PLAYER_ID"], "role": namespace["POSITION_LABEL"],
            "original_aws_model": agent.model.original_aws_model_id,
            "local_model": agent.model.get_config()["model_id"],
            "system_prompt": namespace["SYSTEM_PROMPT"],
            "system_prompt_sha256": hashlib.sha256(namespace["SYSTEM_PROMPT"].encode()).hexdigest(),
            "decisions": rows}


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    # Windows readers may briefly hold a handle that denies replacement. Keep
    # the last complete document visible and retry the atomic rename, never
    # truncate the published JSON while a browser/API reader is using it.
    for attempt in range(10):
        try:
            temporary.replace(path)
            break
        except PermissionError:
            if attempt == 9:
                raise
            sleep(.01 * (attempt + 1))


async def evaluate_native(namespace, payloads, on_update=None):
    """Call the actual registered AWS handler; retain its entire command arrays."""
    app, agent = namespace["app"], namespace["agent"]
    handler = app.handlers["main"]
    rows = []
    capture = Capture()
    logger = app.logger
    logger.addHandler(capture)
    try:
        for index, payload in enumerate(payloads):
            capture.rows.clear()
            before = len(agent.messages)
            previous_assistant = next((m for m in reversed(agent.messages) if m["role"] == "assistant"), None)
            previous_user = next((m for m in reversed(agent.messages) if m["role"] == "user"), None)
            start = perf_counter()
            outputs = [output async for output in handler(deepcopy(payload), None)]
            elapsed = (perf_counter() - start) * 1000
            arrays = [json.loads(output) for output in outputs]
            if not all(isinstance(array, list) for array in arrays):
                raise ValueError("Native handler returned a non-array response")
            logs = deepcopy(capture.rows)
            assistant = next((message for message in reversed(agent.messages) if message["role"] == "assistant"), None)
            user = next((message for message in reversed(agent.messages) if message["role"] == "user"), None)
            # Provider errors can leave the previous conversation untouched. Do
            # not mislabel a prior decision's response/prompt as this call's.
            if assistant is previous_assistant:
                assistant = None
            if user is previous_user:
                user = None
            rows.append({"index": index, "timestamp": datetime.now(timezone.utc).isoformat(),
                         "latency_ms": elapsed, "native_outputs": outputs,
                         "commands": [command for array in arrays for command in array],
                         "source_logs": logs,
                         "parse_fallback": any("LLM parse failed" in x["message"] for x in logs),
                         "exception_fallback": any("agent error:" in x["message"] for x in logs),
                         "emergency_fallback": any("fallback also failed" in x["message"] for x in logs),
                         "conversation_messages_before": before,
                         "conversation_messages_after": len(agent.messages),
                         "last_assistant_message": deepcopy(assistant),
                         "last_user_message": deepcopy(user),
                         "over_500_ms": elapsed > 500, "over_1000_ms": elapsed > 1000})
            if on_update:
                on_update(native_report(namespace, rows))
    finally:
        logger.removeHandler(capture)
    return native_report(namespace, rows)


def worker(profile, role, *, port=None, requests=None, output=None):
    namespace = load_role(profile, role)
    if port is not None:
        namespace["app"].run(port=port, host="127.0.0.1")
    else:
        payloads = json.loads(Path(requests).read_text(encoding="utf-8"))[role]
        write_json(output, native_report(namespace, []))
        report = asyncio.run(evaluate_native(namespace, payloads, lambda r: write_json(output, r)))
        write_json(output, report)


def child_command(profile, role):
    return [sys.executable, "-m", "football_lab.aws_balanced", "worker",
            "--runtime", str(Path(profile).resolve()), "--role", role]


def stop(children):
    for process in children:
        if process.poll() is None:
            process.terminate()
    for process in children:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def check_ollama(manifest):
    config = manifest["settings"]["ollama"]
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=5) as client:
        response = client.get(config["url"] + "/api/tags")
        response.raise_for_status()
        models = {x["name"] for x in response.json().get("models", [])}
    name = config["model"]
    if name not in models and name + ":latest" not in models:
        raise ValueError(f"Local model {name} is not installed; this profile never pulls models")


def serve(profile, base_port=8100):
    manifest = verify(profile)
    check_ollama(manifest)
    children, handles = [], []
    logs = Path(profile) / "logs"
    logs.mkdir(exist_ok=True)
    try:
        for index, role in enumerate(ROLES):
            log = (logs / f"{role}.log").open("ab")
            handles.append(log)
            children.append(subprocess.Popen(child_command(profile, role) + ["--port", str(base_port+index)],
                cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT))
        pending, started = set(range(5)), monotonic()
        with httpx.Client(trust_env=False, timeout=0.5) as client:
            while pending:
                if any(p.poll() is not None for p in children):
                    raise RuntimeError(f"Native runtime exited; inspect {logs}")
                if monotonic()-started > 30:
                    raise TimeoutError(f"Native startup timed out; inspect {logs}")
                for index in list(pending):
                    try:
                        if client.get(f"http://127.0.0.1:{base_port+index}/ping").status_code == 200:
                            pending.remove(index)
                    except httpx.HTTPError:
                        pass
                sleep(0.1)
        print(json.dumps({"implementation": "aws-balanced-qwen", "source_commit": COMMIT,
              "endpoints": {r: f"http://127.0.0.1:{base_port+i}/invocations" for i, r in enumerate(ROLES)},
              "notice": "Five native AgentCore applications; no match engine. Ctrl+C stops all five."}, indent=2), flush=True)
        while all(p.poll() is None for p in children):
            sleep(0.25)
        raise RuntimeError(f"A native runtime exited; inspect {logs}")
    except KeyboardInterrupt:
        pass
    finally:
        stop(children)
        for handle in handles:
            handle.close()


def evaluate(profile, *, state=None, payload=None, team=0, repeats=1, timeout=120, output_root="results", run_directory=None, cancel_event=None):
    manifest = verify(profile)
    check_ollama(manifest)
    run = Path(run_directory).resolve() if run_directory else Path(output_root).resolve() / (datetime.now(timezone.utc).strftime("aws-balanced-qwen-%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8])
    run.mkdir(parents=True, exist_ok=True)
    if payload:
        source = json.loads(Path(payload).read_text(encoding="utf-8"))
        if not isinstance(source, dict) or "prompt" not in source:
            raise ValueError("--payload must contain the original AWS invocation envelope with prompt")
    else:
        source = json.loads(Path(state or ROOT / "football_lab/data/aws_sample_new.json").read_text(encoding="utf-8"))
    requests = {}
    for index, role in enumerate(ROLES):
        # --payload is forwarded unchanged; --state adds only the documented invocation envelope.
        item = source if payload else {"prompt": {"gameState": source, "teamId": team, "myPlayers": [index]}}
        requests[role] = [deepcopy(item) for _ in range(repeats)]
    request_path = run / "requests.json"
    request_path.write_text(json.dumps(requests, indent=2) + "\n", encoding="utf-8")
    (run / "profile.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    children, handles = [], []
    start = monotonic()
    report = {"implementation": "aws-balanced-qwen", "source_commit": COMMIT,
              "kind": "native_fixed_state_evaluation", "official_matches": 0,
              "settings": manifest["settings"], "repeats": repeats,
              "dependencies": {name: version(name) for name in ("strands-agents", "bedrock-agentcore", "ollama")},
              "notice": "Actual original handlers on fixed observations; diagnostic wall-clock timing.",
              "roles": {}}
    try:
        for role in ROLES:
            log = (run / f"{role}.log").open("wb")
            handles.append(log)
            children.append(subprocess.Popen(child_command(profile, role) + ["--requests", str(request_path), "--output", str(run / f"{role}.json")],
                cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT))
        while any(p.poll() is None for p in children) and monotonic()-start < timeout and not (cancel_event and cancel_event.is_set()):
            sleep(0.1)
        report["status"] = "INTERRUPTED" if cancel_event and cancel_event.is_set() else "COMPLETED" if all(p.poll() == 0 for p in children) else "FAILED"
        for role in ROLES:
            path = run / f"{role}.json"
            report["roles"][role] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"error": "Worker failed or exceeded diagnostic timeout; inspect role log"}
    finally:
        stop(children)
        for handle in handles:
            handle.close()
        report["wall_seconds"] = monotonic()-start
        (run / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return run, report

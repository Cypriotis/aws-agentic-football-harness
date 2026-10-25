"""UI jobs around the original AWS application; no replacement decision pipeline."""
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import re
import threading
from time import monotonic
import uuid
import httpx
import yaml
from football_lab.aws_balanced import COMMIT, ROLES
from football_lab.aws_balanced.profile import ROOT, settings, prepare
from football_lab.aws_balanced.runtime import evaluate, write_json


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


class NativeWorkspace:
    def __init__(self, service):
        self.service = service
        self.root = service.root
        self.health_cache = None
        self.health_at = 0
        self.stop_event = threading.Event()
        self.threads = []
        self.controls = {}
        for path in self.root.glob("aws-balanced-qwen-*/ui.json"):
            meta = read_json(path, {})
            if meta.get("status") == "RUNNING":
                meta.update(status="INTERRUPTED", error="Server restarted; completed decision records retained")
                write_json(path, meta)

    def path(self, run_id):
        if not re.fullmatch(r"aws-balanced-qwen-[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}", run_id):
            raise ValueError("Invalid native execution identifier")
        path = (self.root / run_id).resolve()
        if not path.is_relative_to(self.root) or not path.is_dir():
            raise FileNotFoundError("Native execution not found")
        return path

    def catalog(self):
        if self.health_cache is None or monotonic()-self.health_at > 5:
            config = settings()
            health = {"model": config["ollama"]["model"], "endpoint": config["ollama"]["url"],
                      "models": [], "ready": False, "error": None, "dependencies": {}}
            try:
                for name in ("strands-agents", "bedrock-agentcore", "ollama"):
                    health["dependencies"][name] = version(name)
                with httpx.Client(trust_env=False, timeout=2, follow_redirects=False) as client:
                    response = client.get(config["ollama"]["url"] + "/api/tags")
                    response.raise_for_status()
                    health["models"] = sorted(x["name"] for x in response.json().get("models", [])
                        if "qwen" in x["name"].lower() and not any(k in x["name"].lower() for k in ("embedding", "cloud")))
                health["ready"] = bool(health["models"])
                if not health["ready"]:
                    health["error"] = "No installed local Qwen chat model found"
            except (httpx.HTTPError, ValueError, KeyError, PackageNotFoundError) as exc:
                health["error"] = f"Local model/runtime unavailable: {type(exc).__name__}"
            self.health_cache, self.health_at = health, monotonic()
        runs = []
        for path in sorted(self.root.glob("aws-balanced-qwen-*/"), reverse=True)[:200]:
            if not re.fullmatch(r"aws-balanced-qwen-[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}", path.name):
                continue
            if not path.resolve().is_relative_to(self.root):
                continue
            meta = read_json(path / "ui.json")
            summary = read_json(path / "summary.json", {})
            if not meta and summary.get("implementation") != "aws-balanced-qwen":
                continue
            item = meta or {"id": path.name, "created_at": summary.get("created_at") or datetime.strptime(path.name.removeprefix("aws-balanced-qwen-")[:16], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat(),
                           "status": summary.get("status", "PARTIAL"), "team": None,
                           "model": summary.get("settings", {}).get("ollama", {}).get("model"),
                           "repeats": summary.get("repeats", 1)}
            runs.append(item)
        return {"health": self.health_cache, "runs": runs, "source_commit": COMMIT,
                "active": any(t.is_alive() for t in self.service.jobs.values())}

    def launch(self, *, model, team=0, repeats=1, state=None, kind="snapshot", duration=30,
               interval=2, seed=42, opponent="AggressiveBaseline"):
        catalog = self.catalog()
        if not catalog["health"]["ready"]:
            raise ValueError(catalog["health"]["error"] or "Local runtime unavailable")
        if model not in catalog["health"]["models"]:
            raise ValueError("Choose an installed local Qwen chat model")
        if state is not None and len(json.dumps(state)) > 100000:
            raise ValueError("Custom state exceeds 100 KB")
        if kind == "match" and state is not None:
            raise ValueError("Matches start from the seeded simulator kickoff; custom states are for observation tests")
        with self.service.lock:
            if any(t.is_alive() for t in self.service.jobs.values()):
                raise ValueError("An execution is already active; wait for it to complete")
            created = datetime.now(timezone.utc)
            run_id = created.strftime("aws-balanced-qwen-%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
            path = self.root / run_id
            path.mkdir()
            config = settings()
            config["ollama"]["model"] = model
            config_path = path / "native-config.yaml"
            config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
            state_path = path / "input-state.json"
            state_path.write_text(json.dumps(state if state is not None else read_json(ROOT / "football_lab/data/aws_sample_new.json"), indent=2) + "\n", encoding="utf-8")
            meta = {"id": run_id, "created_at": created.isoformat(), "status": "RUNNING", "team": team,
                    "repeats": repeats, "model": model, "input": "custom" if state is not None else "sample", "kind": kind}
            if kind == "match":
                from football_lab.aws_balanced.match import MatchControl
                self.controls[run_id] = MatchControl()
                meta["match_config"] = {"duration": duration, "interval": interval, "seed": seed, "opponent": opponent}
            write_json(path / "ui.json", meta)

            def execute():
                try:
                    runtime = prepare(path / "runtime", config_path)
                    if kind == "match":
                        from football_lab.aws_balanced.match import run_match
                        _, report = run_match(runtime, path, team=team, duration=duration, interval=interval,
                                              seed=seed, opponent=opponent, control=self.controls[run_id])
                    else:
                        _, report = evaluate(runtime, state=state_path, team=team, repeats=repeats,
                                             timeout=300, run_directory=path, cancel_event=self.stop_event)
                    meta["status"] = report["status"]
                    if report["status"] != "COMPLETED":
                        meta["error"] = report.get("error") or ("Match stopped; captured evidence retained" if kind == "match" else "A native worker failed or exceeded the 300-second diagnostic limit")
                except Exception as exc:
                    meta.update(status="FAILED", error=f"{type(exc).__name__}: {str(exc)[:400]}")
                finally:
                    meta["ended_at"] = datetime.now(timezone.utc).isoformat()
                    write_json(path / "ui.json", meta)

            thread = threading.Thread(target=execute, name=f"native-{run_id}", daemon=True)
            self.service.jobs[run_id] = thread
            self.threads.append(thread)
            thread.start()
            return run_id

    def close(self):
        self.stop_event.set()
        for control in self.controls.values():
            control.action("stop")
        for thread in self.threads:
            if thread.is_alive():
                thread.join(timeout=12)

    def control(self, run_id, action):
        self.path(run_id)
        control = self.controls.get(run_id)
        if control is None or not self.service.jobs.get(run_id) or not self.service.jobs[run_id].is_alive():
            raise ValueError("No active local match for this record")
        control.action(action)
        return {"action": action, "paused": control.paused, "stopping": control.cancel.is_set(),
                "notice": "Pause and step take effect at decision boundaries. In-flight inference completes unchanged."}

    @staticmethod
    def _rows(path):
        if not path.exists():
            return []
        result = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                result.append(json.loads(line))
            except ValueError:
                # A concurrently appended final line may not yet be complete.
                continue
        return result

    def frames(self, run_id, *, after=-1, limit=500):
        path = self.path(run_id)
        rows = self._rows(path / "states.jsonl")
        return {"frames": [r for r in rows if r["frame"] > after][:limit], "last_frame": rows[-1]["frame"] if rows else -1}

    def report(self, run_id):
        result = self.detail(run_id)
        if result.get("kind") == "match":
            path = self.path(run_id)
            result["frames"] = self._rows(path / "states.jsonl")
            result["actions"] = self._rows(path / "actions.jsonl")
            result["events"] = self._rows(path / "events.jsonl")
            result["environment"] = read_json(path / "environment.json", {})
        return result

    def detail(self, run_id):
        path = self.path(run_id)
        meta = read_json(path / "ui.json", {})
        summary = read_json(path / "summary.json", {})
        if not meta and summary.get("implementation") != "aws-balanced-qwen":
            raise FileNotFoundError("Native report not found")
        roles = {}
        rows = []
        for index, role in enumerate(ROLES):
            value = read_json(path / f"{role}.json", summary.get("roles", {}).get(role, {}))
            value.setdefault("player_id", index)
            value.setdefault("decisions", [])
            roles[role] = value
            rows.extend({**row, "role": role, "player_id": index} for row in value["decisions"])
        requests = read_json(path / "requests.json", {})
        first = next((value[0] for value in requests.values() if value), {})
        prompt = first.get("prompt", {})
        if isinstance(prompt, str):
            try:
                prompt = json.loads(prompt)
            except ValueError:
                prompt = {}
        state = prompt.get("gameState") if isinstance(prompt, dict) else None
        if state is None:
            state = read_json(path / "input-state.json")
        display = None
        if isinstance(state, dict):
            try:
                from football_lab.game.aws_adapter import parse, serialize
                display = serialize(parse(state))  # presentation only; never fed to native agents
            except (ValueError, TypeError, KeyError, AttributeError):
                pass
        status = meta.get("status", summary.get("status", "PARTIAL"))
        repeats = meta.get("repeats", summary.get("repeats", 1))
        kind = meta.get("kind", summary.get("kind", "snapshot"))
        match = read_json(path / "progress.json") if kind == "match" else None
        if kind == "match":
            state = display = None
        if match:
            match["events"] = self._rows(path / "events.jsonl")
            state = display = match["state"]
            repeats = int(match["duration"] / match["interval"])
        latencies = sorted(r["latency_ms"] for r in rows if isinstance(r.get("latency_ms"), (int, float)))
        def percentile(fraction):
            if not latencies:
                return None
            index = (len(latencies)-1)*fraction
            lo = int(index)
            hi = min(lo+1, len(latencies)-1)
            return latencies[lo] + (latencies[hi]-latencies[lo])*(index-lo)
        return {"id": run_id, "status": status, "error": meta.get("error"), "kind": kind, "match": match,
                "created_at": meta.get("created_at"), "team": meta.get("team", prompt.get("teamId", 0) if isinstance(prompt, dict) else 0),
                "model": meta.get("model", summary.get("settings", {}).get("ollama", {}).get("model")),
                "repeats": repeats, "source_commit": summary.get("source_commit", COMMIT),
                "dependencies": summary.get("dependencies", {}), "roles": roles,
                "state": state, "display_state": display,
                "totals": {"decisions": len(rows), "expected": 5*repeats,
                    "fallbacks": sum(bool(r.get("parse_fallback") or r.get("exception_fallback")) for r in rows),
                    "parse_fallbacks": sum(bool(r.get("parse_fallback")) for r in rows),
                    "exception_fallbacks": sum(bool(r.get("exception_fallback")) for r in rows),
                    "emergency_fallbacks": sum(bool(r.get("emergency_fallback")) for r in rows),
                    "under_500": sum(r["latency_ms"] <= 500 for r in rows if isinstance(r.get("latency_ms"), (int, float))),
                    "under_1000": sum(r["latency_ms"] <= 1000 for r in rows if isinstance(r.get("latency_ms"), (int, float))),
                    "p50": percentile(.5), "p95": percentile(.95)},
                "source_notice": ("Original AWS handlers in simplified local 5v5 physics; model provider replaced by Qwen. "
                                  "Local simulation acceptance and outcomes, not official-server acceptance."
                                  if kind == "match" else "Original AWS handlers; only the model provider changes. Fixed input snapshot. Commands are proposals, not official-server acceptance or simulated outcomes.")}

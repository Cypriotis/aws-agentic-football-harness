"""Native AWS decisions in local football physics, with decision/outcome recording."""
from copy import deepcopy
from importlib.metadata import version
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import threading
from time import monotonic
import yaml
from . import ROLES, COMMIT
from .runtime import ROOT, check_ollama, stop, write_json
from .profile import verify
from football_lab.config import load
from football_lab.game.actions import validate
from football_lab.game.aws_adapter import serialize
from football_lab.simulator.engine import create_simulation
from football_lab.simulator.opponents import create_opponent
from football_lab.telemetry.logger import physics_fingerprint


class MatchStopped(Exception):
    pass


class MatchControl:
    """Pause/step acts at decision boundaries; in-flight native calls are unchanged."""
    def __init__(self):
        self.cancel = threading.Event()
        self.paused = False
        self.steps = 0
        self.lock = threading.Lock()

    def action(self, action):
        with self.lock:
            if action == "stop":
                self.cancel.set()
            elif action == "pause":
                self.paused, self.steps = True, 0
            elif action == "resume":
                self.paused, self.steps = False, 0
            elif action == "step":
                self.paused, self.steps = True, self.steps + 1
            else:
                raise ValueError("Unknown match control")

    def wait(self, on_pause):
        notified = False
        while not self.cancel.is_set():
            with self.lock:
                if not self.paused:
                    return
                if self.steps:
                    self.steps -= 1
                    return
            if not notified:
                on_pause()
                notified = True
            self.cancel.wait(.05)
        raise MatchStopped()


class NativeTeam:
    def __init__(self, profile, path, control, timeout=120):
        self.profile, self.path, self.control = Path(profile), Path(path), control
        self.timeout = timeout
        self.children, self.logs, self.metadata = [], [], {}

    def __enter__(self):
        try:
            for role in ROLES:
                directory = self.path / role
                directory.mkdir(parents=True)
                log = (directory / "worker.log").open("ab")
                self.logs.append(log)
                self.children.append(subprocess.Popen([
                    sys.executable, "-m", "football_lab.aws_balanced.match_worker",
                    "--profile", str(self.profile.resolve()), "--role", role,
                    "--directory", str(directory.resolve())], cwd=ROOT,
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT))
            self._collect("ready.json", lambda role, value: self.metadata.update({role: value}))
            return self
        except BaseException:
            self.close()
            raise

    def _collect(self, filename, callback):
        pending, start = set(ROLES), monotonic()
        while pending:
            if self.control.cancel.is_set():
                raise MatchStopped()
            for role in list(pending):
                path = self.path / role / filename
                if path.exists():
                    callback(role, json.loads(path.read_text(encoding="utf-8")))
                    pending.remove(role)
                elif self.children[ROLES.index(role)].poll() is not None:
                    raise RuntimeError(f"{role} native worker exited; inspect its worker.log")
            if monotonic() - start > self.timeout:
                raise TimeoutError("Native batch exceeded diagnostic timeout; no replacement decisions were invented")
            if pending:
                self.control.cancel.wait(.03)

    def decide(self, observation, team, index, on_result):
        for pid, role in enumerate(ROLES):
            write_json(self.path / role / f"request-{index:04}.json",
                       {"prompt": {"gameState": observation, "teamId": team, "myPlayers": [pid]}})
        self._collect(f"response-{index:04}.json", on_result)

    def close(self):
        if self.path.exists():
            try:
                (self.path / "stop").touch()
            except OSError:
                pass  # Termination below is authoritative, including failed IPC writes.
        stop(self.children)
        for handle in self.logs:
            handle.close()

    def __exit__(self, *args):
        self.close()


def command_admissions(sim, rows, opponent, team):
    """Pass arrays intact to engine. Explicitly guard cross-role ingress ownership.

The local engine rejects duplicate per-player commands. Record every proposed
command and the real acceptance result, rather than silently taking array[0].
"""
    submitted = []
    for pid, row in enumerate(rows):
        row["admissions"] = []
        for command in row["commands"]:
            result = validate(command, team, pid, sim.state)
            ownership = result.category == "ownership_invalid"
            row["admissions"].append({"command": deepcopy(command), "accepted": False,
                                      "category": result.category, "reasons": result.errors})
            if not ownership:
                submitted.append(command)
    submitted.extend(opponent)
    accepted = sim.submit(submitted)
    remaining = deepcopy(accepted)
    for row in rows:
        for item in row["admissions"]:
            if item["category"] == "ownership_invalid":
                continue
            if item["command"] in remaining:
                item.update(accepted=True, category="accepted", reasons=[])
                remaining.remove(item["command"])
            elif item["category"] == "valid":
                item.update(category="duplicate_player_command", reasons=["Local engine accepts one valid command per player per decision boundary"])
    return submitted, accepted


def behavior_signals(row, prior, team, cluster=False):
    """Transparent review cues; never a tactical grade and never a policy input."""
    signals = []
    def flag(code, title, explanation, severity="review"):
        signals.append({"code": code, "title": title, "explanation": explanation, "severity": severity})
    rejected = [a for a in row.get("admissions", []) if not a["accepted"]]
    if rejected:
        flag("rejected", "Command rejected", "; ".join(reason for a in rejected for reason in a["reasons"]), "error")
    if row.get("parse_fallback") or row.get("exception_fallback") or row.get("emergency_fallback"):
        flag("fallback", "Original AWS fallback", "The native handler used its own fallback instead of a parsed model command.")
    before = row["observation"]
    commands = [a["command"] for a in row.get("admissions", []) if a["accepted"]]
    kinds = [c.get("commandType") for c in commands]
    holder_team = before["ball"].get("possessionTeamId")
    if holder_team == team and any(k in {"PRESS_BALL", "INTERCEPT"} for k in kinds):
        flag("press_teammate", "Pressing while team has possession", "The input confirms teammate possession. Review whether pressing the ball supports the attack.")
    if cluster and any(k in {"PRESS_BALL", "INTERCEPT"} for k in kinds):
        flag("many_pressers", "Three or more agents pursue the ball", "At this boundary at least three accepted commands press or intercept. Review defensive cover and spacing.")
    outcome = row.get("outcome")
    if outcome:
        if any(c.get("parameters", {}).get("sprint") for c in commands) and outcome["stamina_before"] < .25:
            flag("fatigue", "Sprinting with low stamina", "The player began this interval below 25% stamina.")
        if len(prior) >= 2 and all(p.get("commands") == row["commands"] for p in prior[-2:]) and all(p.get("outcome", {}).get("distance_moved", 1) < .25 for p in [*prior[-2:], row]):
            flag("stationary_repeat", "Repeated action with little movement", "Three consecutive command arrays are identical and displacement stayed below 0.25 units in each interval. Holding position may be intentional.")
    return signals


def run_match(profile, path, *, team=0, duration=30, interval=2, seed=42,
              opponent="AggressiveBaseline", control=None, pace=True, team_factory=NativeTeam):
    path, control = Path(path), control or MatchControl()
    manifest = verify(profile)
    if team_factory is NativeTeam:
        check_ollama(manifest)
    cfg = load(overrides={"name": "native-aws-balanced-match", "team_id": team,
                         "simulation": {"match_duration_seconds": duration, "decision_interval_seconds": interval,
                                        "seed": seed, "opponent": opponent}})
    sim = create_simulation(cfg["simulation"])
    rival = create_opponent(opponent, seed)
    path.mkdir(parents=True, exist_ok=True)
    (path / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    write_json(path / "profile.json", manifest)
    write_json(path / "environment.json", {"physics_fingerprint": physics_fingerprint(),
               "implementation": "aws-balanced-qwen", "source_commit": COMMIT,
               "python": sys.version, "dependencies": {name: version(name) for name in ("strands-agents", "bedrock-agentcore", "ollama")},
               "agent_policy": "Original native handlers and their original fallbacks",
               "command_contract": "Full arrays retained. Existing engine accepts first valid command per player; other commands are visibly rejected.",
               "clock": "Fixed-step simulation pauses during native inference; latency is wall-clock time."})
    reports, all_rows, links, passes, current_decisions = {}, {}, {}, {}, {}
    progress = {"kind": "match", "phase": "starting", "state": serialize(sim.state), "frame": 0,
                "duration": duration, "interval": interval, "seed": seed, "opponent": opponent,
                "round": 0, "completed_rounds": 0, "pending_roles": list(ROLES), "paused": False,
                "physics": sim.VERSION}
    status, error, started, frame_index, event_index = "COMPLETED", None, monotonic(), 0, 0
    files = {name: (path / f"{name}.jsonl").open("w", encoding="utf-8") for name in ("states", "actions", "match_events", "events")}
    def append(name, value):
        files[name].write(json.dumps(value) + "\n")
        files[name].flush()
    def save_progress(phase=None):
        if phase:
            progress["phase"] = phase
        progress["paused"] = control.paused
        progress["state"] = serialize(sim.state)
        progress["frame"] = frame_index
        write_json(path / "progress.json", progress)
    def save_roles():
        for role, report in reports.items():
            write_json(path / f"{role}.json", report)
    def record_frame():
        append("states", {"frame": frame_index, "tick": sim.state.tick, "state": serialize(sim.state)})
    def drain():
        nonlocal event_index
        events = sim.drain_events()
        for event in events:
            append("match_events", event)
            actor = (event.get("team_id"), event.get("agent_id"))
            name = event["event"]
            decision = links.get(actor)
            if name == "rejected_action":
                decision = current_decisions.get(actor)
            elif name == "pass_attempt":
                passes[actor] = decision
            elif name in {"pass_success", "pass_failed", "pass_unresolved"}:
                decision = passes.pop(actor, None)
            item = {**event, "seq": event_index, "decision_id": decision}
            event_index += 1
            append("events", item)
            if decision in all_rows:
                all_rows[decision]["events"].append(item)
        return events
    try:
        record_frame()
        drain()
        save_progress()
        with team_factory(profile, path / "workers", control) as native:
            reports.update(deepcopy(native.metadata))
            save_roles()
            index = 0
            while not sim.finished:
                control.wait(lambda: save_progress("paused"))
                if control.cancel.is_set():
                    raise MatchStopped()
                observation = serialize(sim.state)
                state_hash = hashlib.sha256(json.dumps(observation, sort_keys=True).encode()).hexdigest()
                progress.update(round=index, pending_roles=list(ROLES))
                save_progress("deciding")
                rows = [None] * 5
                def received(role, report):
                    pid = ROLES.index(role)
                    row = deepcopy(report["decisions"][0])
                    row.update(index=index, decision_id=f"r{index:04}-p{pid}", player_id=pid,
                               tick=observation["tick"], game_time=observation["gameTime"],
                               observation=observation, observation_sha256=state_hash,
                               events=[], outcome=None, admissions=[], signals=[])
                    rows[pid] = row
                    reports[role]["decisions"].append(row)
                    all_rows[row["decision_id"]] = row
                    progress["pending_roles"].remove(role)
                    write_json(path / f"{role}.json", reports[role])
                    save_progress()
                native.decide(observation, team, index, received)
                if control.cancel.is_set():
                    raise MatchStopped()
                # Ownership and the engine's existing command contract are environment
                # boundaries, not an added agent policy. No rejected action is replaced.
                current_decisions.update({(team, pid): row["decision_id"] for pid, row in enumerate(rows)})
                submitted, accepted = command_admissions(sim, rows, rival.decide(deepcopy(sim.state), 1-team), team)
                for pid, row in enumerate(rows):
                    if any(a["accepted"] for a in row["admissions"]):
                        links[(team, pid)] = row["decision_id"]
                append("actions", {"tick": observation["tick"], "actions": submitted, "accepted": accepted})
                drain()
                save_roles()
                save_progress("advancing")
                for _ in range(round(interval / sim.dt)):
                    if control.cancel.is_set():
                        raise MatchStopped()
                    if sim.finished:
                        break
                    sim.step()
                    frame_index += 1
                    drain()
                    record_frame()
                    save_progress()
                    if pace:
                        control.cancel.wait(sim.dt)
                after = serialize(sim.state)
                cluster = sum(any(a["accepted"] and a["command"]["commandType"] in {"PRESS_BALL", "INTERCEPT"} for a in row["admissions"]) for row in rows) >= 3
                for pid, row in enumerate(rows):
                    def player(state):
                        return next(p for p in state["players"] if p["teamCode"] == ("HOME" if team == 0 else "AWAY") and p["agentId"] == f"agentId_{pid}")
                    before_player, after_player = player(observation), player(after)
                    start, end = before_player["position"], after_player["position"]
                    row["outcome"] = {"end_tick": after["tick"], "end_time": after["gameTime"],
                         "before": before_player, "after": after_player,
                         "distance_moved": math.hypot(end["x"]-start["x"], end["y"]-start["y"]),
                         "stamina_before": before_player["stamina"], "stamina_after": after_player["stamina"],
                         "had_ball": before_player["hasBall"], "has_ball": after_player["hasBall"],
                         "ball_before": observation["ball"], "ball_after": after["ball"],
                         "score_before": observation["score"], "score_after": after["score"],
                         "note": "Observed during this decision interval; not proof the action caused the outcome. Movement is endpoint displacement, not path length."}
                    row["signals"] = behavior_signals(row, reports[ROLES[pid]]["decisions"][:-1], team, cluster)
                progress["completed_rounds"] = index + 1
                save_roles()
                save_progress()
                index += 1
    except (MatchStopped, KeyboardInterrupt):
        status = "INTERRUPTED"
    except Exception as exc:
        status, error = "FAILED", f"{type(exc).__name__}: {str(exc)[:500]}"
    finally:
        try:
            save_roles()
            save_progress("finished" if status == "COMPLETED" else status.lower())
        except OSError as exc:
            status, error = "FAILED", f"Evidence write failed: {exc}"
        finally:
            for handle in files.values():
                handle.close()
        report = {"implementation": "aws-balanced-qwen", "kind": "match", "status": status,
                  "source_commit": COMMIT, "settings": manifest["settings"], "roles": reports,
                  "error": error, "wall_seconds": monotonic()-started, "score": dict(sim.state.score),
                  "simulated_seconds": sim.state.game_time, "official_matches": 0}
        write_json(path / "summary.json", report)
    return path, report


def verify_match(path):
    """Re-simulate recorded submissions; no agent or model is invoked."""
    path = Path(path)
    def rows(name):
        return [json.loads(line) for line in (path / f"{name}.jsonl").read_text(encoding="utf-8").splitlines() if line]
    cfg = yaml.safe_load((path / "config.yaml").read_text(encoding="utf-8"))
    sim = create_simulation(cfg["simulation"])
    expected, batches = rows("states"), {r["tick"]: r["actions"] for r in rows("actions")}
    events = sim.drain_events()
    mismatch = []
    for index, item in enumerate(expected):
        if index:
            sim.step()
            events.extend(sim.drain_events())
        if serialize(sim.state) != item["state"]:
            mismatch.append(item["tick"])
        if sim.state.tick in batches:
            sim.submit(batches[sim.state.tick])
            events.extend(sim.drain_events())
    same_events = events == rows("match_events")
    return {"verified": not mismatch and same_events, "frames": len(expected),
            "mismatched_ticks": mismatch[:20], "event_stream_equal": same_events, "model_calls": 0}

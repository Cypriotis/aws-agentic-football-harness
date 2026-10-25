"""Closed-loop source-agent bridge: evidence, admission, cancellation and replay."""
from copy import deepcopy
import json
import threading
import time
import pytest
from fastapi.testclient import TestClient
from football_lab.aws_balanced import ROLES
from football_lab.aws_balanced.profile import prepare
from football_lab.aws_balanced.match import run_match, MatchControl, MatchStopped, verify_match, command_admissions
from football_lab.config import load
from football_lab.game.actions import command
from football_lab.game.aws_adapter import parse
from football_lab.simulator.scripted_policy import scripted_action
from football_lab.observability.api import create_app
from football_lab.simulator.engine import create_simulation


class ScriptedTeam:
    instances = []
    def __init__(self, profile, path, control):
        self.control, self.closed, self.calls = control, False, []
        self.metadata = {r: {"decisions": [], "system_prompt": "labelled offline fixture"} for r in ROLES}
        self.instances.append(self)
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.closed = True
    def decide(self, observation, team, index, on_result):
        self.calls.append(deepcopy(observation))
        for pid, role in enumerate(ROLES):
            cmds = [scripted_action(parse(observation), team, pid)]
            if pid == 1:
                cmds = [command("PASS", team, pid, target_player_id=2, type="GROUND")]
            if pid == 3:
                cmds *= 2
            on_result(role, {"decisions": [{"commands": cmds, "latency_ms": 12,
                      "conversation_messages_before": index*2, "conversation_messages_after": index*2+2}]})


@pytest.mark.parametrize("team", [0, 1])
def test_native_match_evolves_preserves_arrays_and_replays(tmp_path, team):
    profile = prepare(tmp_path / "profile")
    path, report = run_match(profile, tmp_path / "match", duration=6, interval=2,
                            team=team, pace=False, team_factory=ScriptedTeam)
    instance = ScriptedTeam.instances[-1]
    assert report["status"] == "COMPLETED" and report["simulated_seconds"] == 6
    assert instance.closed and [s["tick"] for s in instance.calls] == [0, 20, 40]
    assert instance.calls[0]["players"] != instance.calls[1]["players"]
    assert verify_match(path) == {"verified": True, "frames": 61, "mismatched_ticks": [], "event_stream_equal": True, "model_calls": 0}
    for index in range(3):
        rows = [report["roles"][r]["decisions"][index] for r in ROLES]
        assert len({r["observation_sha256"] for r in rows}) == 1
        assert all(r["outcome"]["end_tick"] == (index+1)*20 for r in rows)
    fwd = report["roles"]["forward1"]["decisions"][0]
    assert len(fwd["commands"]) == 2
    assert fwd["admissions"][0]["accepted"] and not fwd["admissions"][1]["accepted"]
    assert fwd["admissions"][1]["category"] == "duplicate_player_command"
    defender = report["roles"]["defender"]["decisions"][0]
    assert not defender["admissions"][0]["accepted"]
    assert defender["signals"][0]["code"] == "rejected"
    for row in report["roles"]["defender"]["decisions"]:
        rejected = [e for e in row["events"] if e["event"] == "rejected_action"]
        assert len(rejected) == 1 and rejected[0]["tick"] == row["tick"]


def test_bridge_does_not_allow_one_role_to_control_teammates():
    sim = create_simulation(load()["simulation"])
    rows = [{"commands": [command("PRESS_BALL", 0, 4 if pid == 1 else pid, intensity=.5)]} for pid in range(5)]
    submitted, accepted = command_admissions(sim, rows, [], 0)
    assert rows[1]["admissions"][0]["category"] == "ownership_invalid"
    assert len(submitted) == 4 and len(accepted) == 4


def test_atomic_reports_retry_transient_windows_read_locks(tmp_path, monkeypatch):
    from pathlib import Path
    from football_lab.aws_balanced.runtime import write_json
    original = Path.replace
    attempts = []
    def transient(self, target):
        attempts.append(target)
        if len(attempts) < 3:
            raise PermissionError("simulated Windows read handle")
        return original(self, target)
    monkeypatch.setattr(Path, "replace", transient)
    write_json(tmp_path / "report.json", {"complete": True})
    assert json.loads((tmp_path / "report.json").read_text()) == {"complete": True}
    assert len(attempts) == 3


@pytest.mark.asyncio
async def test_failed_invocation_does_not_reuse_previous_model_message():
    import logging
    from types import SimpleNamespace
    from football_lab.aws_balanced.runtime import evaluate_native
    async def handler(payload, context):
        yield "[]"
    agent = SimpleNamespace(messages=[{"role": "user", "content": [{"text": "old observation"}]},
                                      {"role": "assistant", "content": [{"text": "old response"}]}],
                            model=SimpleNamespace(original_aws_model_id="original",
                                                  get_config=lambda: {"model_id": "labelled fixture"}))
    namespace = {"agent": agent, "app": SimpleNamespace(handlers={"main": handler}, logger=logging.getLogger("stale-test")),
                 "MY_PLAYER_ID": 0, "POSITION_LABEL": "GK", "SYSTEM_PROMPT": "fixture"}
    report = await evaluate_native(namespace, [{}])
    assert report["decisions"][0]["last_assistant_message"] is None
    assert report["decisions"][0]["last_user_message"] is None


def test_pause_step_and_cancel():
    control = MatchControl()
    control.action("pause")
    paused, passed = threading.Event(), threading.Event()
    worker = threading.Thread(target=lambda: (control.wait(paused.set), passed.set()))
    worker.start()
    assert paused.wait(1) and not passed.is_set()
    control.action("step")
    worker.join(1)
    assert passed.is_set() and control.paused and control.steps == 0
    control.action("stop")
    with pytest.raises(MatchStopped):
        control.wait(lambda: None)


def test_stopped_match_keeps_partial_evidence_and_closes_team(tmp_path):
    control = MatchControl()
    class CancelTeam(ScriptedTeam):
        def decide(self, *args):
            super().decide(*args)
            control.action("stop")
    path, report = run_match(prepare(tmp_path / "profile"), tmp_path / "match",
                            duration=6, pace=False, team_factory=CancelTeam, control=control)
    assert report["status"] == "INTERRUPTED"
    assert ScriptedTeam.instances[-1].closed
    assert report["roles"]["goalkeeper"]["decisions"][0]["outcome"] is None
    assert verify_match(path)["verified"]


def test_native_match_api_lifecycle_and_frame_pagination(tmp_path, monkeypatch):
    import football_lab.aws_balanced.match as module
    original = module.run_match
    monkeypatch.setattr(module, "run_match", lambda *a, **kw: original(*a, **kw, pace=False, team_factory=ScriptedTeam))
    with TestClient(create_app(tmp_path)) as client:
        native = client.app.state.service.native
        native.health_cache = {"ready": True, "models": ["qwen2.5:14b"]}
        native.health_at = time.monotonic()
        assert client.post("/api/native/runs", json={"model": "qwen2.5:14b", "kind": "match", "state": {}}).status_code == 409
        response = client.post("/api/native/runs", json={"model": "qwen2.5:14b", "kind": "match", "duration": 6})
        assert response.status_code == 202
        run_id = response.json()["id"]
        native.service.jobs[run_id].join(5)
        detail = client.get(f"/api/native/runs/{run_id}").json()
        assert detail["kind"] == "match" and detail["status"] == "COMPLETED"
        assert detail["totals"]["expected"] == 15
        assert detail["state"]["tick"] == 60
        assert len(client.get(f"/api/native/runs/{run_id}/frames?after=10&limit=7").json()["frames"]) == 7
        report = client.get(f"/api/native/runs/{run_id}/report").json()
        assert len(report["frames"]) == 61 and len(report["actions"]) == 3
        assert client.post(f"/api/native/runs/{run_id}/control", json={"action": "pause"}).status_code == 409
        assert client.post("/api/native/runs", json={"model": "qwen2.5:14b", "duration": 999}).status_code == 422

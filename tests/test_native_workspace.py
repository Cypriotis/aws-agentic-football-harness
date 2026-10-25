"""UI boundary tests; invoke no real model while checking the native job adapter."""
import json
import threading
import time
from fastapi.testclient import TestClient
import pytest
from football_lab.aws_balanced import COMMIT, ROLES
from football_lab.aws_balanced.runtime import write_json
from football_lab.observability.api import create_app
from football_lab.observability.service import Service


def ready(native):
    native.health_cache = {"ready": True, "model": "qwen2.5:14b", "models": ["qwen2.5:14b"],
                           "endpoint": "http://localhost:11434", "error": None, "dependencies": {}}
    native.health_at = time.monotonic()


def recording(root, suffix="1234abcd"):
    path = root / f"aws-balanced-qwen-20261025T131006Z-{suffix}"
    path.mkdir()
    commands = [{"commandType": "SET_STANCE", "playerId": 0, "teamId": 0, "parameters": {"stance": 0}},
                {"commandType": "MOVE_TO", "playerId": 0, "teamId": 0, "parameters": {"target_x": 10, "target_y": 2}}]
    write_json(path / "goalkeeper.json", {"decisions": [{"latency_ms": 1100, "commands": commands,
               "parse_fallback": True, "exception_fallback": False, "last_assistant_message": {"content": [{"text": "original response"}]}}]})
    write_json(path / "summary.json", {"implementation": "aws-balanced-qwen", "status": "COMPLETED",
               "source_commit": COMMIT, "repeats": 1, "settings": {"ollama": {"model": "qwen2.5:14b"}}})
    write_json(path / "requests.json", {"goalkeeper": [{"prompt": {"teamId": 0, "myPlayers": [0],
               "gameState": {"tick": 0, "gameTime": 0, "players": [], "ball": {"position": {"x": 0, "y": 0, "z": 0}}}}}]})
    return path


def test_native_history_preserves_reports_and_missing_evidence(tmp_path):
    path = recording(tmp_path)
    with TestClient(create_app(tmp_path)) as client:
        svc = client.app.state.service
        ready(svc.native)
        catalog = client.get("/api/native").json()
        assert catalog["runs"][0]["id"] == path.name
        report = client.get(f"/api/native/runs/{path.name}").json()
        assert report["status"] == "COMPLETED"
        assert report["totals"]["decisions"] == 1 and report["totals"]["expected"] == 5
        assert report["totals"]["fallbacks"] == 1 and report["totals"]["under_1000"] == 0
        assert len(report["roles"]["goalkeeper"]["decisions"][0]["commands"]) == 2
        assert report["roles"]["defender"]["decisions"] == []
        assert report["display_state"] is not None
        assert "not official-server acceptance" in report["source_notice"]
        assert client.get("/api/native/runs/not-a-native-run").status_code == 404


def test_native_launch_routes_through_original_profile_and_recovers(tmp_path, monkeypatch):
    import football_lab.observability.native as module
    seen = []
    gate = threading.Event()
    def execute(profile, **kwargs):
        seen.append((profile, kwargs))
        assert "from football_lab.aws_balanced.model import QwenModel as BedrockModel" in (profile / "lib/agent_base.py").read_text()
        path = kwargs["run_directory"]
        write_json(path / "goalkeeper.json", {"decisions": [{"latency_ms": 20, "commands": [{"commandType": "SET_STANCE"}]}]})
        gate.wait(2)
        for role in ROLES[1:]:
            write_json(path / f"{role}.json", {"decisions": [{"latency_ms": 30, "commands": []}]})
        report = {"status": "COMPLETED", "source_commit": COMMIT}
        write_json(path / "summary.json", report)
        return path, report
    monkeypatch.setattr(module, "evaluate", execute)
    app = create_app(tmp_path)
    with TestClient(app) as client:
        svc = app.state.service
        ready(svc.native)
        response = client.post("/api/native/runs", json={"model": "qwen2.5:14b", "repeats": 1, "team": 1})
        assert response.status_code == 202
        run_id = response.json()["id"]
        for _ in range(100):
            report = client.get(f"/api/native/runs/{run_id}").json()
            if report["totals"]["decisions"]:
                break
            time.sleep(.01)
        assert report["status"] == "RUNNING" and report["totals"]["decisions"] == 1
        assert report["team"] == 1
        assert client.post("/api/runs", json={}).status_code in (404, 405)
        assert client.post("/api/native/runs", json={"model": "qwen2.5:14b"}).status_code == 409
        gate.set()
        svc.jobs[run_id].join(3)
        report = client.get(f"/api/native/runs/{run_id}").json()
        assert report["status"] == "COMPLETED" and report["totals"]["decisions"] == 5
        assert seen[0][1]["cancel_event"] is svc.native.stop_event
    with TestClient(create_app(tmp_path)) as reopened:
        assert reopened.get(f"/api/native/runs/{run_id}").json()["status"] == "COMPLETED"


def test_native_api_rejects_bad_configuration_and_bounds(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        native = client.app.state.service.native
        ready(native)
        assert client.post("/api/native/runs", json={"model": "qwen:cloud"}).status_code == 409
        assert client.post("/api/native/runs", json={"model": "qwen2.5:14b", "repeats": 6}).status_code == 422
        assert client.post("/api/native/runs", json={"model": "qwen2.5:14b", "decision_mode": "v1-active"}).status_code == 422
        assert client.post("/api/native/runs", json={"model": "qwen2.5:14b", "state": {"padding": "x" * 100001}}).status_code == 409
        assert not list(tmp_path.glob("aws-balanced-qwen-*/ui.json"))


def test_native_restart_retains_partial_decisions(tmp_path):
    path = recording(tmp_path)
    write_json(path / "ui.json", {"id": path.name, "status": "RUNNING", "repeats": 2, "team": 0})
    with TestClient(create_app(tmp_path)) as client:
        report = client.get(f"/api/native/runs/{path.name}").json()
        assert report["status"] == "INTERRUPTED" and report["totals"]["decisions"] == 1


def test_native_uses_shared_execution_guard_and_shutdown_signal(tmp_path):
    svc = Service(tmp_path)
    app = create_app(tmp_path, service=svc)
    ready(svc.native)
    wait = threading.Event()
    thread = threading.Thread(target=lambda: wait.wait(2))
    thread.start()
    svc.jobs["other-native-job"] = thread
    try:
        with TestClient(app) as client:
            assert client.post("/api/native/runs", json={"model": "qwen2.5:14b"}).status_code == 409
    finally:
        wait.set(); thread.join(); svc.close()
    assert svc.native.stop_event.is_set()

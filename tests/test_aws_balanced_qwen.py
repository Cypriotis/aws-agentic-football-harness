"""Native-source fidelity, full handler behavior and isolated local-only transport."""
from copy import deepcopy
import importlib.util
import json
import logging
from pathlib import Path
import sys
import types
import httpx
import pytest
from football_lab.aws_balanced import COMMIT, DIRECTORIES, ROLES
from football_lab.aws_balanced.profile import (
    DEFAULT_CONFIG, NEW_IMPORT, OLD_IMPORT, ROOT, prepare, reference_files, settings, verify,
)

NATIVE_AVAILABLE = importlib.util.find_spec("bedrock_agentcore") is not None and importlib.util.find_spec("strands") is not None


@pytest.fixture
def profile(tmp_path):
    return prepare(tmp_path / "native")


@pytest.fixture
def native(profile, monkeypatch):
    if not NATIVE_AVAILABLE:
        pytest.skip("Install the project dependencies for actual native SDK integration")
    from football_lab.aws_balanced.runtime import load_role
    previous_path = list(sys.path)
    monkeypatch.setenv("FOOTBALL_NATIVE_PROFILE", str(profile))
    try:
        yield lambda role: load_role(profile, role)
    finally:
        sys.path[:] = previous_path
        for name, module in list(sys.modules.items()):
            location = getattr(module, "__file__", None)
            if location and Path(location).resolve().is_relative_to(profile):
                sys.modules.pop(name, None)


def test_only_source_change_is_model_import(profile):
    original = reference_files()
    report = verify(profile)
    assert report["source_commit"] == COMMIT
    changed = []
    for name, source in original.items():
        actual = (profile / name).read_bytes()
        if actual != source:
            changed.append(name)
            assert actual.replace(NEW_IMPORT, OLD_IMPORT) == source
    assert changed == ["lib/agent_base.py"]
    for directory in DIRECTORIES:
        assert (profile / "ai-team-strands-balanced" / directory / "src/main.py").is_file()
    assert prepare(profile) == profile


def test_overlay_refuses_edits_and_untracked_code(profile):
    source = profile / "lib/state.py"
    original = source.read_bytes()
    source.write_bytes(original + b"\n# change\n")
    with pytest.raises(ValueError, match="differs"):
        verify(profile)
    source.write_bytes(original)
    (profile / "lib/strands.py").write_text("raise RuntimeError('shadowed')", encoding="utf-8")
    with pytest.raises(ValueError, match="Unexpected Python"):
        verify(profile)


@pytest.mark.parametrize("update", [
    {"ollama": {"url": "https://example.com", "model": "qwen2.5:14b"}},
    {"ollama": {"url": "http://localhost:11434", "model": "qwen:cloud"}},
    {"ollama": {"url": "http://localhost:11434", "model": "qwen2.5:14b", "temperature": 0}},
    {"source_commit": "different"}, {"memory": {"mode": "short_term"}},
])
def test_profile_rejects_nonlocal_and_non_model_changes(tmp_path, update):
    import yaml
    value = settings()
    value.update(update)
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(value), encoding="utf-8")
    with pytest.raises(ValueError):
        settings(path)


def test_agent_config_is_separate_from_environment_config():
    from football_lab.config import load
    with pytest.raises(ValueError, match="Native AWS profile"):
        load(DEFAULT_CONFIG)


@pytest.mark.parametrize("role", ROLES)
async def test_native_transport_prompt_history_and_full_command_array(native, role):
    from football_lab.aws_balanced.runtime import evaluate_native
    ns = native(role)
    pid = ROLES.index(role)
    original_model = ("micro", "lite", "pro", "micro", "lite")[pid]
    assert ns["agent"].model.original_aws_model_id == f"us.amazon.nova-{original_model}-v1:0"
    assert ns["agent"].system_prompt == ns["SYSTEM_PROMPT"]
    captured = []
    commands = [{"commandType": "SET_STANCE", "parameters": {"stance": 0}},
                {"commandType": "MOVE_TO", "parameters": {"target_x": 999, "target_y": -999, "sprint": True}}]
    # Python booleans test the original AWS recovery parser as well as multi-command preservation.
    response_text = json.dumps(commands).replace("true", "True")

    async def respond(request):
        assert request.url.host == "localhost" and request.url.path == "/api/chat"
        body = json.loads(request.content)
        captured.append(body)
        assert body["model"] == "qwen2.5:14b"
        assert body["options"] == {}  # no experimental generation settings
        assert body["tools"] == []
        return httpx.Response(200, text=json.dumps({"message": {"role": "assistant", "content": response_text}, "done": True, "done_reason": "stop", "total_duration": 1000000, "prompt_eval_count": 100, "eval_count": 30}) + "\n")

    ns["agent"].model.client_args["transport"] = httpx.MockTransport(respond)
    state = json.loads((ROOT / "football_lab/data/aws_sample_new.json").read_text(encoding="utf-8"))
    payload = {"prompt": {"gameState": state, "teamId": 1, "myPlayers": [pid]}}
    report = await evaluate_native(ns, [payload, payload])
    assert len(captured) == 2
    assert captured[0]["messages"][0] == {"role": "system", "content": ns["SYSTEM_PROMPT"]}
    native_state = sys.modules["state"].summarize_state(state, 1, pid, ns["POSITION_LABEL"])
    assert captured[0]["messages"][1]["content"] == native_state
    assert any(m["role"] == "assistant" for m in captured[1]["messages"])
    assert report["decisions"][1]["conversation_messages_before"] > 0
    for row in report["decisions"]:
        assert not row["parse_fallback"] and not row["exception_fallback"]
        assert len(row["commands"]) == 2
        assert all(c["teamId"] == 1 and c["playerId"] == pid for c in row["commands"])
        assert row["commands"][1]["parameters"]["target_x"] == 55
        assert row["commands"][1]["parameters"]["target_y"] == -35


@pytest.mark.parametrize("raw", ["invalid output", "[]", "[{\"commandType\":\"UNKNOWN\"}]", "[{\"commandType\":\"RESET\"}]", "[{\"commandType\":\"MOVE_TO\",\"parameters\":{\"target_x\":90,\"target_y\":-90}}]"])
async def test_handler_decisions_equal_original_aws_handler(native, raw):
    ns = native("midfielder")
    original = types.ModuleType("original_aws_agent_base")
    exec(compile(reference_files()["lib/agent_base.py"], "original_aws_agent_base.py", "exec"), original.__dict__)
    overlay = sys.modules["agent_base"]

    class App:
        logger = logging.getLogger("aws-fidelity")
        def entrypoint(self, fn):
            return fn

    class Agent:
        def __call__(self, prompt):
            return raw

    state = json.loads((ROOT / "football_lab/data/aws_sample_new.json").read_text(encoding="utf-8"))
    for team in (0, 1):
        for string_prompt in (False, True):
            prompt = {"gameState": state, "teamId": team, "myPlayers": [2]}
            payload = {"prompt": json.dumps(prompt) if string_prompt else prompt}
            outputs = []
            for module in (original, overlay):
                handler = module.create_invoke_handler(App(), Agent(), 2, "MID", ns["fallback_commands"], ns["MID_CONFIG"])
                outputs.append([x async for x in handler(deepcopy(payload), None)])
            assert outputs[0] == outputs[1]


async def test_native_sdk_http_contract_without_inference(native):
    from starlette.testclient import TestClient
    ns = native("goalkeeper")
    async def respond(request):
        return httpx.Response(200, text=json.dumps({"message": {"content": '[]'}, "done": True, "done_reason": "stop", "total_duration": 1000000, "prompt_eval_count": 100, "eval_count": 1}) + "\n")
    ns["agent"].model.client_args["transport"] = httpx.MockTransport(respond)
    with TestClient(ns["app"]) as client:
        assert client.get("/ping").status_code == 200
        response = client.post("/invocations", json={"prompt": {"teamId": 0, "myPlayers": [0], "gameState": {}}})
        assert response.status_code == 200
        assert "SET_STANCE" in response.text


async def test_native_handler_preserves_exception_and_emergency_fallback(native):
    ns = native("midfielder")
    module = sys.modules["agent_base"]
    class App:
        logger = logging.getLogger("aws-fallback-fidelity")
        def entrypoint(self, fn):
            return fn
    class BrokenModel:
        def __call__(self, prompt):
            raise RuntimeError("injected provider failure")
    state = json.loads((ROOT / "football_lab/data/aws_sample_new.json").read_text(encoding="utf-8"))
    payload = {"prompt": {"gameState": state, "teamId": 1, "myPlayers": [2]}}
    handler = module.create_invoke_handler(App(), BrokenModel(), 2, "MID", ns["fallback_commands"], ns["MID_CONFIG"])
    output = [x async for x in handler(payload, None)]
    assert json.loads(output[0]) == ns["fallback_commands"](state, 1, 2)
    def broken_fallback(*args):
        raise RuntimeError("injected fallback failure")
    handler = module.create_invoke_handler(App(), BrokenModel(), 2, "MID", broken_fallback, ns["MID_CONFIG"])
    commands = json.loads([x async for x in handler(payload, None)][0])
    assert len(commands) == 1 and commands[0]["teamId"] == 1 and commands[0]["playerId"] == 2


def test_native_initialization_never_constructs_a_bedrock_model(native, monkeypatch):
    import strands.models
    def forbidden(*args, **kwargs):
        raise AssertionError("Cloud model constructed")
    monkeypatch.setattr(strands.models, "BedrockModel", forbidden)
    for role in ROLES:
        assert native(role)["agent"].model.get_config()["model_id"] == "qwen2.5:14b"

"""Build and verify a reviewable source overlay; never edit the upstream reference."""
from copy import deepcopy
import difflib
import hashlib
import json
from pathlib import Path
import yaml
from football_lab.config import local_url
from . import COMMIT, DIRECTORIES

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "configs/aws_balanced_qwen.yaml"
OLD_IMPORT = b"from strands.models import BedrockModel"
NEW_IMPORT = b"from football_lab.aws_balanced.model import QwenModel as BedrockModel"


def settings(path=DEFAULT_CONFIG):
    value = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return validate_settings(value)


def validate_settings(value):
    if not isinstance(value, dict) or set(value) != {"name", "source_commit", "ollama"}:
        raise ValueError("Native profile accepts only name, source_commit and ollama")
    if value["source_commit"] != COMMIT:
        raise ValueError("Native profile requires the verified pinned AWS commit")
    model = value["ollama"]
    if not isinstance(model, dict) or set(model) != {"url", "model"}:
        raise ValueError("Only the local Ollama endpoint and model may be substituted")
    model["url"] = local_url(model["url"])
    if not isinstance(model["model"], str) or not model["model"].strip() or any(
        x in model["model"].lower() for x in ("cloud", "http:", "https:")):
        raise ValueError("An installed local model name is required")
    return value


def reference_files():
    reference = ROOT / "upstream/aws-reference"
    manifest = json.loads((ROOT / "upstream/manifest.json").read_text(encoding="utf-8"))
    if manifest["commit"] != COMMIT:
        raise ValueError("Reference commit mismatch")
    selected = {}
    for entry in manifest["files"]:
        name = entry["path"]
        data = (reference / name).read_bytes()
        actual = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        if actual != entry["git_blob_sha"]:
            raise ValueError(f"Pinned AWS reference changed: {name}")
        prefix = "agentic-football-sample-agents/"
        if name == "LICENSE":
            selected[name] = data
        elif name.startswith(prefix + "lib/") and name.endswith(".py"):
            selected[name.removeprefix(prefix)] = data
        elif name in {prefix + f"ai-team-strands-balanced/{role}/src/main.py" for role in DIRECTORIES}:
            selected[name.removeprefix(prefix)] = data
    if len([x for x in selected if x.endswith("src/main.py")]) != 5:
        raise ValueError("All five original AWS role entrypoints are required")
    original = selected["lib/agent_base.py"]
    if original.count(OLD_IMPORT) != 1:
        raise ValueError("Expected exactly one original BedrockModel import")
    return selected


def expected_overlay():
    files = reference_files()
    files["lib/agent_base.py"] = files["lib/agent_base.py"].replace(OLD_IMPORT, NEW_IMPORT, 1)
    return files


def prepare(destination, config=DEFAULT_CONFIG):
    value = settings(config)
    target = Path(destination).resolve()
    # Never write an overlay into the original source .
    for protected in (ROOT / "upstream", ROOT / "football_lab", ROOT / "configs"):
        if target == protected or target.is_relative_to(protected):
            raise ValueError("Choose a separate runtime output directory")
    files = expected_overlay()
    if target.exists() and any(target.iterdir()):
        verify(target)
        previous = json.loads((target / "profile.json").read_text(encoding="utf-8"))
        if previous["settings"] != value:
            raise ValueError("Existing profile has different settings; choose a new output directory")
        return target
    target.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    original = reference_files()["lib/agent_base.py"].decode("utf-8").splitlines(keepends=True)
    modified = files["lib/agent_base.py"].decode("utf-8").splitlines(keepends=True)
    (target / "model-substitution.patch").write_text("".join(difflib.unified_diff(
        original, modified, fromfile="aws/lib/agent_base.py", tofile="qwen/lib/agent_base.py")), encoding="utf-8")
    report = {"source_commit": COMMIT, "implementation": "aws-balanced-qwen",
              "settings": value, "changed_upstream_files": ["lib/agent_base.py"],
              "source_change": "one import: BedrockModel -> local QwenModel",
              "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    (target / "profile.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    verify(target)
    return target


def verify(target):
    target = Path(target).resolve()
    manifest = json.loads((target / "profile.json").read_text(encoding="utf-8"))
    if manifest.get("source_commit") != COMMIT:
        raise ValueError("Overlay source commit mismatch")
    expected = expected_overlay()
    if set(manifest.get("files", {})) != set(expected):
        raise ValueError("Overlay source inventory mismatch")
    actual_code = {p.relative_to(target).as_posix() for p in target.rglob("*.py")}
    if actual_code != {name for name in expected if name.endswith(".py")}:
        raise ValueError("Unexpected Python files in native overlay")
    for name, data in expected.items():
        path = target / name
        if not path.resolve().is_relative_to(target) or path.read_bytes() != data:
            raise ValueError(f"Runtime source differs from approved model-only overlay: {name}")
        if manifest["files"][name] != hashlib.sha256(data).hexdigest():
            raise ValueError(f"Overlay manifest mismatch: {name}")
    # Revalidate settings without accepting a modified manifest as configuration.
    validate_settings(deepcopy(manifest["settings"]))
    return manifest

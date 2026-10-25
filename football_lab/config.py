"""Local match environment configuration. Agent policy lives in the pinned AWS source."""
from copy import deepcopy
from pathlib import Path
import math
from urllib.parse import urlparse
import yaml

DEFAULT = {'name': 'native-aws-balanced-match', 'team_id': 0, 'simulation': {'match_duration_seconds': 300, 'physics_timestep_seconds': 0.1, 'physics_version': 'v2', 'decision_interval_seconds': 2, 'seed': 42, 'opponent': 'AggressiveBaseline', 'max_match_duration_seconds': 1800, 'player_speed': 6, 'sprint_multiplier': 1.5, 'ball_speed': 22, 'goal_half_width': 3.66, 'pickup_radius': 1.25, 'save_probability': 0.7, 'player_radius': 0.5, 'control_speed': 8.0, 'shot_block_probability': 0.55, 'pass_interception_probability': 0.65, 'rebound_speed_retention': 0.45, 'ball_drag_per_second': 0.95, 'possession_settle_seconds': 0.3}}


def merge(base, update):
    result = deepcopy(base)
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def local_url(url):
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Ollama URL must use HTTP on localhost, 127.0.0.1 or ::1; inference stays local")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("Ollama URL must be a bare local endpoint without credentials")
    return url.rstrip("/")


def positive(value, label, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{label} must be positive and finite")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label} exceeds {maximum}")


def validate(cfg):
    if set(cfg) != set(DEFAULT):
        raise ValueError("Match configuration accepts only name, team_id and simulation")
    if set(cfg['simulation']) != set(DEFAULT['simulation']):
        raise ValueError("Unknown simulation setting")
    if type(cfg['team_id']) is not int or cfg['team_id'] not in (0, 1):
        raise ValueError("team_id must be 0 or 1")
    sim = cfg["simulation"]
    if sim["physics_version"] != "v2":
        raise ValueError("Only the current local physics v2 is supported")
    for key in ("match_duration_seconds", "physics_timestep_seconds", "decision_interval_seconds",
                "max_match_duration_seconds", "player_speed", "sprint_multiplier", "ball_speed",
                "goal_half_width", "pickup_radius", "player_radius", "control_speed"):
        positive(sim[key], f"simulation.{key}")
    positive(sim["max_match_duration_seconds"], "maximum match duration", 86400)
    positive(sim["match_duration_seconds"], "match duration", sim["max_match_duration_seconds"])
    if sim["match_duration_seconds"] / sim["physics_timestep_seconds"] > 250000:
        raise ValueError("At most 250000 physics ticks per match are supported")
    if type(sim["seed"]) is not int:
        raise ValueError("simulation.seed must be an integer")
    positive(sim["player_radius"], "player_radius", 1)
    positive(sim["control_speed"], "control_speed", 30)
    for key in ("save_probability", "shot_block_probability", "pass_interception_probability",
                "rebound_speed_retention", "ball_drag_per_second"):
        value = sim[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"{key} must be finite and in 0..1")
    if type(sim["possession_settle_seconds"]) not in (int, float) or not 0 <= sim["possession_settle_seconds"] <= 2:
        raise ValueError("possession_settle_seconds must be 0..2")
    for key in ("match_duration_seconds", "decision_interval_seconds"):
        ratio = sim[key] / sim["physics_timestep_seconds"]
        if abs(ratio - round(ratio)) > 1e-7:
            raise ValueError(f"{key} must be a multiple of physics_timestep_seconds")
    if sim["decision_interval_seconds"] < sim["physics_timestep_seconds"]:
        raise ValueError("decision interval must be at least one physics timestep")
    if sim["opponent"] not in {"RandomBaseline", "DefensiveBaseline", "AggressiveBaseline"}:
        raise ValueError("Unknown opponent")
    return cfg


def load(path=None, overrides=None):
    raw = yaml.safe_load(Path(path).read_text(encoding='utf-8')) if path else {}
    raw = {} if raw is None else raw
    if not isinstance(raw, dict):
        raise ValueError('Configuration must be a YAML mapping')
    if 'source_commit' in raw:
        raise ValueError('Native AWS profile: use football_lab.aws_balanced.profile.settings for agent configuration')
    return validate(merge(merge(DEFAULT, raw), overrides or {}))

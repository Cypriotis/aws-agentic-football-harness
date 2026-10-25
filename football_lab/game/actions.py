"""Local simulator commands and admission rules. Native parsing remains upstream."""
from dataclasses import dataclass, field
import math


COMMANDS = {"MOVE_TO", "PASS", "SHOOT", "PRESS_BALL", "INTERCEPT", "MARK", "FOLLOW_PLAYER",
            "SLIDE_TACKLE", "GK_DISTRIBUTE", "SET_STANCE", "CLEAR_OVERRIDE", "RESET"}


def command(kind, team, pid, **params):
    return {"commandType": kind, "teamId": team, "playerId": pid, "parameters": params, "duration": 0}


@dataclass
class Validation:
    category: str
    errors: list[str] = field(default_factory=list)

    @property
    def valid(self):
        return self.category == "valid"


def number(v):
    return type(v) in (int, float) and math.isfinite(v)


def validate(action, team, pid, state=None, allow_team_reset=False, *, check_preconditions=True):
    if not isinstance(action, dict) or not isinstance(action.get("commandType"), str):
        return Validation("syntax_invalid", ["Missing command object or commandType"])
    kind = action["commandType"]
    if kind not in COMMANDS:
        return Validation("unsupported_command", [f"Unsupported command: {kind}"])
    if (type(action.get("teamId")) is not int or type(action.get("playerId")) is not int or
            action.get("teamId") != team or action.get("playerId") != pid or team not in (0, 1) or pid not in range(5)):
        return Validation("ownership_invalid", ["Incorrect or missing team/player ownership"])
    params = action.get("parameters", {})
    if not isinstance(params, dict):
        return Validation("invalid_parameters", ["parameters must be an object"])
    duration = action.get("duration", 0)
    if not number(duration) or not 0 <= duration <= 300:
        return Validation("invalid_parameters", ["duration must be in 0..300 (local safety cap)"])
    required = {
        "MOVE_TO": ("target_x", "target_y", "sprint"), "PASS": ("target_player_id", "type"),
        "SHOOT": ("aim_location", "power"), "PRESS_BALL": ("intensity",), "INTERCEPT": ("aggressive",),
        "MARK": ("target_player_id", "tightness"),
        "FOLLOW_PLAYER": ("target_player_id", "target_team", "distance"),
        "SLIDE_TACKLE": ("target_player_id", "sprint", "distance"),
        "GK_DISTRIBUTE": ("target_player_id", "method"), "SET_STANCE": ("stance",),
    }.get(kind, ())
    errors = [f"Missing parameter: {k}" for k in required if k not in params]
    errors.extend(f"Unsupported parameter for {kind}: {k}" for k in params if k not in required)
    for key, choices in {"type": ("GROUND", "AERIAL", "THROUGH"), "aim_location": ("TL", "TR", "BL", "BR", "CENTER"),
                         "method": ("THROW", "KICK"), "tightness": ("TIGHT", "LOOSE", "NORMAL"),
                         "target_team": ("HOME", "AWAY")}.items():
        if key in params and params[key] not in choices:
            errors.append(f"Invalid {key}: expected one of {choices}")
    for key in ("sprint", "aggressive"):
        if key in params and type(params[key]) is not bool:
            errors.append(f"{key} must be boolean")
    for key, lo, hi in (("target_x", -55, 55), ("target_y", -35, 35), ("intensity", 0, 1),
                        ("power", 0, 1), ("distance", 0, 110)):
        if key in params and (not number(params[key]) or not lo <= params[key] <= hi):
            errors.append(f"{key} must be finite and in {lo}..{hi}")
    for key, allowed in (("target_player_id", range(5)), ("stance", range(3))):
        if key in params and (type(params[key]) is not int or params[key] not in allowed):
            errors.append(f"{key} must be an integer in {list(allowed)}")
    if errors:
        return Validation("invalid_parameters", errors)
    if kind == "SLIDE_TACKLE" and params["distance"] > 3:
        return Validation("invalid_parameters", ["Local slide tackle distance is limited to 3 meters"])
    if not check_preconditions:
        return Validation("valid")
    if kind == "RESET" and not allow_team_reset:
        return Validation("precondition_failed", ["Team-wide RESET is disabled for independently owned agents"])
    if kind == "GK_DISTRIBUTE" and pid != 0:
        return Validation("precondition_failed", ["GK_DISTRIBUTE requires goalkeeper"])
    if state is not None:
        me = state.player(team, pid)
        if me is None:
            return Validation("precondition_failed", ["Owned player absent from visible state"])
        if kind in {"PASS", "SHOOT", "GK_DISTRIBUTE"} and state.ball.holder != (team, pid):
            return Validation("precondition_failed", ["Action needs confirmed ball possession"])
        if kind in {"PASS", "GK_DISTRIBUTE", "MARK", "FOLLOW_PLAYER", "SLIDE_TACKLE"}:
            target_team = team if kind in {"PASS", "GK_DISTRIBUTE"} else 1 - team
            if kind == "FOLLOW_PLAYER":
                target_team = 0 if params["target_team"] == "HOME" else 1
            target = state.player(target_team, params["target_player_id"])
            if target is None or target.key == me.key:
                return Validation("precondition_failed", ["Target player absent or self-targeted"])
            if kind == "SLIDE_TACKLE" and (state.ball.holder != target.key or
                                          me.position.distance(target.position) > 3):
                return Validation("precondition_failed", ["Tackle needs opposing holder within 3 local meters"])
        if kind == "GK_DISTRIBUTE" and abs(me.position.x - (-55 if team == 0 else 55)) > 18:
            return Validation("precondition_failed", ["Local goalkeeper distribution requires own penalty area"])
    return Validation("valid")


def validate_structure(action, team, pid):
    return validate(action, team, pid, check_preconditions=False)

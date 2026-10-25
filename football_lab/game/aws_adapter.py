"""Adapters for legacy and current AWS sample payloads; no cloud imports."""
import json
import math
from .state import Ball, GameState, Player, Vec


def team_id(value):
    if isinstance(value, str):
        return {"home": 0, "away": 1, "0": 0, "1": 1}.get(value.strip().lower())
    return value if type(value) is int and value in (0, 1) else None


def player_id(value):
    if isinstance(value, str):
        try:
            value = int(value.rsplit("_", 1)[-1])
        except ValueError:
            return None
    return value if type(value) is int and 0 <= value <= 4 else None


def finite(value, default=0):
    return float(value) if type(value) in (int, float) and math.isfinite(value) else default


def vector(value):
    value = value if isinstance(value, dict) else {}
    return Vec(finite(value.get("x")), finite(value.get("y")))


def unwrap(payload):
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise ValueError("State payload must be an object")
    if "prompt" in payload:
        prompt = payload["prompt"]
        payload = json.loads(prompt) if isinstance(prompt, str) else prompt
    if not isinstance(payload, dict):
        raise ValueError("prompt must contain an object")
    return payload


def parse(payload):
    payload = unwrap(payload)
    raw = payload.get("gameState", payload)
    if not isinstance(raw, dict):
        raise ValueError("gameState must be an object")
    warnings, players, seen = [], [], set()
    entries = raw.get("players") or []
    if not isinstance(entries, list):
        raise ValueError("players must be an array")
    for entry in entries:
        if not isinstance(entry, dict):
            warnings.append("Skipped non-object player"); continue
        tid = team_id(entry.get("teamCode", entry.get("teamId")))
        pid = player_id(entry.get("agentId", entry.get("playerId")))
        if tid is None or pid is None or (tid, pid) in seen:
            warnings.append("Skipped invalid or duplicate player identity"); continue
        seen.add((tid, pid))
        players.append(Player(tid, pid, vector(entry.get("position")),
                              max(0, min(1, finite(entry.get("stamina"), 1))),
                              vector(entry.get("velocity")), str(entry.get("lastAction", "SET_STANCE"))))
    b = raw.get("ball") or {}
    if not isinstance(b, dict):
        b = {}; warnings.append("Invalid ball object; defaulted visible position")
    claim = b.get("possessionAgentId", b.get("possessionPlayerId"))
    discriminator = b.get("possessionTeamCode", b.get("possessionTeamId"))
    claim_team = team_id(discriminator)
    pid = player_id(claim)
    holder, status = None, "free"
    flagged = [(team_id(p.get("teamCode", p.get("teamId"))), player_id(p.get("agentId", p.get("playerId"))))
               for p in entries if isinstance(p, dict) and p.get("hasBall") is True]
    flagged = [key for key in flagged if key in seen]
    if b.get("isFree") is not True:
        if claim is not None:
            candidates = [p.key for p in players if p.player_id == pid and
                          (discriminator is None or p.team_id == claim_team)]
            # An explicit, unique visible hasBall flag can disambiguate duplicate IDs.
            if len(candidates) > 1 and len(flagged) == 1 and flagged[0] in candidates:
                candidates = flagged
            if len(candidates) == 1 and (not flagged or flagged == candidates):
                holder, status = candidates[0], "known"
            else:
                status = "unknown"
                warnings.append("Ambiguous or unresolved ball possession; team not guessed")
        elif len(flagged) == 1:
            holder, status = flagged[0], "known"
        elif b.get("isFree") is False or len(flagged) > 1:
            status = "unknown"
            warnings.append("Possession marked occupied without a unique holder")
    score = raw.get("score") if isinstance(raw.get("score"), dict) else {}
    return GameState(players, Ball(vector(b.get("position")), vector(b.get("velocity")), holder, status),
                     max(0, int(finite(raw.get("tick")))), max(0, finite(raw.get("gameTime"))),
                     {side: max(0, int(finite(score.get(side)))) for side in ("home", "away")},
                     raw.get("playMode", "OPEN_PLAY"), warnings)


def serialize(state, format="new"):
    if format not in {"new", "old"}:
        raise ValueError("format must be new or old")
    players = []
    for p in state.players:
        identity = ({"agentId": f"agentId_{p.player_id}", "teamCode": "HOME" if p.team_id == 0 else "AWAY"}
                    if format == "new" else {"playerId": p.player_id, "teamId": p.team_id})
        players.append({**identity, "position": p.position.json(), "velocity": p.velocity.json(),
                        "stamina": p.stamina, "lastAction": p.last_action,
                        "hasBall": state.ball.holder == p.key})
    ball = {"position": state.ball.position.json(), "velocity": state.ball.velocity.json(),
            "isFree": state.ball.possession_status == "free"}
    if state.ball.holder:
        tid, pid = state.ball.holder
        ball.update(possessionTeamId=tid)  # Explicit local extension removes newer-ID ambiguity.
        ball["possessionAgentId" if format == "new" else "possessionPlayerId"] = f"agentId_{pid}" if format == "new" else pid
    return {"tick": state.tick, "gameTime": state.game_time, "playMode": state.play_mode,
            "score": dict(state.score), "ball": ball, "players": players}


def invocation(state, team, pid):
    return {"prompt": {"gameState": serialize(state), "teamId": team, "myPlayers": [pid]}}


def invocation_identity(payload, default_player):
    raw = unwrap(payload)
    tid = team_id(raw.get("teamId"))
    if tid is None:
        raise ValueError("Invocation needs explicit teamId; HOME is never assumed")
    ids = raw.get("myPlayers", [default_player])
    if not isinstance(ids, list) or ids != [default_player]:
        raise ValueError("Invocation must assign exactly this role's player")
    return tid, default_player

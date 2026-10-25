"""Typed visible state. Possession always includes both team and player."""
from dataclasses import dataclass, field
import math


@dataclass(frozen=True)
class Vec:
    x: float = 0
    y: float = 0

    def distance(self, other):
        return math.hypot(self.x - other.x, self.y - other.y)

    def json(self):
        return {"x": self.x, "y": self.y, "z": 0}


@dataclass
class Player:
    team_id: int
    player_id: int
    position: Vec = field(default_factory=Vec)
    stamina: float = 1
    velocity: Vec = field(default_factory=Vec)
    last_action: str = "SET_STANCE"

    @property
    def key(self):
        return self.team_id, self.player_id


@dataclass
class Ball:
    position: Vec = field(default_factory=Vec)
    velocity: Vec = field(default_factory=Vec)
    holder: tuple[int, int] | None = None
    possession_status: str = "free"  # free, known, unknown


@dataclass
class GameState:
    players: list[Player] = field(default_factory=list)
    ball: Ball = field(default_factory=Ball)
    tick: int = 0
    game_time: float = 0
    score: dict = field(default_factory=lambda: {"home": 0, "away": 0})
    play_mode: str | int = "OPEN_PLAY"
    warnings: list[str] = field(default_factory=list)

    def player(self, team, pid):
        return next((p for p in self.players if p.key == (team, pid)), None)

    def team(self, team):
        return sorted((p for p in self.players if p.team_id == team), key=lambda p: p.player_id)

    def summary(self, team, pid, role):
        own_goal, opp_goal = goals(team)
        data = {
            "tick": self.tick, "game_time_seconds": self.game_time, "score": self.score,
            "play_mode": self.play_mode, "your_team": team, "your_player": pid,
            "role": role, "own_goal_x": own_goal, "opponent_goal_x": opp_goal,
            "you_have_ball": self.ball.holder == (team, pid),
            "teammate_ids": [p.player_id for p in self.team(team) if p.player_id != pid],
            "opponent_ids": [p.player_id for p in self.team(1 - team)],
            "ball": {"position": self.ball.position.json(), "velocity": self.ball.velocity.json(),
                     "holder": self.ball.holder, "possession_status": self.ball.possession_status},
            "players": [{"team": p.team_id, "id": p.player_id, "x": round(p.position.x, 2),
                         "y": round(p.position.y, 2), "stamina": round(p.stamina, 3)}
                        for p in self.players], "state_warnings": self.warnings,
        }
        return data


def goals(team):
    return (-55.0, 55.0) if team == 0 else (55.0, -55.0)

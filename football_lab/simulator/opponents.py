"""Seeded opponents receive visible state and emit the identical validated command interface."""
import random
from football_lab.simulator.scripted_policy import scripted_action
from football_lab.game.actions import command, validate
from football_lab.game.state import goals


class Baseline:
    def __init__(self, seed=42):
        self.rng = random.Random(seed)

    def decide(self, state, team):
        actions = [self.action(state, team, pid) for pid in range(5)]
        return [a if validate(a, team, pid, state).valid else scripted_action(state, team, pid)
                for pid, a in enumerate(actions)]

    def action(self, state, team, pid):
        return scripted_action(state, team, pid)


class RandomBaseline(Baseline):
    def action(self, state, team, pid):
        if state.ball.holder == (team, pid):
            if self.rng.random() < 0.5:
                return command("SHOOT", team, pid, aim_location="CENTER", power=0.7)
            return command("PASS", team, pid, target_player_id=self.rng.choice([p for p in range(5) if p != pid]), type="GROUND")
        if self.rng.random() < 0.3:
            return command("PRESS_BALL", team, pid, intensity=0.6)
        return command("MOVE_TO", team, pid, target_x=self.rng.uniform(-55, 55), target_y=self.rng.uniform(-35, 35), sprint=False)


class DefensiveBaseline(Baseline):
    def action(self, state, team, pid):
        if pid < 2 or state.ball.holder == (team, pid):
            return scripted_action(state, team, pid)
        own, _ = goals(team)
        if state.ball.holder and state.ball.holder[0] == team:
            return scripted_action(state, team, pid)
        nearest = min(state.team(team)[1:], key=lambda p: p.position.distance(state.ball.position))
        if pid == nearest.player_id:
            return command("PRESS_BALL", team, pid, intensity=0.8)
        return command("MOVE_TO", team, pid, target_x=own * (0.4 if pid == 2 else 0.2),
                       target_y=-12 if pid == 3 else 12 if pid == 4 else 0, sprint=False)


class AggressiveBaseline(Baseline):
    def action(self, state, team, pid):
        if pid < 2 or state.ball.holder == (team, pid) or (state.ball.holder and state.ball.holder[0] == team):
            return scripted_action(state, team, pid)
        return command("PRESS_BALL", team, pid, intensity=1.0)


def create_opponent(name, seed):
    return {"RandomBaseline": RandomBaseline, "DefensiveBaseline": DefensiveBaseline,
            "AggressiveBaseline": AggressiveBaseline}[name](seed)

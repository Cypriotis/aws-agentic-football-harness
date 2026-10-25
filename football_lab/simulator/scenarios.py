"""Seeded kickoff for the native-agent match environment."""
from football_lab.game.state import Ball, GameState, Player, Vec


def initial_state(kickoff_team=0, seed=42):
    # Seed changes visible initial lane jitter while preserving the same 1-1-1-2 roles.
    import random
    rng = random.Random(seed)
    state = GameState()
    for tid in (0, 1):
        direction = 1 if tid == 0 else -1
        for pid, (x, y) in enumerate(((-50, 0), (-28, 0), (-5, 0), (15, -12), (15, 12))):
            state.players.append(Player(tid, pid, Vec(x * direction, y + (rng.uniform(-1, 1) if pid else 0))))
    me = state.player(kickoff_team, 2)
    me.position = Vec(0, 0)
    state.ball = Ball(Vec(0, 0), Vec(), me.key, "known")
    return state

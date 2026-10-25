import json
import pytest
from football_lab.config import load
from football_lab.game.actions import command
from football_lab.game.aws_adapter import serialize
from football_lab.game.state import Vec, Ball
from football_lab.simulator.scripted_policy import scripted_action
from football_lab.simulator.engine import Simulation
from football_lab.simulator.opponents import create_opponent


def play(seed=42):
    cfg = load(overrides={"simulation": {"seed": seed, "match_duration_seconds": 20}})["simulation"]
    sim = Simulation(cfg)
    states, events = [], []
    while not sim.finished:
        if sim.state.tick % 20 == 0:
            sim.submit([scripted_action(sim.state, tid, pid) for tid in (0, 1) for pid in range(5)])
        sim.step()
        states.append(serialize(sim.state))
        events.extend(sim.drain_events())
    return sim, states, events


def test_seeded_determinism_and_termination():
    a, states_a, events_a = play()
    b, states_b, events_b = play()
    assert states_a == states_b
    assert events_a == events_b
    assert a.state.game_time == 20
    assert a.state.play_mode == "FULL_TIME"
    assert events_a[-1]["event"] == "full_time"
    assert len(states_a[-1]["players"]) == 10
    assert all(-55 <= p["position"]["x"] <= 55 for s in states_a for p in s["players"])
    assert all(0 <= p["stamina"] <= 1 for s in states_a for p in s["players"])
    a.reset()
    assert a.state.tick == 0 and a.state.score == {"home": 0, "away": 0}


def test_goal_and_kickoff():
    sim = Simulation(load()["simulation"])
    # Direct ball crossing tests goal detection independently of shooting accuracy/save randomness.
    sim.state.ball = Ball(Vec(54, 0), Vec(20, 0))
    sim.step()
    assert sim.state.score == {"home": 1, "away": 0}
    assert sim.state.ball.holder == (1, 2)
    assert "goal" in [e["event"] for e in sim.drain_events()]


def test_goalkeeper_save():
    cfg = load()["simulation"]
    cfg["save_probability"] = 1
    sim = Simulation(cfg)
    shooter = sim.state.player(0, 3)
    shooter.position = Vec(45, 0)
    sim.state.player(1, 0).position = Vec(50, 0)
    sim.state.ball = Ball(shooter.position, Vec(), shooter.key, "known")
    sim.submit([command("SHOOT", 0, 3, aim_location="CENTER", power=0.8)])
    for _ in range(8):
        sim.step()
    assert any(e["event"] == "save" for e in sim.drain_events())
    assert sim.state.score["home"] == 0


def test_pass_and_interception():
    sim = Simulation(load()["simulation"])
    for p in sim.state.players:
        p.position = Vec(-50 if p.team_id == 0 else 50, 30)
    sim.state.player(0, 2).position = Vec(0, 0)
    sim.state.player(0, 3).position = Vec(10, 0)
    sim.state.player(1, 1).position = Vec(5, 0)
    sim.state.ball = Ball(Vec(), Vec(), (0, 2), "known")
    sim.submit([command("PASS", 0, 2, target_player_id=3, type="GROUND")])
    for _ in range(5):
        sim.step()
    assert sim.state.ball.holder == (1, 1)
    assert any(e["event"] == "pass_failed" for e in sim.drain_events())


@pytest.mark.parametrize("name", ["RandomBaseline", "DefensiveBaseline", "AggressiveBaseline"])
def test_opponent_same_interface(name):
    from football_lab.game.actions import validate
    sim = Simulation(load()["simulation"])
    actions = create_opponent(name, 42).decide(sim.state, 1)
    assert all(validate(a, 1, pid, sim.state).valid for pid, a in enumerate(actions))


def test_invalid_commands_do_not_crash():
    sim = Simulation(load()["simulation"])
    assert sim.submit([{}, command("MOVE_TO", 0, 2, target_x=900, target_y=0, sprint=True)]) == []
    assert len([e for e in sim.drain_events() if e["event"] == "rejected_action"]) == 2

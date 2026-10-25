"""Behavioral regression cases for the automatic-capture/shot/pass loop."""
import math
import random
import pytest
from football_lab.config import load
from football_lab.game.actions import command
from football_lab.game.state import Ball, Player, Vec
from football_lab.simulator.engine import Simulation, create_simulation
from football_lab.simulator.physics import circle_contact, first_boundary, separate_players


class FixedRandom(random.Random):
    def __init__(self, value):
        super().__init__(0)
        self.value = value

    def random(self):
        return self.value


def shot_scene(draw=0, overrides=None):
    cfg = load(overrides={"simulation": overrides or {}})["simulation"]
    sim = Simulation(cfg)
    for i, player in enumerate(sim.state.players):
        player.position = Vec(-40 + 3 * i, 25)
    sim.state.player(0, 3).position = Vec(30, 0)
    sim.state.player(1, 1).position = Vec(32, 0)
    sim.state.ball = Ball(Vec(30, 0), Vec(), (0, 3), "known")
    sim.rng = FixedRandom(draw)
    sim.drain_events()
    return sim


def shoot(sim):
    sim.submit([command("SHOOT", 0, 3, aim_location="CENTER", power=0.9)])


def test_successful_field_block_produces_loose_rebound():
    sim = shot_scene(0)
    shoot(sim)
    sim.step()
    assert sim.state.ball.holder is None
    assert sim.flight["kind"] == "rebound"
    assert sim.state.ball.velocity.x < 0
    assert any(e["event"] == "shot_block" for e in sim.drain_events())


def test_failed_block_does_not_capture_or_reroll_each_tick():
    sim = shot_scene(0.999999)
    shoot(sim)
    for _ in range(4):
        sim.step()
    events = sim.drain_events()
    attempts = [e for e in events if e["event"] == "shot_block_attempt" and e["agent_id"] == 1]
    assert len(attempts) == 1
    assert not attempts[0]["success"]
    assert sim.state.ball.holder is None
    assert sim.state.ball.position.x > 40


def test_fast_shot_bypasses_player_behind_the_launch():
    sim = shot_scene(0)
    sim.state.player(1, 1).position = Vec(29, 0)
    shoot(sim)
    sim.step()
    assert sim.state.ball.holder is None
    assert not [e for e in sim.drain_events() if e["event"] == "shot_block_attempt"]


def test_fast_pass_near_opponent_is_not_guaranteed_capture():
    sim = shot_scene(0.999999)
    sim.state.player(0, 2).position = Vec(45, 0)
    sim.submit([command("PASS", 0, 3, target_player_id=2, type="GROUND")])
    for _ in range(3):
        sim.step()
    events = sim.drain_events()
    attempts = [e for e in events if e["event"] == "ball_control_attempt" and e["team_id"] == 1]
    assert len(attempts) == 1 and not attempts[0]["success"]
    assert sim.state.ball.holder is None


def test_slow_loose_ball_can_be_controlled():
    sim = shot_scene(0.999999)
    sim.state.ball = Ball(Vec(31.9, 0), Vec(1, 0))
    sim.step()
    assert sim.state.ball.holder == (1, 1)


def test_goalkeeper_miss_does_not_turn_into_automatic_pickup():
    sim = shot_scene(0.999999, {"save_probability": 0})
    sim.state.player(0, 3).position = Vec(48, 0)
    sim.state.player(1, 1).position = Vec(-30, 20)
    sim.state.player(1, 0).position = Vec(52, 0)
    sim.state.ball = Ball(Vec(48, 0), Vec(), (0, 3), "known")
    shoot(sim)
    for _ in range(4):
        sim.step()
    events = sim.drain_events()
    assert len([e for e in events if e["event"] == "save_attempt"]) == 1
    assert sim.state.score["home"] == 1


def test_overlap_resolved_deterministically_in_both_list_orders():
    a, b = Player(0, 2, Vec()), Player(1, 2, Vec())
    c, d = Player(0, 2, Vec()), Player(1, 2, Vec())
    separate_players([a, b], 0.5)
    separate_players([d, c], 0.5)
    assert a.position.distance(b.position) >= 1 - 1e-7
    assert a.position == c.position and b.position == d.position


def test_capture_settle_time_prevents_instantaneous_return_tackle():
    sim = shot_scene(0)
    sim.state.tick = 100
    sim.state.game_time = 10
    sim.state.player(0, 3).position = Vec(30.9, 0)
    sim.state.player(1, 1).position = Vec(32, 0)
    sim._capture(sim.state.player(1, 1))
    sim.submit([command("PRESS_BALL", 0, 3, intensity=1)])
    sim.step()
    assert sim.state.ball.holder == (1, 1)


def test_contact_and_boundaries_use_first_crossing():
    distance, entry = circle_contact(Vec(5, 0), Vec(), Vec(10, 0), 1)
    assert distance == 0 and entry == pytest.approx(0.4)
    assert circle_contact(Vec(5, 2), Vec(), Vec(10, 0), 1) is None
    assert circle_contact(Vec(11, 0), Vec(), Vec(10, 0), 2)[1] == pytest.approx(0.9)
    assert circle_contact(Vec(5, 1), Vec(), Vec(10, 0), 1)[1] == pytest.approx(0.5)
    fraction, axis, line = first_boundary(Vec(54, 34.8), Vec(56, 36))
    assert fraction == pytest.approx(1 / 6)
    assert axis == "y" and line == 35


def test_only_current_physics_is_available():
    cfg = load()["simulation"]
    assert create_simulation(cfg).VERSION == "simplified-local-v2"
    cfg["physics_version"] = "v1"
    with pytest.raises(ValueError):
        create_simulation(cfg)


def test_no_artificial_goal_without_a_line_crossing():
    sim = shot_scene(0.999999)
    sim.state.ball = Ball(Vec(0, 0), Vec())
    for _ in range(40):
        sim.step()
    assert sim.state.score == {"home": 0, "away": 0}


def test_block_probability_is_seeded_and_not_a_guaranteed_outcome():
    results = []
    for seed in range(40):
        sim = shot_scene()
        sim.rng = random.Random(seed)
        shoot(sim)
        sim.step()
        results.append(any(e["event"] == "shot_block" for e in sim.drain_events()))
    assert 0 < sum(results) < len(results)


@pytest.mark.parametrize("patch", [{"physics_version": "v9"}, {"shot_block_probability": 2},
                                   {"player_radius": 0}, {"control_speed": -2},
                                   {"ball_drag_per_second": float("nan")}])
def test_invalid_physics_config(patch):
    with pytest.raises(ValueError):
        load(overrides={"simulation": patch})

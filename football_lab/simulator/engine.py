"""Small seeded 5v5 simulator. This deliberately approximates football, not the AWS engine."""
from copy import deepcopy
import math
import random
from football_lab.game.actions import validate
from football_lab.game.state import Ball, Vec, goals
from .physics import clamp_position, move_toward, circle_contact, first_boundary, separate_players
from .scenarios import initial_state


class Simulation:
    VERSION = "simplified-local-v2"

    def __init__(self, config):
        self.config = deepcopy(config)
        self.dt = config["physics_timestep_seconds"]
        self.end_tick = round(config["match_duration_seconds"] / self.dt)
        self.reset()

    def reset(self):
        self.rng = random.Random(self.config["seed"])
        self.state = initial_state(seed=self.config["seed"])
        self.intents, self.expires, self.stances, self.cooldowns = {}, {}, {}, {}
        self.flight = None
        self.last_touch = (0, 2)
        self.possession_since = 0
        self.events = []
        self.finished = False
        self._emit("kickoff", team_id=0)

    def _emit(self, kind, **data):
        self.events.append({"event": kind, "tick": self.state.tick,
                            "simulation_time": self.state.game_time, **data})

    def drain_events(self):
        result, self.events = self.events, []
        return result

    def submit(self, actions):
        """Validate all commands against the same state before applying one-shot effects."""
        accepted, seen = [], set()
        for action in actions:
            if not isinstance(action, dict):
                self._emit("rejected_action", reason="Non-object command"); continue
            team, pid = action.get("teamId"), action.get("playerId")
            result = validate(action, team, pid, self.state)
            if not result.valid:
                self._emit("rejected_action", team_id=team, agent_id=pid, reason=result.errors); continue
            key = (team, pid)
            if key in seen:
                self._emit("rejected_action", team_id=team, agent_id=pid, reason="Duplicate player command"); continue
            seen.add(key); accepted.append(deepcopy(action))
        for action in sorted(accepted, key=lambda a: (a["teamId"], a["playerId"])):
            key = action["teamId"], action["playerId"]
            player = self.state.player(*key)
            player.last_action = action["commandType"]
            self.intents[key] = action
            # MOVE_TO persists to its target; maintained commands default to the next decision.
            duration = action.get("duration", 0)
            self.expires[key] = self.state.game_time + duration if duration > 0 else float("inf")
            kind, params = action["commandType"], action.get("parameters", {})
            if kind == "SET_STANCE":
                self.stances[key] = params["stance"]
            elif kind == "CLEAR_OVERRIDE":
                self.intents.pop(key, None)
            elif kind in {"PASS", "GK_DISTRIBUTE", "SHOOT"}:
                self._kick(player, kind, params)
        return accepted

    def _kick(self, player, kind, params):
        if self.state.ball.holder != player.key:
            return
        self.last_touch = player.key
        if kind == "SHOOT":
            aim = params["aim_location"]
            target_y = 0 if aim == "CENTER" else (2.6 if aim in ("TR", "BR") else -2.6)
            distance = abs(goals(player.team_id)[1] - player.position.x)
            spread = 0.15 + distance * 0.018 + (1 - player.stamina) * 0.6
            target = Vec(goals(player.team_id)[1] + (1 if player.team_id == 0 else -1),
                         target_y + self.rng.uniform(-spread, spread))
            speed = self.config["ball_speed"] * (0.5 + params["power"])
            self.flight = {"kind": "shot", "source": player.key, "started": self.state.game_time,
                           "target": None, "save_tried": False, "attempted": set()}
            self._emit("shot", team_id=player.team_id, agent_id=player.player_id,
                       on_target=abs(target.y) <= self.config["goal_half_width"])
        else:
            receiver = self.state.player(player.team_id, params["target_player_id"])
            target = receiver.position
            if params.get("type") == "THROUGH":
                target = clamp_position(Vec(target.x + (3 if player.team_id == 0 else -3), target.y))
            speed = self.config["ball_speed"]
            self.flight = {"kind": "pass", "source": player.key, "target": receiver.key,
                           "started": self.state.game_time, "save_tried": False, "attempted": set()}
            self._emit("pass_attempt", team_id=player.team_id, agent_id=player.player_id,
                       target_player_id=receiver.player_id, method=kind)
        distance = max(0.001, player.position.distance(target))
        self.state.ball = Ball(player.position,
            Vec((target.x - player.position.x) / distance * speed, (target.y - player.position.y) / distance * speed))

    def _target(self, player):
        key = player.key
        action = self.intents.get(key)
        if not action or self.state.game_time > self.expires.get(key, 0):
            return player.position, False, 1.0
        kind, params = action["commandType"], action.get("parameters", {})
        ball = self.state.ball
        if kind == "MOVE_TO":
            return Vec(params["target_x"], params["target_y"]), params["sprint"], 1.0
        if kind in {"PRESS_BALL", "INTERCEPT", "SLIDE_TACKLE"}:
            target = ball.position
            if kind == "INTERCEPT":
                target = clamp_position(Vec(target.x + ball.velocity.x * 0.2, target.y + ball.velocity.y * 0.2))
            return target, params.get("sprint", params.get("aggressive", False)), 0.5 + params.get("intensity", 0.5) * 0.5
        if kind in {"MARK", "FOLLOW_PLAYER"}:
            tid = 1 - player.team_id
            if kind == "FOLLOW_PLAYER":
                tid = 0 if params["target_team"] == "HOME" else 1
            target = self.state.player(tid, params["target_player_id"])
            if target:
                gap = params.get("distance", 1 if params.get("tightness") == "TIGHT" else 3)
                own = Vec(goals(player.team_id)[0], 0)
                return move_toward(target.position, own, gap), False, 1.0
        return player.position, False, 1.0

    def _capture(self, player, reason="pickup"):
        previous = self.state.ball.holder
        flight = self.flight
        if flight and flight["kind"] == "pass":
            success = flight["source"][0] == player.team_id and flight["source"] != player.key
            self._emit("pass_success" if success else "pass_failed", team_id=flight["source"][0],
                       agent_id=flight["source"][1], receiver_team=player.team_id, receiver_id=player.player_id)
            if player.team_id != flight["source"][0]:
                self._emit("interception", team_id=player.team_id, agent_id=player.player_id)
        self.flight = None
        self.state.ball = Ball(player.position, Vec(), player.key, "known")
        self.last_touch = player.key
        self.possession_since = self.state.game_time
        if previous != player.key:
            self._emit("possession_change", team_id=player.team_id, agent_id=player.player_id, reason=reason)

    def _restart(self, team, goalkeeper=False):
        self.intents.clear(); self.expires.clear(); self.flight = None
        if goalkeeper:
            self._capture(self.state.player(team, 0), "goal_kick")
        else:
            fresh = initial_state(team, self.config["seed"] + sum(self.state.score.values()))
            # Goal restarts reposition players but retain their fatigue.
            for player in self.state.players:
                player.position = fresh.player(*player.key).position
                player.velocity = Vec()
            self.state.ball = fresh.ball
            self.last_touch = (team, 2)
            self.possession_since = self.state.game_time
        self._emit("restart", team_id=team, restart_type="goal_kick" if goalkeeper else "kickoff")

    def _outcome(self, old, new):
        boundary = first_boundary(old, new)
        if boundary is None:
            return False
        fraction, axis, line = boundary
        if axis == "x":
            crossing_y = old.y + (new.y - old.y) * fraction
            if abs(crossing_y) <= self.config["goal_half_width"]:
                scoring_team = 0 if line > 0 else 1
                self.state.score["home" if scoring_team == 0 else "away"] += 1
                if self.flight and self.flight["kind"] == "pass":
                    self._emit("pass_failed", team_id=self.flight["source"][0], agent_id=self.flight["source"][1])
                self._emit("goal", team_id=scoring_team, score=dict(self.state.score))
                self._restart(1 - scoring_team)
            else:
                if self.flight and self.flight["kind"] == "pass":
                    self._emit("pass_failed", team_id=self.flight["source"][0], agent_id=self.flight["source"][1])
                self._emit("out_of_play", boundary="goal_line")
                self._restart(1 if line > 0 else 0, goalkeeper=True)
            return True
        if axis == "y":
            if self.flight and self.flight["kind"] == "pass":
                self._emit("pass_failed", team_id=self.flight["source"][0], agent_id=self.flight["source"][1])
            self.flight = None
            self._emit("out_of_play", boundary="sideline")
            self.state.ball.position = Vec(old.x + (new.x - old.x) * fraction, line)
            team = 1 - self.last_touch[0]
            player = min(self.state.team(team), key=lambda p: p.position.distance(self.state.ball.position))
            player.position = self.state.ball.position
            self._capture(player, "throw_in")
            return True
        return False

    def step(self):
        if self.finished:
            return self.state
        self.state.tick += 1
        self.state.game_time = round(self.state.tick * self.dt, 8)
        # Compute movement targets from one snapshot so list order does not alter decisions.
        targets = {p.key: self._target(p) for p in self.state.players}
        old_positions = {p.key: p.position for p in self.state.players}
        for player in self.state.players:
            target, sprint, intensity = targets[player.key]
            speed = self.config["player_speed"] * (0.5 + player.stamina * 0.5) * intensity
            if sprint and player.stamina > 0.05:
                speed *= self.config["sprint_multiplier"]
            old = player.position
            player.position = clamp_position(move_toward(old, target, speed * self.dt))
            moved = old.distance(player.position)
            player.velocity = Vec((player.position.x - old.x) / self.dt, (player.position.y - old.y) / self.dt)
            player.stamina = max(0, min(1, player.stamina + self.dt * (0.006 if moved < 0.01 else -0.012 if sprint else -0.003)))
        separate_players(self.state.players, self.config["player_radius"])
        for player in self.state.players:
            old = old_positions[player.key]
            player.velocity = Vec((player.position.x - old.x) / self.dt, (player.position.y - old.y) / self.dt)
        ball = self.state.ball
        if ball.holder:
            holder = self.state.player(*ball.holder)
            ball.position = holder.position
            # Only purposeful pressure/tackle can take a held ball; contact is a local heuristic.
            challengers = [p for p in self.state.players if p.team_id != holder.team_id and
                          p.position.distance(holder.position) < 1.6 and
                          self.intents.get(p.key, {}).get("commandType") in {"PRESS_BALL", "SLIDE_TACKLE", "INTERCEPT"}]
            self.rng.shuffle(challengers)
            for p in challengers:
                if self.state.game_time - self.possession_since < self.config["possession_settle_seconds"]:
                    continue
                if self.state.game_time < self.cooldowns.get(p.key, -1):
                    continue
                self.cooldowns[p.key] = self.state.game_time + 1
                probability = 0.6 if self.intents[p.key]["commandType"] == "SLIDE_TACKLE" else 0.35
                if self.rng.random() < probability:
                    self._emit("tackle", team_id=p.team_id, agent_id=p.player_id)
                    self._capture(p, "tackle"); break
        else:
            self._advance_loose_ball()
        if self.state.tick >= self.end_tick:
            self.finished = True
            self.state.play_mode = "FULL_TIME"
            if self.flight and self.flight["kind"] == "pass":
                self._emit("pass_unresolved", team_id=self.flight["source"][0], agent_id=self.flight["source"][1])
            self._emit("full_time", score=dict(self.state.score))
        return self.state

    def _block_shot(self, player, contact, distance, speed):
        """An outfield block creates a rebound, never an automatic controlled possession."""
        radius = self.config["pickup_radius"]
        intent = self.intents.get(player.key, {}).get("commandType")
        readiness = 1 if intent in {"MARK", "PRESS_BALL", "INTERCEPT", "SLIDE_TACKLE"} else 0.65
        probability = (self.config["shot_block_probability"] * readiness *
                       (1 - 0.6 * distance / radius) * (0.7 + 0.3 * player.stamina) *
                       min(1, math.sqrt(self.config["ball_speed"] / max(speed, 0.001))))
        success = self.rng.random() < probability
        self._emit("shot_block_attempt", team_id=player.team_id, agent_id=player.player_id,
                   ball_speed=speed, probability=probability, success=success)
        if not success:
            return False
        ball = self.state.ball
        nx, ny = contact.x - player.position.x, contact.y - player.position.y
        norm = math.hypot(nx, ny)
        if norm < 1e-8:
            nx, ny = -ball.velocity.x / speed, -ball.velocity.y / speed
        else:
            nx, ny = nx / norm, ny / norm
        dot = ball.velocity.x * nx + ball.velocity.y * ny
        retention = self.config["rebound_speed_retention"]
        velocity = Vec((ball.velocity.x - 2 * dot * nx) * retention,
                       (ball.velocity.y - 2 * dot * ny) * retention)
        ball.position = clamp_position(Vec(contact.x + nx * 0.05, contact.y + ny * 0.05))
        ball.velocity = velocity
        self.last_touch = player.key
        self.flight = {"kind": "rebound", "source": player.key, "started": self.state.game_time,
                       "target": None, "save_tried": False, "attempted": set()}
        self._emit("shot_block", team_id=player.team_id, agent_id=player.player_id,
                   rebound_speed=math.hypot(velocity.x, velocity.y))
        return True

    def _advance_loose_ball(self):
        ball = self.state.ball
        old = ball.position
        new = Vec(old.x + ball.velocity.x * self.dt, old.y + ball.velocity.y * self.dt)
        speed = math.hypot(ball.velocity.x, ball.velocity.y)
        boundary = first_boundary(old, new)
        boundary_fraction = boundary[0] if boundary else 1.0
        candidates = []
        for player in self.state.players:
            flight = self.flight
            if flight and player.key == flight["source"] and self.state.game_time - flight["started"] < 0.4:
                continue
            if flight and speed > self.config["control_speed"] and player.key in flight.get("attempted", set()):
                continue
            # Players behind a departing fast ball cannot capture it merely because
            # the launch point is still inside their old pickup radius.
            if speed > self.config["control_speed"] and (
                (player.position.x - old.x) * ball.velocity.x +
                (player.position.y - old.y) * ball.velocity.y < -1e-8
            ):
                continue
            keeper_shot = bool(flight and flight["kind"] == "shot" and player.player_id == 0 and
                               player.team_id != flight["source"][0] and
                               abs(player.position.x - goals(player.team_id)[0]) <= 18)
            radius = self.config["pickup_radius"] + (1 if keeper_shot else 0)
            contact = circle_contact(player.position, old, new, radius)
            if contact and contact[1] <= boundary_fraction:
                distance, fraction = contact
                candidates.append((fraction, distance, self.rng.random(), player, keeper_shot))
        for fraction, distance, _, player, keeper_shot in sorted(candidates, key=lambda c: c[:3]):
            flight = self.flight
            contact = Vec(old.x + (new.x - old.x) * fraction, old.y + (new.y - old.y) * fraction)
            if flight:
                flight.setdefault("attempted", set()).add(player.key)
            if keeper_shot:
                if flight["save_tried"]:
                    continue
                flight["save_tried"] = True
                probability = self.config["save_probability"]
                success = self.rng.random() < probability
                self._emit("save_attempt", team_id=player.team_id, agent_id=0,
                           probability=probability, success=success, ball_speed=speed)
                if not success:
                    continue
                self._emit("save", team_id=player.team_id, agent_id=0)
                self._capture(player, "save")
                return
            if flight and flight["kind"] == "shot" and speed > self.config["control_speed"]:
                if self._block_shot(player, contact, distance, speed):
                    return
                continue
            # Slow loose balls are controllable. Fast passes favor an expecting receiver;
            # opponents must intercept successfully and cannot reroll at every physics tick.
            if speed > self.config["control_speed"]:
                proximity = 1 - 0.5 * distance / self.config["pickup_radius"]
                if flight and flight["kind"] == "pass" and player.team_id == flight["source"][0]:
                    probability = (0.95 if player.key == flight["target"] else 0.8) * proximity
                else:
                    probability = (self.config["pass_interception_probability"] * proximity *
                                   min(1, math.sqrt(self.config["control_speed"] / speed)))
                success = self.rng.random() < probability
                self._emit("ball_control_attempt", team_id=player.team_id, agent_id=player.player_id,
                           probability=probability, success=success, ball_speed=speed)
                if not success:
                    continue
            self._capture(player, "interception" if flight and flight["kind"] == "pass" and
                          player.team_id != flight["source"][0] else "pickup")
            return
        ball.position = new
        drag = self.config["ball_drag_per_second"] ** self.dt
        ball.velocity = Vec(ball.velocity.x * drag, ball.velocity.y * drag)
        self._outcome(old, new)


def create_simulation(config):
    """Construct the one supported local football environment."""
    if config.get('physics_version', 'v2') != 'v2':
        raise ValueError('Only the current local physics v2 is supported')
    return Simulation(config)

"""Scripted opposition policy for the local environment, not a selectable agent backend."""
from football_lab.game.actions import command
from football_lab.game.state import goals


def scripted_action(state, team, pid):
    me = state.player(team, pid)
    own, opp = goals(team)
    ball = state.ball
    if me is None:
        return command("SET_STANCE", team, pid, stance=2 if pid < 2 else 0)
    teammates = [p for p in state.team(team) if p.player_id != pid]
    owns_ball = ball.holder == (team, pid)
    if owns_ball:
        if pid == 0:
            if teammates and abs(me.position.x - own) <= 18:
                target = min(teammates, key=lambda p: me.position.distance(p.position))
                return command("GK_DISTRIBUTE", team, pid, target_player_id=target.player_id, method="THROW")
        if pid >= 2 and abs(me.position.x - opp) < 25:
            return command("SHOOT", team, pid, aim_location="BL" if pid == 4 else "TR", power=0.8 if pid == 2 else 0.9)
        if pid <= 2:
            targets = [p for p in teammates if p.player_id in ((3, 4) if pid == 2 else (1, 2, 3, 4))]
            if targets:
                target = min(targets, key=lambda p: abs(p.position.x - opp) if pid == 2 else me.position.distance(p.position))
                return command("PASS", team, pid, target_player_id=target.player_id, type="GROUND")
        # Sample forwards stop at x=33, then shoot at next decision. Preserve role lanes.
        return command("MOVE_TO", team, pid, target_x=opp * 0.6, target_y=-8 if pid == 3 else 8, sprint=me.stamina > 0.25)
    opponents = state.team(1 - team)
    if pid == 1 and opponents:
        danger = min(opponents, key=lambda p: abs(p.position.x - own))
        if abs(danger.position.x - own) < 30:
            return command("MARK", team, pid, target_player_id=danger.player_id, tightness="TIGHT")
    our_ball = ball.holder is not None and ball.holder[0] == team
    if pid >= 3 and our_ball:
        return command("MOVE_TO", team, pid, target_x=opp * 0.5, target_y=-10 if pid == 3 else 10, sprint=me.stamina > 0.25)
    press = me.position.distance(ball.position) < 20 and not our_ball
    if press:
        return command("PRESS_BALL", team, pid, intensity=0.6 if pid == 2 else 0.7)
    x = [own * 0.9, own * 0.6, ball.position.x * 0.5, opp * 0.4, opp * 0.4][pid]
    y = [max(-10, min(10, ball.position.y * 0.5)), 0, ball.position.y * 0.3, -8, 8][pid]
    return command("MOVE_TO", team, pid, target_x=x, target_y=y, sprint=False)

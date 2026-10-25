import pytest
from football_lab.game.actions import command, validate
from football_lab.game.aws_adapter import parse
from test_state import payload


@pytest.mark.parametrize("action,category", [
    ({}, "syntax_invalid"),
    (command("FLY", 0, 2), "unsupported_command"),
    (command("MOVE_TO", 1, 2, target_x=0, target_y=0, sprint=False), "ownership_invalid"),
    (command("MOVE_TO", 0, 2, target_x=80, target_y=0, sprint=False), "invalid_parameters"),
    (command("MOVE_TO", 0, 2, target_x=0, target_y=0, sprint="true"), "invalid_parameters"),
    (command("PASS", 0, 2), "invalid_parameters"),
    (command("SHOOT", 0, 2, aim_location="CENTER", power=float("nan")), "invalid_parameters"),
    (command("GK_DISTRIBUTE", 0, 2, target_player_id=3, method="THROW"), "precondition_failed"),
    (command("RESET", 0, 2), "precondition_failed"),
    (command("PRESS_BALL", 0, 2, intensity=0.5), "valid"),
])
def test_validation(action, category):
    assert validate(action, 0, 2).category == category


def test_possession_preconditions():
    action = command("PASS", 0, 2, target_player_id=3, type="GROUND")
    assert validate(action, 0, 2, parse(payload(team=0))).valid
    assert not validate(action, 0, 2, parse(payload(team=1))).valid
    assert not validate(action, 0, 2, parse(payload())).valid

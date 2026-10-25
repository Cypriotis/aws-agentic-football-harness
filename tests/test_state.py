import json
import pytest
from football_lab.game.aws_adapter import parse, serialize, invocation_identity
from football_lab.game.state import goals


def payload(new=True, team=None):
    players = [{"agentId": f"agentId_{pid}", "teamCode": code, "position": {"x": 5}}
               if new else {"playerId": pid, "teamId": tid, "position": {"x": 5}}
               for tid, code in ((0, "hOmE"), (1, "AWAY")) for pid in range(5)]
    ball = {"isFree": False, "possessionAgentId" if new else "possessionPlayerId": "agentId_2" if new else 2}
    if team is not None:
        ball["possessionTeamId"] = team
    return {"players": players, "ball": ball}


@pytest.mark.parametrize("new", [False, True])
def test_formats_and_ambiguity(new):
    state = parse(payload(new))
    assert len(state.players) == 10
    assert state.ball.holder is None
    assert state.ball.possession_status == "unknown"
    assert state.warnings


@pytest.mark.parametrize("new", [False, True])
@pytest.mark.parametrize("team", [0, 1])
def test_possession_roundtrip(new, team):
    state = parse({"prompt": json.dumps({"gameState": payload(new, team)})})
    assert state.ball.holder == (team, 2)
    for fmt in ("old", "new"):
        back = parse(serialize(state, fmt))
        assert back.ball.holder == (team, 2)
    assert goals(team)[1] == (55 if team == 0 else -55)


def test_missing_fields():
    state = parse({})
    assert state.ball.possession_status == "free"
    assert state.score == {"home": 0, "away": 0}


def test_hasball_disambiguates():
    raw = payload()
    raw["players"][7]["hasBall"] = True
    assert parse(raw).ball.holder == (1, 2)


def test_isfree_authoritative():
    raw = payload(team=0)
    raw["ball"]["isFree"] = True
    assert parse(raw).ball.holder is None


def test_missing_team_is_not_home():
    with pytest.raises(ValueError):
        invocation_identity({"prompt": {"gameState": {}}}, 2)

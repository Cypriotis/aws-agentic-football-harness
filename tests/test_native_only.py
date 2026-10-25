"""Public entry points expose native AWS agents only."""
import subprocess
import sys
from fastapi.testclient import TestClient
import pytest
from football_lab.config import load
from football_lab.observability.api import create_app


def test_only_native_execution_api_is_published(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        paths = client.get('/openapi.json').json()['paths']
        assert {p for p in paths if p.startswith('/api/')} == {
            '/api/health', '/api/native', '/api/native/runs',
            '/api/native/runs/{run_id}', '/api/native/runs/{run_id}/control',
            '/api/native/runs/{run_id}/frames', '/api/native/runs/{run_id}/report',
        }
        assert client.get('/api/health').json()['implementation'] == 'aws-balanced-qwen'
        assert client.get('/api/runs').status_code == 404
        assert client.post('/api/native/runs', json={'model': 'qwen2.5:14b', 'backend': 'mock'}).status_code == 422


def test_environment_config_cannot_select_an_alternate_agent():
    assert set(load()) == {'name', 'team_id', 'simulation'}
    with pytest.raises(ValueError):
        load(overrides={'agents': {'midfielder': {'backend': 'deterministic'}}})


def test_cli_has_native_commands_and_no_old_agent_runner():
    result = subprocess.run([sys.executable, '-m', 'football_lab', '--help'], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert all(name in result.stdout for name in ('ui', 'run-match', 'evaluate', 'verify-match'))
    removed = subprocess.run([sys.executable, '-m', 'football_lab', 'test-agent'], capture_output=True, text=True, timeout=10)
    assert removed.returncode == 2

"""Read-only diagnostics for the native agent runtime."""
from importlib.metadata import PackageNotFoundError, version
import sys
from .aws_balanced.profile import settings
from .aws_balanced.runtime import check_ollama


def doctor():
    config = settings()
    packages = {}
    for name in ('strands-agents', 'bedrock-agentcore', 'ollama'):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    result = {'python': sys.executable, 'implementation': 'aws-balanced-qwen',
              'dependencies': packages, 'ollama': config['ollama'], 'ready': False}
    try:
        check_ollama({'settings': config})
        result['ready'] = all(packages.values())
    except Exception as exc:
        result['error'] = str(exc)
    return result

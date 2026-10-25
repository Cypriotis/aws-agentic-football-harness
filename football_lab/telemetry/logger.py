"""Fingerprint the local match environment; agent evidence is captured by the native runner."""
import hashlib
from pathlib import Path


def physics_fingerprint():
    root = Path(__file__).parents[1] / 'simulator'
    digest = hashlib.sha256()
    for filename in ('engine.py', 'physics.py', 'scenarios.py'):
        digest.update(filename.encode())
        digest.update((root / filename).read_bytes())
    return digest.hexdigest()

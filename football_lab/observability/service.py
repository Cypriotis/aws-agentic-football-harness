"""Own native jobs and their local artifact root. No database or background evaluators."""
from pathlib import Path
import threading


class Service:
    def __init__(self, results_root='results'):
        self.root = Path(results_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.jobs = {}
        self.lock = threading.RLock()
        self.native = None

    def close(self):
        if self.native is not None:
            self.native.close()

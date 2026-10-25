"""The sole model-provider replacement used by original AWS create_agent()."""
import json
import os
from pathlib import Path
from strands.models.ollama import OllamaModel


class QwenModel(OllamaModel):
    def __init__(self, model_id):
        if model_id not in {"us.amazon.nova-micro-v1:0", "us.amazon.nova-lite-v1:0", "us.amazon.nova-pro-v1:0"}:
            raise ValueError(f"Unexpected original AWS model assignment: {model_id}")
        path = os.environ.get("FOOTBALL_NATIVE_PROFILE")
        if not path:
            raise ValueError("Start native agents through python -m football_lab.aws_balanced")
        profile = json.loads((Path(path) / "profile.json").read_text(encoding="utf-8"))
        # The launcher validates the entire source overlay before importing it.
        config = profile["settings"]["ollama"]
        from football_lab.config import local_url
        endpoint = local_url(config["url"])
        name = config["model"]
        if not isinstance(name, str) or not name or "cloud" in name.lower() or "://" in name:
            raise ValueError("Only an installed local model may replace Nova")
        self.original_aws_model_id = model_id
        super().__init__(host=endpoint, model_id=name,
                         ollama_client_args={"trust_env": False, "follow_redirects": False})
        # No prompt/output rewrite, forced JSON mode, generation tuning, history reset,
        # custom Agent construction, SQLite memory, skills or command validation here.

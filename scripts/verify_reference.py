"""Verify preserved reference files against the pinned Git blob hashes, offline."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / "upstream/manifest.json").read_text(encoding="utf-8"))
failures = []
for entry in manifest["files"]:
    path = root / "upstream/aws-reference" / entry["path"]
    data = path.read_bytes()
    digest = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
    if digest != entry["git_blob_sha"]:
        failures.append(entry["path"])
print(json.dumps({"commit": manifest["commit"], "files": len(manifest["files"]), "mismatches": failures}, indent=2))
raise SystemExit(bool(failures))

# Native AWS Football Lab

Original AWS balanced football agents running local Qwen through Ollama, with a 5v5 simulator and browser UI for inspecting decisions and outcomes. The simulator is a local approximation of the AWS match environment.
<img width="1201" height="771" alt="image" src="https://github.com/user-attachments/assets/69f16c38-e546-408c-8b6f-d346757c3c97" />

## Setup

Requires Python 3.12+, Node.js 22.18+, Ollama, and an installed `qwen2.5:14b` model.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
npm.cmd --prefix dashboard ci
npm.cmd --prefix dashboard run build
.\.venv\Scripts\python.exe -m football_lab ui --port 8770
```

Open http://127.0.0.1:8770/native and select **5v5 simulated match**. Model settings are in `configs/aws_balanced_qwen.yaml`.

## Commands

```powershell
.\.venv\Scripts\python.exe -m football_lab doctor
.\.venv\Scripts\python.exe -m football_lab evaluate --repeats 2
.\.venv\Scripts\python.exe -m football_lab run-match --duration 12
.\.venv\Scripts\python.exe -m football_lab verify-match results/RUN-ID
```

## Checks

```powershell
.\.venv\Scripts\python.exe scripts/verify_reference.py
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
npm.cmd --prefix dashboard run build
```

The AWS source is pinned in `upstream/manifest.json`. See [THIRD_PARTY.md](THIRD_PARTY.md) for attribution and licenses.

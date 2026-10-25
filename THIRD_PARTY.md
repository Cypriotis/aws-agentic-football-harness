# Third-party source

`upstream/aws-reference/` contains the AWS balanced team and shared library from [aws-samples/sample-ai-possibilities](https://github.com/aws-samples/sample-ai-possibilities/tree/02fadb4bc7748eeb0d2ab6db150edfbd5766f63f/agentic-football-sample-agents), commit `02fadb4bc7748eeb0d2ab6db150edfbd5766f63f`, under MIT No Attribution. The original license is retained at `upstream/aws-reference/LICENSE` and copied into each runtime. The runtime overlay replaces the Bedrock model import with the local Qwen provider.

The scripted opponent policy adapts the AWS fallback. Frontend dependency and font licenses are in `dashboard/public/THIRD-PARTY-LICENSES.txt` and included in the UI build.

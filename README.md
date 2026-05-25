# k8sense

A Claude Agent SDK-powered SRE for the homelab-k3s Kubernetes cluster.

## Status

**Phase 1 (current):** `k8sense ask "<question>"` — one-shot investigation CLI.

See [the design spec](docs/superpowers/specs/2026-05-25-k8sense-design.md) for the full 5-phase roadmap.

## Install (dev)

```bash
# With pip:
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Or with uv:
uv sync --extra dev

# Optional: only if you want to bill via the Anthropic API instead of using
# your existing Claude Code OAuth login:
cp .env.example .env  # add your ANTHROPIC_API_KEY
```

## Authentication

k8sense uses whatever auth your `claude` CLI is logged into by default
(typically OAuth via Claude Code, billed against your Claude subscription).
Set `ANTHROPIC_API_KEY` only if you want to bill against your Anthropic API
account instead.

## Usage

```bash
k8sense doctor                                       # check the environment
k8sense ask "list all namespaces"                    # ask a question
k8sense ask "is the argocd-server pod healthy?"      # investigate something specific
```

## Run the eval suite

Requires `ANTHROPIC_API_KEY` set and a reachable cluster:

```bash
python -m evals.runner
cat evals/report.md
```

## Run the test suite

```bash
pytest                                  # unit tests (no API spend)
K8SENSE_ALLOW_API=1 pytest -m smoke     # end-to-end against real cluster (charges API)
```

## Architecture

- `src/k8sense/cli.py` — argparse entrypoint, `ask` and `doctor` subcommands.
- `src/k8sense/agent.py` — assembles `ClaudeAgentOptions`, drives the streaming receive loop.
- `src/k8sense/tools/kubectl.py` — single read-only kubectl tool with verb allowlist.
- `src/k8sense/prompts/system.py` — system prompt + startup topology snapshot.
- `src/k8sense/render.py` — rich-based output rendering.
- `evals/` — fingerprint-based eval harness.
- `tests/` — unit, integration, smoke layers.

## License

This is currently a personal learning project — no public license assigned yet.

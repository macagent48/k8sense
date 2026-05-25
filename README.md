# k8sense

A Claude Agent SDK SRE for homelab k3s clusters.

## Phase 1 status

`k8sense ask "<question>"` — one-shot investigation CLI.

## Install (dev)

```bash
# With pip:
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Or with uv:
uv sync --extra dev

# Then in either case:
cp .env.example .env  # add your ANTHROPIC_API_KEY
```

## Usage

```bash
k8sense doctor                                  # check environment
k8sense ask "list all namespaces"               # ask a question
```

See `docs/superpowers/specs/2026-05-25-k8sense-design.md` for the full design.

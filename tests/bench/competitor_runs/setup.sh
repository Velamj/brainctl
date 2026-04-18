#!/usr/bin/env bash
# Set up the competitor-bench environment.
#
# Why a separate venv: brainctl ships against Python 3.11+ and runs
# happily on 3.14. Several competitor SDKs (Mem0, Letta, Cognee) pin
# transitive deps that don't yet ship 3.14 wheels (notably
# pydantic-core, tokenizers, and onnxruntime). Pin to 3.13 here so
# the install reliably resolves; 3.12 also works if 3.13 isn't on
# the box.
#
# Usage:
#   bash tests/bench/competitor_runs/setup.sh
#   source .venv-competitor-bench/bin/activate
#   export OPENAI_API_KEY=... MEM0_API_KEY=... ZEP_API_KEY=... LETTA_API_KEY=...
#   python -m tests.bench.competitor_runs.run_all --bench locomo --limit 5
#
# All version pins below are PyPI-verified as of 2026-04-16.

set -euo pipefail

VENV_DIR="${VENV_DIR:-.venv-competitor-bench}"
PYTHON_BIN="${PYTHON_BIN:-python3.13}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "ERROR: $PYTHON_BIN not found on PATH." >&2
  echo "Install python 3.13 (brew install python@3.13) or override PYTHON_BIN." >&2
  exit 1
fi

if [[ ! -d "$VENV_DIR" ]]; then
  echo "Creating venv at $VENV_DIR (python: $PYTHON_BIN)..."
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

python -m pip install --upgrade pip --quiet

echo "Installing brainctl (editable, from worktree root)..."
python -m pip install -e . --quiet

# ---------------------------------------------------------------------------
# Competitor SDK pins.
#
# Each block is independent so a single failing install (e.g. a missing
# system lib for one SDK) doesn't block the others — install best-effort
# and let run_all.py's CompetitorUnavailable path skip whatever is
# missing.
# ---------------------------------------------------------------------------

install_pkg() {
  local pkg="$1"
  echo "  -> $pkg"
  if ! python -m pip install "$pkg" --quiet; then
    echo "     WARN: $pkg failed to install — adapter will be skipped at runtime."
  fi
}

echo "Installing competitor SDKs (pinned versions)..."

# OpenAI baseline (vector_stores beta) — needs openai>=2.32.0.
install_pkg "openai==2.32.0"

# Mem0 hosted SDK.
install_pkg "mem0ai==2.0.0"

# Letta cloud client.
install_pkg "letta-client==1.10.3"

# Zep cloud SDK.
install_pkg "zep-cloud==3.20.0"

# Cognee — heavy install (LangChain + OpenAI + sqlite-vec). Allow
# a few minutes. Local-mode by default.
install_pkg "cognee==1.0.0"

# MemoryLake stub — install commented out until product-of-record is
# confirmed (see memorylake_adapter.py for the rationale).
# install_pkg "memorylake==0.1.0"

echo
echo "Done."
echo
echo "Required env vars at runtime (each adapter that lacks one is skipped):"
echo "  OPENAI_API_KEY    (used by: openai_memory, cognee, letta embedder)"
echo "  MEM0_API_KEY      (used by: mem0)"
echo "  LETTA_API_KEY     (used by: letta)"
echo "  ZEP_API_KEY       (used by: zep)"
echo
echo "Smoke run:"
echo "  python -m tests.bench.competitor_runs.run_all --bench locomo --limit 5"
echo
echo "Cost-gated full run (refuses to start if >\$5):"
echo "  python -m tests.bench.competitor_runs.run_all --bench locomo"

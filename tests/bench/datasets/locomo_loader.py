"""LOCOMO dataset loader with cache-first fetch.

Resolution order (first hit wins):
  1. ``tests/bench/locomo/locomo10.json`` — the in-tree committed copy
     (~2.7M, present in the repo today). Keeps the bench fully offline.
  2. ``tests/bench/datasets/locomo/locomo10.json`` — gitignored cache,
     populated on first download.
  3. Download from the snap-research/locomo GitHub release tag and
     write to (2).

The loader returns the raw JSON list (10 conversations) because the
existing eval code (``tests/bench/locomo_eval.py``) and the legacy
runner both consume that shape directly.

LOCOMO is MIT-licensed
(https://github.com/snap-research/locomo/blob/main/LICENSE).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional


# The in-tree committed dataset — the runner has historically relied on this.
# Keep using it so we don't force a download on every fresh clone / CI box.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_COMMITTED_PATH = _REPO_ROOT / "tests" / "bench" / "locomo" / "locomo10.json"

# Gitignored cache directory — created on first download.
_CACHE_DIR = Path(__file__).resolve().parent / "locomo"
_CACHE_PATH = _CACHE_DIR / "locomo10.json"

# Upstream sources, tried in order. The HF mirror is the most stable but
# requires network egress; the GitHub raw URL is the canonical drop.
_UPSTREAM_URLS = (
    # GitHub raw — pinned to the snap-research/locomo main branch.
    "https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json",
    # HF mirror as a fallback, no auth required.
    "https://huggingface.co/datasets/snap-stanford/locomo10/resolve/main/locomo10.json",
)


def _resolve_path() -> Optional[Path]:
    """Return the first existing dataset path, or None if all misses."""
    if _COMMITTED_PATH.exists():
        return _COMMITTED_PATH
    if _CACHE_PATH.exists():
        return _CACHE_PATH
    return None


def _download_to_cache(timeout: float = 60.0) -> Path:
    """Fetch the LOCOMO JSON from upstream and write to the gitignored cache.

    Raises ``RuntimeError`` if every upstream URL fails — the caller should
    fall back to skipping the bench (the test fixtures do this) rather than
    crashing CI.
    """
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    last_err: Optional[Exception] = None
    for url in _UPSTREAM_URLS:
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "brainctl-bench/1.0 (locomo loader)"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
            # Validate it parses before persisting — half-downloads would
            # otherwise poison the cache silently.
            json.loads(data)
            _CACHE_PATH.write_bytes(data)
            return _CACHE_PATH
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
            last_err = exc
            continue
    raise RuntimeError(
        f"Could not download LOCOMO from any upstream URL ({_UPSTREAM_URLS}): "
        f"{last_err!r}. Place the file manually at {_CACHE_PATH} or "
        f"{_COMMITTED_PATH}."
    )


def dataset_path(allow_download: Optional[bool] = None) -> Path:
    """Return a path to the LOCOMO JSON file, downloading if necessary.

    The ``BRAINCTL_BENCH_NO_DOWNLOAD=1`` env var disables the network
    fallback — useful in CI environments where the committed file should
    always be the source of truth.
    """
    hit = _resolve_path()
    if hit is not None:
        return hit
    if allow_download is None:
        allow_download = os.environ.get("BRAINCTL_BENCH_NO_DOWNLOAD") != "1"
    if not allow_download:
        raise FileNotFoundError(
            f"LOCOMO dataset not present at {_COMMITTED_PATH} or "
            f"{_CACHE_PATH}, and BRAINCTL_BENCH_NO_DOWNLOAD=1 disabled "
            f"the auto-download fallback."
        )
    return _download_to_cache()


def load(allow_download: Optional[bool] = None) -> List[Dict[str, Any]]:
    """Return the parsed LOCOMO dataset (list of 10 conversation records)."""
    p = dataset_path(allow_download=allow_download)
    return json.loads(p.read_text())


__all__ = ["dataset_path", "load"]

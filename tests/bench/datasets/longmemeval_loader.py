"""LongMemEval dataset loader (longmemeval_s split) with cache-first fetch.

We use the ``longmemeval_s_cleaned.json`` split (~277 MB) because it
contains the full distractor haystack — every entry carries ~50–500
sessions of which only a few are gold ``answer_session_ids``. That's
what makes the retrieval metrics (Hit@K, Recall@K, MRR, nDCG@K)
meaningful: there's something to *not* retrieve.

The smaller ``longmemeval_oracle.json`` (15.4 MB) split is *not* used
here — its haystack is exactly the gold session set (we verified
500/500 entries have ``set(haystack_session_ids) == set(answer_session_ids)``),
so retrieval scores against it are vacuously 1.0. Oracle is built for
end-to-end answer-quality eval (LLM-as-judge); we don't need it.

The much larger ``longmemeval_m`` (~2.7 GB) split carries even longer
haystacks (~500 sessions) and is excluded because the cache cost
outweighs the marginal information vs ``_s``.

Resolution order (first hit wins):
  1. ``tests/bench/datasets/longmemeval/longmemeval_s_cleaned.json`` —
     gitignored cache.
  2. Download from
     https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/
     and write to (1).

LongMemEval is MIT-licensed
(https://github.com/xiaowu0162/LongMemEval/blob/main/LICENSE).

Schema (per entry in the oracle split):
    {
      "question_id":          str,   # stable hash
      "question_type":        str,   # see CATEGORIES below
      "question":             str,
      "answer":               str | int | list,
      "question_date":        str ISO date,
      "haystack_session_ids": [str],
      "haystack_dates":       [str],
      "haystack_sessions":    [[ {role, content}, ... ], ...],
      "answer_session_ids":   [str],   # gold evidence
    }

We treat questions whose ``question_type`` is in
``RETRIEVAL_FRIENDLY_TYPES`` as in-scope: their gold answer is checkable
via string / fuzzy match against the conversation content. The remaining
types (``temporal-reasoning``, ``knowledge-update``) need LLM-as-judge
for accuracy and are skipped — but we still measure *retrieval* quality
on them when ``include_judge_only=True`` is passed, since gold session
IDs are present for every entry.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional


_CACHE_DIR = Path(__file__).resolve().parent / "longmemeval"
_CACHE_PATH = _CACHE_DIR / "longmemeval_s_cleaned.json"

# Backward-compat: an older version of this loader cached the oracle split
# at this path. We delete it on dataset_path() to prevent silent shadowing.
_LEGACY_ORACLE_PATH = _CACHE_DIR / "longmemeval_oracle.json"

_UPSTREAM_URL = (
    "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/"
    "resolve/main/longmemeval_s_cleaned.json"
)

# Question types whose gold `answer` is a single token / short string we
# can check by exact / fuzzy match against either the model's predicted
# answer or against the gold-evidence session content. Retrieval quality
# is what we report regardless; this list controls which axes we include
# in the headline overall metrics.
RETRIEVAL_FRIENDLY_TYPES = (
    "single-session-user",
    "single-session-assistant",
    "single-session-preference",
    "multi-session",
)

# Types that require LLM-as-judge to score accuracy. We still compute
# retrieval metrics (Hit@K, Recall@K, MRR) on them since the gold
# evidence session IDs are deterministic — only the answer score needs
# the judge. Off by default in the headline number.
JUDGE_ONLY_TYPES = (
    "temporal-reasoning",
    "knowledge-update",
)


def _download_to_cache(timeout: float = 600.0) -> Path:
    """Fetch the longmemeval_s split (~277 MB) into the gitignored cache.

    The 600s timeout accounts for the full file size on a slow link;
    typical LAN downloads finish in ~30s.
    """
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # Tidy up any old oracle cache so it doesn't shadow the active file.
    if _LEGACY_ORACLE_PATH.exists():
        try:
            _LEGACY_ORACLE_PATH.unlink()
        except OSError:
            pass
    req = urllib.request.Request(
        _UPSTREAM_URL,
        headers={"User-Agent": "brainctl-bench/1.0 (longmemeval loader)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(
            f"Could not download LongMemEval oracle split from "
            f"{_UPSTREAM_URL}: {exc!r}. Place the file manually at "
            f"{_CACHE_PATH}."
        ) from exc

    # Validate it parses before persisting.
    try:
        json.loads(data)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Downloaded LongMemEval payload from {_UPSTREAM_URL} is "
            f"not valid JSON: {exc!r}"
        ) from exc
    _CACHE_PATH.write_bytes(data)
    return _CACHE_PATH


def dataset_path(allow_download: Optional[bool] = None) -> Path:
    """Return a path to the LongMemEval _s split JSON, downloading if needed."""
    if _CACHE_PATH.exists():
        return _CACHE_PATH
    if allow_download is None:
        allow_download = os.environ.get("BRAINCTL_BENCH_NO_DOWNLOAD") != "1"
    if not allow_download:
        raise FileNotFoundError(
            f"LongMemEval _s split not present at {_CACHE_PATH}, and "
            f"BRAINCTL_BENCH_NO_DOWNLOAD=1 disabled the auto-download "
            f"fallback. Manual download: {_UPSTREAM_URL} (~277 MB)."
        )
    return _download_to_cache()


def load(
    allow_download: Optional[bool] = None,
    include_judge_only: bool = False,
) -> List[Dict[str, Any]]:
    """Return the parsed LongMemEval _s split entries.

    Args:
        allow_download: When False, never hit the network — raises
            ``FileNotFoundError`` if the cache is empty.
        include_judge_only: When True, return entries from every
            question_type. When False (default), filter down to
            ``RETRIEVAL_FRIENDLY_TYPES`` so the bench's headline number
            reflects axes that are evaluable without an LLM judge.
    """
    p = dataset_path(allow_download=allow_download)
    raw = json.loads(p.read_text())
    if include_judge_only:
        return raw
    return [
        e for e in raw
        if e.get("question_type") in RETRIEVAL_FRIENDLY_TYPES
    ]


__all__ = [
    "JUDGE_ONLY_TYPES",
    "RETRIEVAL_FRIENDLY_TYPES",
    "dataset_path",
    "load",
]

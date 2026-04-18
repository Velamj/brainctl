"""MemoryLake adapter — PROVISIONAL.

PyPI ``memorylake==0.1.0`` (PyPI 2026-04-16) exists but is an extremely
early-stage package. The "MemoryLake" name appears in two contexts in
the agent-memory landscape:

  1. The early PyPI package above (single-author, 0.1.0, no docs index).
  2. A "memory passport" / portable-memory marketing term used by
     Walrus/Sui-side projects (MemWal etc.) — NOT a Python SDK.

Until we can verify which (if any) is the "MemoryLake" the brainctl
competitive matrix is meant to compare against, this adapter raises
``CompetitorUnavailable("memorylake", reason="ambiguous-product")``.

The runner records that as a skipped row, the report calls it out
honestly, and Worker E knows to either substitute another competitor
or pull the row from the landing comparison table.

To wire the real product, replace this module's body with the
actual SDK calls and update setup.sh with the correct pin.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .common import CompetitorUnavailable


class MemoryLakeAdapter:
    name = "memorylake"
    pinned_version = "0.1.0"
    needs_api_key = False
    cost_per_1k_writes_usd = 0.0
    cost_per_1k_queries_usd = 0.0

    def __init__(self) -> None:
        raise CompetitorUnavailable(
            self.name,
            "ambiguous-product: PyPI memorylake==0.1.0 exists but lacks "
            "documented agent-memory API surface; the 'MemoryLake' "
            "competitor referenced in the brainctl matrix may be a "
            "different (Web3 'memory passport') product. Adapter "
            "intentionally stubbed until product-of-record is confirmed.",
        )

    # The remaining methods are unreachable but kept as protocol stubs
    # so the type signature matches CompetitorAdapter.

    def setup(self, tenant_id: str) -> None: ...
    def ingest(self, key: str, text: str, speaker: str = "", timestamp: str = "") -> None: ...
    def search(self, query: str, top_k: int) -> List[Dict[str, Any]]: return []
    def teardown(self) -> None: ...

"""brainctl retrieval eval harness.

Seeds a deterministic corpus into a throwaway brain.db, runs a graded
query set through the real `Brain.search` path (or the full `cmd_search`
pipeline), and reports P@k / Recall@k / MRR / nDCG@k so we can gate
regressions on the hybrid FTS5+vec blend.

Run directly with:
    python -m tests.bench.run
or via the CLI wrapper at bin/brainctl-bench.
"""

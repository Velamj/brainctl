"""External-benchmark dataset loaders.

Each loader exposes a ``load(...)`` callable that returns the parsed dataset
in a normalized shape and handles caching to a gitignored directory under
``tests/bench/datasets/<name>/``. The committed files in
``tests/bench/<name>/`` (when present) are preferred over a network fetch
so the bench keeps working offline and in CI.
"""

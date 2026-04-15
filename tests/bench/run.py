"""Entry point for the search-quality benchmark.

Examples:
    python -m tests.bench.run                 # print JSON report to stdout
    python -m tests.bench.run --update-baseline  # rewrite the committed baseline
    python -m tests.bench.run --check          # exit 1 on >2% regression
"""

from __future__ import annotations

import argparse
import json
import sys

from tests.bench.eval import (
    compare_to_baseline,
    load_baseline,
    run,
    save_baseline,
)


def main() -> int:
    p = argparse.ArgumentParser(description="brainctl search-quality benchmark")
    p.add_argument("--k", type=int, default=10,
                   help="top-k window for ranking metrics (default 10)")
    p.add_argument("--update-baseline", action="store_true",
                   help="rewrite tests/bench/baselines/search_quality.json with this run")
    p.add_argument("--check", action="store_true",
                   help="compare to baseline and exit non-zero on >2%% regression")
    p.add_argument("--rows", action="store_true",
                   help="include per-query rows in the JSON output")
    p.add_argument("--pipeline", choices=("cmd", "brain"), default="cmd",
                   help="search path: cmd (full hybrid CLI) or brain (FTS5 only)")
    args = p.parse_args()

    result = run(k=args.k, pipeline=args.pipeline)

    if args.update_baseline:
        save_baseline(result)
        print(f"wrote baseline to tests/bench/baselines/search_quality.json", file=sys.stderr)

    out = {
        "overall": result["overall"],
        "by_category": result["by_category"],
        "k": result["k"],
        "pipeline": result.get("pipeline", "cmd"),
    }
    if args.rows:
        out["rows"] = result["rows"]

    if args.check:
        base = load_baseline()
        if base is None:
            print("no baseline committed; run with --update-baseline first",
                  file=sys.stderr)
            return 2
        diff = compare_to_baseline(result, base)
        out["diff"] = diff
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0 if diff["ok"] else 1

    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

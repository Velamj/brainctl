# Legacy BrainCTL vs MemPalace comparison bundle

- Current repo commit: `c130fbd47174a0932822db2ef7fe64e18a74d30d`
- Historical reference source: `C:\Users\mario\Documents\GitHub\brainctl\benchmarks\results\full_compare_20260418_033425\summary.json`
- Historical source mode: `recovered summary bundle`
- Command: `benchmarks\compare_memory_engines.py --label prepush_smoke --longmemeval-limit 1 --locomo-limit 1 --membench-limit 1 --convomem-limit-per-category 1`

## Datasets

- LongMemEval: `C:\Users\mario\Documents\GitHub\LongMemEval\data\longmemeval_s_cleaned.json`
- LoCoMo: `C:\Users\mario\Documents\GitHub\locomo\data\locomo10.json`
- MemBench FirstAgent: `C:\Users\mario\Documents\GitHub\Membench\MemData\FirstAgent`
- ConvoMem cache: `C:\Users\mario\Documents\GitHub\mempalace\benchmarks\convomem_cache`

## What is measured now

- New BrainCTL reruns: LongMemEval `brain` and `cmd`, LoCoMo `cmd_session`, MemBench FirstAgent `cmd_turn`, and ConvoMem `cmd` coverage/status.
- Old BrainCTL and MemPalace are frozen historical reference series loaded from the recovered 2026-04-18 bundle.

## Blocked or partial runs

- membench old_brainctl cmd_turn: MemBench comparison is partial because ThirdAgent and noise-extended slices are not included.
- membench mempalace raw_turn: MemBench comparison is partial because ThirdAgent and noise-extended slices are not included.
- convomem old_brainctl cmd: Blocked while loading ConvoMem evidence data: <urlopen error [WinError 10054] An existing connection was forcibly closed by the remote host>
- convomem mempalace raw: Blocked while loading ConvoMem evidence data: <urlopen error [WinError 10054] An existing connection was forcibly closed by the remote host>
- membench new_brainctl cmd_turn: MemBench comparison is partial because ThirdAgent and noise-extended slices are not included.
- convomem new_brainctl cmd: Blocked while loading ConvoMem evidence data: HTTP Error 404: Not Found

## Output files

- `summary.json` and `summary.csv`: all series in one table.
- `comparison_table.json` and `comparison_table.csv`: long-form metric rows.
- `runs/*.json`: per-run payloads.
- `charts/*.png`: regenerated charts with old BrainCTL, new BrainCTL, and MemPalace together.

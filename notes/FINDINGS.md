# What we found running Q12 and Q5 across GPUs, and what changed

Branch `exchange-operator-v2` of [aocsa/sirius](https://github.com/aocsa/sirius/tree/exchange-operator-v2):
the original exchange work (9 commits) rebased onto `main` (DuckDB v2), plus the fixes below.
Design doc: `docs/super-sirius/exchange.md` in that branch.

## Fixed

### 1. A 120 s stall in every distributed Q5 (Bloom merge)
- **Symptom:** Q5 SF1000 on 2 GPUs took 122 s per run; the log said
  `filters not merged across ranks: ... 1 of 2 ranks in 120000 ms`.
- **Cause:** Q5's first join builds on the 1-row ASIA region. After the hash exchange one rank's
  build is empty, and it returned before the cross-rank Bloom all-gather, so its peer waited out
  the 2-minute timeout.
- **Fix** (`fix(dynamic_filter): every rank takes part in the Bloom merge`): an empty build or
  drained probe targets still merge (an empty Bloom of the agreed size), and a rank that never
  saw a whole build joins each key's all-gather with no filter. Never an empty Bloom from a rank
  that may hold keys. Q5 SF1000 on 2 GPUs, warm: 122 s -> 3.0 s (before the DuckDB v2 rebase; 1.84 s
on v2).

### 2. Tables with fewer files than ranks were refused (byte-range scans)
- **Symptom:** `a table of 1 files across 2 ranks; each rank needs one` (supplier, nation, region).
- **Fix** (`feat(exchange): split a table's local files across ranks by byte range`): a table's
  files are laid end to end and cut into equal byte spans per rank; a rank reads the row groups
  whose start offset is in its span (main's `parquet_byte_range`, from sirius-db/sirius#2043).
  Also balances by bytes instead of file count. Remote (S3) files keep whole-file striping.

### 3. Rebase onto DuckDB v2 main
- Settings are per session on main: the exchange settings now go through
  `get_operator_params(context, scope)` and `query_operator_options(context)`.
- Main's multi-partition Bloom accumulation (and its BUILD_FILTER operator) is turned off in
  distributed mode, so the cross-rank merge runs on whole-build publication only; RIGHT_SEMI/ANTI
  joins, which main plans with a BUILD_FILTER, work again.
- The v2 shell loads a local extension only with `-unsigned`.

### 4. GPU OOM at SF3000 on 2 GPUs, then a hang (spilling)
- **Symptom:** Q5 SF3000 ran on 1 GPU (spilling ~7-12 GB to host) but on 2 GPUs one rank died
  with `CUDA error ... failed to allocate 690 MB` and the other hung.
- **Cause A:** batches landed by the exchange sat in `EXCHANGE_SOURCE`'s private deque, outside
  any repository, so the spiller could not move them; they took whatever GPU memory was free.
  **Fix** (`fix(exchange): queue landed batches where they can be spilled`): they land in the
  source's port repository, which the downgrade executor spills to host/disk; each landing holds
  the query's work lease, so a late batch after the query ends is dropped, not written to freed
  memory.
- **Cause B:** `EXCHANGE_SINK` hash-split batches inside `sink()`, where an OOM fails the query
  (a sink may already have sent part of its input). **Fix** (`fix(exchange): split batches by
  rank in EXCHANGE_SINK::execute`): split in `execute()`, where an OOM reschedules the task, as
  PARTITION does; `sink()` only sends.
- Result: Q5 SF3000 on 2 GPUs runs: 30.6 s warm (1 GPU 38.1 s).

### 5. One rank's failure hung the others (fail-stop per query)
- **Symptom:** after any failure on one rank, its peers waited for its batches forever (sources),
  or for 2-10 minutes (Bloom merges, plan-shape check).
- **Fix** (`feat(exchange): a failed query is aborted on every rank`): an `abort_message` for the
  query goes to every peer; each fails that query's sources and all-gathers at once. Only that
  query's streams (`stream_id >> 20`) end; the transport and the next queries carry on.
  Test: `scripts/test_failstop.sh` (rank 0 fails in ~1.7 s with rank 1's error; the next query
  succeeds).

## Open (not fixed)

- `count(*)` without GROUP BY on N ranks returns one row per partial count (sum them for now).
- `exchange_service_` is destroyed after the memory manager at shutdown (one SIGSEGV seen in
  `cuMemFreeAsync` at exit, not reproduced since).
- S3 (`sirius_read_parquet`) and DuckDB-native table scans are not split across ranks, but the
  plan treats them as split: results would be multiplied by N. Use local parquet.
- MARK joins (`IN`/`NOT IN` subqueries) are hash-exchanged like inner joins; a NULL build key only
  reaches one rank, so `NOT IN` with NULLs can be wrong. Refuse or broadcast them.
- A rank that throws before reaching a Bloom merge (OOM building the Bloom) still makes its peers
  wait 2 minutes per key; the query then fails through the abort path.
- Delim joins, CTEs, TOP_N and COUNT(DISTINCT) partials are refused in distributed mode.

## Data pitfalls we hit

- A re-run of a generator wrapper that started with `rm -rf` of the dataset hit it mid-benchmark,
  deleting 10 of 15 orders files; the query then silently returned 1/3 of the counts.
  `gen_tpch.sh` now generates into `<dir>.tmp` and swaps it in only after it verifies; still,
  verify the dataset (`tools/verify_tpch.py`) right before benchmarking.
- Instance NVMe (`/opt/dlami/nvme`) is wiped on reboot on AWS DLAMI boxes.

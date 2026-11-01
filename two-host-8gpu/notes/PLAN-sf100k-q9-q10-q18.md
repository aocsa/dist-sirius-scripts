# SF100K Q9, Q10, Q18: plan, changes and validation

Goal: each query at SF100K takes at most 4x its SF30K time, cold and hot (3.5x until
2026-11-01). A run is stopped at the cap 4 x (cold + hot) + 60 s, and work moves to the next
query.

All runs: 2 hosts (gcn-17, gcn-18) x 4 GPUs = 8 ranks, NIXL over TCP, data at
`/scratch/prestouser/aocsa/tpch_sf100000`, config `p180-bp64-bloom8.yaml`,
`SIRIUS_PRIORITY_DOWNSTREAM_FIRST=1`. Run directories are under `runs/`.

| Query | SF30K cold / hot (s) | SF100K limit cold / hot (s) | Cap (s) |
|---|---|---|---|
| Q9  | 134.0 / 113.4 | 536 / 454 | 1049 |
| Q10 | 94.6 / 65.8   | 378 / 263 | 701  |
| Q18 | 97.4 / 68.6   | 390 / 274 | 724  |

## Status

| Query | Before (`sf100k-rest`) | Now | Ratio cold / hot | Status |
|---|---|---|---|---|
| Q9  | cold hit the 926 s cap | 430.6 / 374.0 s (`sf100k-lanes8b`) | 3.21x / 3.30x | pass |
| Q10 | cold hit the 621 s cap | 243.6 / 157.6 s (`sf100k-stats2`)  | 2.58x / 2.40x | pass |
| Q18 | cold hit the 641 s cap | 305.0 / 271.0 s (`sf100k-q18having`) | 3.13x / 3.95x | pass (hot by 3 s); work stopped |

All three pass under the 4x rule. Under the earlier 3.5x rule Q18 hot (needs ≤240 s) failed.
Q9 and Q18 ran with `STATS=1` and `SIRIUS_EXCHANGE_TCP_LANES=8`; Q10 with `STATS=1` only.

## Shared change 1: column statistics for the DuckDB planner (Q9, Q10)

**Analysis.** The views are multi-file `read_parquet`, and DuckDB 2.0 returns no column
statistics for a multi-file scan. The join-order estimator then falls back to "distinct
count = filtered row count" for every join key. That makes the 4e9 filtered `part` rows
the domain of `partkey`, so the selectivity of `p_name like '%yellow%'` is lost. Q9 and
Q10 joined the two biggest tables (lineitem and orders) first and spent their time on
that join's exchange and spill.

**Change.**
- `analyze_stats.sh DATA` reads every footer under DATA once. It writes
  `stats/<basename DATA>.csv` with the exact min, max and null count of every integer,
  decimal and date column (about 2 minutes at SF100K).
- `gen_sql.py` with `STATS=1` passes those stats to each view as
  `read_parquet(..., column_statistics := MAP {...})`.
- The estimator then gets min/max-based distinct counts. As a side effect, Sirius's
  decimal SUM narrowing (`enable_decimal_sum_stats_bound`) gets the bounds it needs.

**Plan effect (CPU EXPLAIN, `/tmp/aocsa_stats/plans`).** The join order changes in Q7,
Q8, Q9, Q10 and Q21 with key stats, and also in Q3 with full stats.
- Q9 now joins lineitem with the partsupp/part/supplier chain first and orders last.
- Q10 builds on lineitem joined with orders and probes with customer joined with nation.

## Shared change 2: leaked exchange shared memory (all queries)

**Analysis.** Q18 (`sf100k-stats2`) and Q9 with 8 lanes (`sf100k-lanes8`) failed at about
200 s with `TCP lane N failed ... NIXL_ERR_REMOTE_DISCONNECT`. A gcn-18 rank had been
SIGKILLed: the host log says `Killed`, with no timeout and no numa_guard line. Each
rank's exchange receive mirror `/dev/shm/sirius-exchange-<pid>-<rank>` (448 MiB) is
unlinked only by its destructor. Every rank killed by a cap had left its mirror behind,
about 45 GB per host, and the kernel OOM-killed a rank of the next run.

**Change.**
- Removed the leaked files: 100 on gcn-17, 105 on gcn-18.
- `xnode_tpch.sh`: new `sweep_shm`, run before every launch and from `reap()`. It
  deletes this user's mirrors whose pid is dead.
- Sirius `src/exchange/nixl_fabric.cpp`: `unlink_dead_mirrors()` runs before a rank
  creates its own mirror and unlinks this user's mirrors of dead pids. It is in the
  current build.

## Q9

| Step | Change | Run | Cold / hot (s) | Result |
|---|---|---|---|---|
| 0 | baseline | `sf100k-rest` | cap | fail |
| 1 | key stats | `sf100k-stats` | 450.5 / 424.5 | cold pass; the hot run was slowed by `analyze_stats.sh` running next to it |
| 2 | full stats | `sf100k-stats2` | 437.8 / 439.1 | cold pass, hot 3.87x: fail |
| 3 | + `SIRIUS_EXCHANGE_TCP_LANES=8` | `sf100k-lanes8` | - | killed by the shm leak (shared change 2) |
| 4 | step 3 again on clean shm | `sf100k-lanes8b` | **430.6 / 374.0** | **pass** |

**Analysis for step 3 (telemetry, `sf100k-stats2`).**
- The lineitem scan with its dynamic filter runs at about 8.7 GB/s compressed per rank,
  close to the GPFS limit.
- In the hot run, HASH_JOIN(45) waits until 802 s on its exchange: about 180 GB per rank
  at about 1 GB/s inbound, on a 400 Gb/s bond.
- So the change is more TCP lanes per rank (the default is 4).

**Validation.**
- 175 rows on both iterations.
- The result is identical (md5 of the rows) to `sf100k-stats2`.
- No error lines.

**Still open.** The final HASH_JOIN(50) runs its 46 partitions one after another from
host and disk (about 58 s). That is the next target if more margin is needed.

## Q10

| Step | Change | Run | Cold / hot (s) | Result |
|---|---|---|---|---|
| 0 | baseline | `sf100k-rest` | cap | fail |
| 1 | key stats | `sf100k-stats` | - | startup failure (rank 4 reached 6 of 7 peers), not a query problem |
| 2 | full stats | `sf100k-stats2` | **243.6 / 157.6** | **pass** |

**Validation.**
- 20 rows on both iterations, ordered by revenue descending.
- Sanity checks are OK.

## Q18

| Step | Change | Run | Cold / hot (s) | Result |
|---|---|---|---|---|
| 0 | baseline | `sf100k-rest` | cap | fail |
| 1 | full stats | `sf100k-stats2` | - | killed by the shm leak |
| 2 | full stats, 8 lanes, clean shm | `sf100k-lanes8b` | cap (641 s) | fail; spilled 1.87 TB, GPU OOM retries, 18-34 GB downgrade requests |
| 3a | complete-group HAVING pushdown, SF30K check | `sf30k-having3` | 67.4 / 59.4 (was 97.4 / 68.6) | correct: rows match the SF30K reference on both iterations |
| 3b | complete-group HAVING pushdown | `sf100k-q18having` | **305.0 / 271.0** | cold 3.13x; hot 3.95x: **pass under 4x**, fail under 3.5x |
| 4 | skip the cross-rank filter of a whole-table build | `sf100k-q18wholebuild` | - | built; not measured at SF100K (gcn-17 GPU memory leak, see Discoveries). Stopped: Q18 already passes under 4x |

**Analysis.**
- The subquery `select l_orderkey from lineitem group by l_orderkey having
  sum(l_quantity) > 300` groups all ~600B lineitem rows.
- lineitem is clustered by orderkey, so each partial aggregate already holds nearly
  every group whole. Still, the partial step emits about 150B groups, which are
  hash-exchanged (about 450 GB per rank at about 1 GB/s) and spilled before the merge.
- Almost all of those groups fail the HAVING.

**Change (Sirius).** Apply the HAVING to the partial groups that are provably whole,
before the exchange.
- **Plan time** (`src/planner/distributed_plan.cpp`), at a FILTER directly above
  MERGE_GROUP_BY:
  - The aggregates must be SUM/MIN/MAX/COUNT, with no AVG, DISTINCT, STDDEV, grouping
    sets or wide-integer sums. The partial types must equal the merge's.
  - One integer group key must come unchanged from a Parquet scan column.
  - Each rank reads its files' footers through the scan's metadata cache
    (`sirius_scan_manager::parquet_footer`, 16 threads). It checks that, within every
    file, the row-group [min, max] ranges of that key meet at most at their ends.
  - All ranks all-gather the file hulls and check the same across files.
  - If that holds, the aggregate gets a copy of the HAVING and this rank's sorted
    row-group bounds on the GPU (`complete_group_having`). The plan shape gets an `H`.
- **Run time:**
  - The Parquet scan marks its batches `whole_row_groups`. Projection, filter and
    row-preserving swaps keep the mark; cached or pinned chunks are never marked.
  - For a marked batch, `sirius_physical_grouped_aggregate` keeps a partial group only
    if at least one of these holds:
    - its key is a row-group bound;
    - its key is NULL;
    - it passes the HAVING.
  - A key strictly inside a row group's range appears in no other row group, and a row
    group is never split across batches, so that group's partial is final.
  - Groups that survive go through the exchange, merge and HAVING unchanged, so the
    result cannot change.
  - At query end the log reports `[complete-group HAVING] kept X of Y partial groups`.

**Validation of step 3.**
1. The proof holds. SF30K: proven over 1800 files. SF100K: proven over 6000 files, about
   1.31M row-group bounds per rank, 3.3–5.8 s of plan time for the footers.
2. The pruning works. Each rank keeps about 631K of 5.6B partial groups at SF30K, and
   about 2.1M of 18.75B at SF100K.
3. Correct: at SF30K the result matches the reference on both iterations. At SF100K both
   iterations return the same 100 rows.
4. SF100K timing: 305.0 / 271.0 s. That passes under 4x (limit 390 / 274 s).
5. Regression of Q9 and Q10 on this build: not run.

**What is left in Q18 hot (telemetry, `sf100k-q18having`).** In both iterations, 3 or 4
of the 8 ranks stall for 120 s:
- `CONCAT(4)` builds the 65 GB per-rank customer table for `HASH_JOIN(27)`. It takes
  125.8 ± 0.2 s on those ranks and 5.8 s on the others.
- The 120 s is the merge timeout of the join's Bloom filter (`exchange all-gather ...:
  4 of 8 ranks in 120000 ms`), after which the rank publishes no filter.
- The slow ranks start their lineitem scan late (362 s instead of 315 s in the hot
  iteration). Every rank then waits in `EXCHANGE_SOURCE(8)` until about 473 s.
- Without the stall, hot would be roughly 100 s shorter.

**Step 4 change (Sirius `src/planner/distributed_plan.cpp`).**
- `merge_filters_across_ranks` cancels a join's dynamic filters when its build is a
  whole table (`whole_table`).
- A whole table here is a Parquet scan under only projections, partitions and concats,
  with no dynamic filter and table filters on the build join keys only.
- Q18's customer build qualifies: its only filter is `c_custkey <= 14999999999`, which
  DuckDB derived from the `o_custkey` statistics.
- A Bloom filter of every customer key drops no orders row, so the result can't change.
- The plan shape gets a `w`, and the log reports `[distributed plan] a join's build is a
  whole table: no dynamic filters`.

**Step 4 status.**
- SF30K regression: `sf30k-wholebuild`, 66.6 / 62.3 s, matches the reference. At SF30K
  the customer join publishes no filter, so the change is not exercised there.
- SF100K: not measured. Q18 work stopped once the rule became 4x.

## Discoveries

Findings from the logs, the quent telemetry and the code. Most of them apply beyond these
three queries.

### Data layout

- Orders (1740 files) and lineitem (6000 files) are range-partitioned by orderkey. Each
  file covers its own ascending range, and no two files overlap. For example,
  `orders.1` holds keys 1 to 344,826,915 and `lineitem.1` holds keys 1 to 99,997,472.
  Inside a file, the row groups (872 to 881 per file) are in key order too.
- The glob lists files in lexicographic order (`lineitem.1`, `.10`, `.100`, ...). A
  rank's contiguous share of the list is therefore scattered in key space, even though
  each file is clustered.
- Compression is SNAPPY with near-plain encoding. Q9's six lineitem columns are about
  2.6 GB per file, about 1.94 TB per rank.
- A lineitem footer is about 1.75 MB, so a rank's ~750 lineitem files carry about
  1.3 GB of footers.

### DuckDB planner (vendored v2.0)

- A multi-file `read_parquet` returns no column statistics
  (`MultiFileScanStatsInternal`).
- Without statistics, `ExtractGetStats` falls back to
  `DistinctCount(cardinality_after_filters)`, and `DomainEstimate` takes the minimum of
  those fallbacks. A filtered table's row count then becomes its join key's domain, and
  the filter's selectivity is lost. That is the root cause of the bad Q9 and Q10 join
  orders.
- The `column_statistics := MAP {...}` option of `read_parquet` fixes it without any
  engine change. With min/max-based distinct counts the domains are right.
- Statistics change plans beyond Q9 and Q10: Q3, Q7, Q8 and Q21, with minor changes in
  Q2 and Q22. They have not been regression-tested on the GPU yet.

### Exchange and network

- The exchange reaches only about 1 GB/s inbound per rank over TCP, on a 400 Gb/s bond
  (2x200G 802.3ad). For Q9 hot, the exchange, not the scan, was the bottleneck: about
  180 GB per rank.
- TCP lanes default to max(2, 16 / ranks per host), which is 4 here. Raising them to 8
  (`SIRIUS_EXCHANGE_TCP_LANES=8`) cut Q9 hot from 439 s to 374 s.
- The 1 GB/s ceiling is still far below the link rate. That gap is the next lever for
  any exchange-heavy query.

### Host memory and shared memory leak

- Each rank's exchange receive mirror (`/dev/shm/sirius-exchange-<pid>-<rank>`,
  448 MiB) is unlinked only in the fabric destructor. A rank killed at a cap leaves its
  mirror behind until reboot.
- About 200 leaked mirrors (~45 GB per host) built up and got ranks OOM-killed. The
  symptom looks like a network failure, not a memory problem:
  `TCP lane N failed ... NIXL_ERR_REMOTE_DISCONNECT` at about 200 s, and `Killed` in
  the host log.
- Free memory per node during runs is only about 30 GB (MemFree plus file pages,
  including ~23 GB of shared memory per NUMA node). `min_free_kbytes` is 16.5 GB and the
  GPFS daemon `mmfsd` holds 55 GB, so there is little slack for leaks.

### Diagnostics

- A SIGKILLed rank loses its buffered stdout, so its `rank_N.log` is empty. It also
  loses unflushed telemetry, so `aocsa_pipeops.py` finds no events. Use the Sirius log
  (`<run>/<q>/logs/<host>/sirius_<date>.log`) and the host logs instead.
- `Communication thread has thrown an exception: std::bad_alloc` also appears at normal
  shutdown, so it is not a failure signal.
- `spill_bytes` can read 0 even when telemetry shows HOST and DISK spill (Q9 tail). Don't
  trust it alone.
- Running `analyze_stats.sh` next to a benchmark slowed Q9 hot. A heavy footer scan must
  never share the hosts with a measured run.

### Q9 run profile (telemetry, full stats)

| Phase | Time (s) | Notes |
|---|---|---|
| Orders scan and shuffle | 0–50 | |
| Partsupp scan | 94–133 | |
| Lineitem scan with dynamic filter | 140–370 (cold), 534–724 (hot) | 3.36 TB decoded per rank, ~8.7 GB/s compressed, near the GPFS limit |
| HASH_JOIN(45) | lags until 802 (hot) | limited by the exchange |
| Final HASH_JOIN(50) | ~58 | 46 partitions run one after another from host and disk |

### Q18: why it fails, and what the engine allows

- **Why it fails.** The HAVING subquery's partial aggregate emits about 150B groups.
  Lineitem is clustered, so nearly all of them are already complete in their batch and
  fail `sum > 300`. They still go through the hash exchange and get spilled: 1.87 TB of
  spill, GPU OOM retries, and 18–34 GB downgrade requests.
- **Row groups are never split.** No page-level reads exist, and no operator splits a
  batch before the aggregate. Two consequences follow:
  - A row group belongs to exactly one rank (whichever byte range holds its start
    offset).
  - A row group lands whole in one scan batch. A batch can still mix files, and a file
    can span several batches.
- **Pinned or cached tables are the exception.** They are served in chunks that can cut
  a row group. Those are the only batches that can't carry the `whole_row_groups` mark.
- **Batches carry no metadata.** A `cucascade::data_batch` has no annotation slot, and
  projection and filter rebuild their output operator data. Per-batch row-group
  statistics would therefore not reach the aggregate. Hence the design:
  - a plan-time proof that a key lies in only one row group;
  - a one-bit `whole_row_groups` mark that projection and filter copy forward.
- **The proof only needs endpoints.** Suppose every file's row-group key ranges, and all
  files' key hulls, meet at most at their ends. Then a key that is not a row group's min
  or max lies inside exactly one row group, and its batch's partial is final. So the
  aggregate only needs the sorted set of this rank's row-group endpoints.
- **No allreduce.** The only collective is `exchange::all_gather` of small GPU tables.
  It must run once per query at plan time with a stream id every rank reserves the same
  way. Calling it per batch can deadlock.
- **Footer cost.** Bind parses only the first file's footer, so the proof reads the rest
  at plan time. It goes through the scan's metadata cache
  (`sirius_scan_manager::parquet_footer`), so the scan then gets cache hits instead of
  re-reading them.
- **Partial output is safe to filter.** The partial aggregate's output is
  `[keys..., aggregates...]`, the same layout as the merge's. Decimal SUM narrowing casts
  back to the declared DECIMAL128. So the HAVING can be evaluated on the partial when
  the aggregates are SUM, MIN, MAX or COUNT.

### Other

- Late materialization (`SIRIUS_EXP_LATE_MAT`) works only for GPU-pinned tables on a
  single node, so it is no option for Q10 or Q18 here.
- `groups_whole_on_rank` (skip the exchange when the input is already hashed on the
  group keys) is a different property from "groups whole in a batch". It does not apply
  to Q18.
- The Q14 regression came from UCX's CUDA memory hooks. Each `cuMemFreeAsync` walked
  UCX's registration cache interval tree (`ucs_rcache_unmapped_callback`). Fix:
  `UCX_MEM_CUDA_HOOK_MODE=none` and `UCX_MEMTYPE_CACHE=n`, set in
  `disable_ucx_cuda_hooks()`.
- `cudf::apply_boolean_mask` is deprecated in this cudf. The Q18 code calls
  `sirius::apply_retention_mask`, which must be qualified because ADL also finds the cudf
  overload.

### Cross-rank Bloom filter merge stalls under memory pressure (Q18, Q3)

- A distributed join's Bloom filter (up to 8 GiB) is merged with `exchange::all_gather`
  in 512 MiB chunks. It runs inside the build's CONCAT task, which still holds its inputs
  and its output (about 130 GB for Q18's customer build).
- A received batch lands only if the GPU or the host has room at that moment
  (`incoming_batch::land`). On a full rank the peers' chunks wait, the merge times out
  after 120 s, and the rank publishes no filter at all.
- The same timeout shows up in `sf100k-stats2` Q18 and in the `sf100k-q3bloom16` and
  `sf100k-q3max16` Q3 runs. In Q3 the filter is useful (customer is filtered by
  segment), so step 4 does not cover it.
- The general fix: let the merge's inputs land while the build holds its memory, or
  release the CONCAT inputs before the merge.

### DuckDB adds range filters from statistics

- With `STATS=1`, DuckDB adds `c_custkey <= 14999999999` to Q18's customer scan, taken
  from the `o_custkey` max.
- A build scan with "any table filter" is therefore no proof of a selective build. Step 4
  ignores filters on the join key itself, since DuckDB carries such ranges to the probe
  side through the join's equality anyway.

### Build, install and run pitfalls

- The extension loads `build/release/install/lib/libsirius.so.0` (RPATH), not
  `build/release/libsirius.so`. After each build, copy to a temp file in `install/lib`
  and `mv` it over `libsirius.so.0.0.0`. Never overwrite it in place while ranks run.
  The first two SF30K HAVING runs (`sf30k-having`, `sf30k-having2`) used the old library
  and validate nothing.
- `sirius_physical_operator::get_operator_id()` throws before
  `pipeline::assign_operator_ids` has run, which includes all of `distribute_plan`. A
  temporary log that called it made planning throw on one rank, and every rank's exchange
  setup then failed (`sf100k-q18wholebuild-crash`).
- `STATS=1` needs `stats/<data dir>.csv`, which exists only for SF100K. At SF30K
  `gen_sql.py` fails and writes an empty `q.sql`, and the ranks wait with nothing to run.
- A CPU `EXPLAIN` of the SF100K SQL with column statistics takes over 300 s.
- A bash script that is running can be replaced safely with an atomic rename (the running
  shell keeps the old file). An in-place edit is not safe.

### GPU memory held after killed ranks (gcn-17)

- After the crashed run, GPUs 1 and 2 on gcn-17 kept about 18 GB each with no process
  listed. It slowly fell to 18.4 and 16.6 GB over about 20 minutes. `nvidia-imex` runs on
  the hosts, and fabric-exported memory of killed ranks is the likely holder.
- A rank's pool (0.95 of the GPU, 187.7 GB) can then not be allocated at load time
  (`sirius_duckdb_cpp_init ... out_of_memory`). Even with `usage_limit_bytes: 160Gi`
  (`p180-bp64-bloom8-gpu160.yaml`) the 172 GB allocation failed.
- Resetting needs root: `nvidia-smi -r` reports insufficient permissions, and sudo
  needs a password. Before a run, check `nvidia-smi --query-gpu=memory.used` on both
  hosts, not just the process list.

## Other open items

- **Regression check:** rerun the queries whose plans change with `STATS=1` (Q3, Q7, Q8,
  Q21, plus minor changes in Q2 and Q22) and compare with `runs/sf100k-rest`. Also rerun
  Q9 and Q10 on the current build (HAVING pushdown and step 4 included).
- **GPUs 1 and 2 on gcn-17** need a reset, or must free on their own, before the next
  8-rank run.
- **Q18 step 4** is unmeasured at SF100K. Measure it if Q18 needs more margin than 3 s.
- **Bloom merge stall** (see Discoveries): affects Q3; not started.
- **Report:** done, `runs/sf100k-rest/REPORT.md`.

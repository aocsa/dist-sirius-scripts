# Scan optimization plan: two-phase scan, row-group skipping, scan sharing

TPC-H SF10000, 4 ranks, GPFS via io_uring O_DIRECT, code at `dist-plan-opt-rank-pin` (626fe147). Measured numbers come from `experiments-summary.md` and its rank-0 trace aggregates (`/tmp/aocsa/tpch22/dpo-best/profile/*/parsed/rank0/`); anything else is labeled an estimate.

**Baseline update (2026-10-25).** Benchmark these items with the new recommended config: `direct-io.yaml` plus `memory.host.block_size: 4194304`, 210Gi, 4 ranks.
- That config makes all 22 queries 2.67× faster cold and 1.34× faster warm.
- Scans now take a smaller share of each query, so re-check the 1a split (fetch vs decode) under it before relying on the older profile numbers below, which were taken with 1 MiB blocks.

New baselines from the 22-query sweep (cold / warm, s):

| Q | 4 MiB baseline |
|---|---|
| Q12 | 42.4 / 20.6 |
| Q17 | 39.4 / 29.9 |
| Q18 | 56.3 / 36.6 |
| Q19 | 46.4 / 26.1 |
| Q21 | 69.9 / 53.2 |
| Q11 | 13.0 / 9.3 |
| Q2 | 11.8 / 7.7 |
| Q22 | 6.0 / 4.6 |

## What the code and data actually show

| Claim | Status |
|---|---|
| Bloom filters run post-decode | Confirmed: `src/op/scan/sirius_physical_dynamic_filter.cpp:52-105`; `DYNAMIC_FILTER` wraps the scan at `src/planner/sirius_physical_plan_generator.cpp:803-811`. |
| Only zone maps reach the reader | Confirmed. The zone map implements `sirius_ast_lowerable` (`src/op/dynamic_filter/sirius_dynamic_filter.hpp:109-111`), the Bloom filter does not (`:345-347`). Merge at `src/op/scan/dynamic_filter_merge.cpp:311-333`, snapshot and `set_filter` at `src/op/scan/parquet_gpu_ingestible.cpp:1790-1806`. Off by default (`src/sirius_config.hpp:215`), skipped when distributed (`src/op/dynamic_filter/dynamic_filter_publisher.cpp:1279`), Bloom-only when distributed (`:1326-1331`), OR-merged with `all_gather` (`:1030-1077`). |
| Static row-group pruning at prepare time | Confirmed (`parquet_gpu_ingestible.cpp:1276-1289, 1391-1398`); traces log `872 -> 872`: nothing pruned. |
| Local uring uses plain `read_parquet` | **Corrected location.** `uring_reactor.hpp:129` sets `prefers_bulk_io = false`, so the ingestible calls `cudf::io::read_parquet` inline (`parquet_gpu_ingestible.cpp:1884-1905`). `materialize_all_columns` (`src/op/scan/parquet_materialize.cpp:178-237`) is only reached by REST (`rest_reactor.hpp:250`). |
| Late materialization / fused filter only for pinned tables | Confirmed (`src/planner/late_mat_plan_pass.hpp:19`, `src/scan_manager/sirius_scan_manager.cpp:853-860`, `src/compression/simpatico_codegen/src/simpatico_codegen.cpp:296`). |
| No shared scan; CTE only when DuckDB plans one | Confirmed (`src/planner/sirius_plan_cte.cpp:20-58`). **Correction:** Q15 already gets a materialized CTE and scans lineitem once, and Q20 scans each table once (integ SF3000 plan logs). Q18 scans lineitem twice. |
| cuDF hybrid scan reader | **Available.** libcudf 26.08.01 (`pixi.toml:101,114`) ships `cudf::io::parquet::experimental::hybrid_scan_reader` and `hybrid_scan_multifile`, with `materialize_filter_columns` / `materialize_payload_columns` and chunked variants (`include/cudf/io/experimental/hybrid_scan.hpp:494-531, 575-631`). Sirius already uses it for footer pruning (`parquet_gpu_ingestible.cpp:1289`). **Limit:** it prunes payload data **pages**, not rows (`hybrid_scan.hpp:60-65`). |

Data layout, read from footers (`lineitem.100.parquet`, `orders.50.parquet`): each lineitem row group holds ~113.7k rows in 6 dictionary-encoded data pages of ~20k rows. It spans ~115k consecutive order keys (~28k orders) but the full ship-date range. An orders row group holds ~98k orders.

So cuDF's two-phase read would still decode almost every payload page (each keeps at least ~7 rows), and row groups can be skipped only when a build is very sparse.

## Item 1: Two-phase scan

**Goal.** Decode the filter and key columns, filter, then decode the payload columns only for surviving rows.

**Expected gain (estimate).** Ceiling = payload byte share × decode share of scan time. The decode share is unmeasured: Sirius's warm scans run at 16–24 GB/s, while a standalone replay of the same reads from cached blocks reaches 27–48 GB/s. Byte shares: `bench/dist-tpch/colsizes.json`; survivors: warm traces.

| Scan | Phase 1 | Payload (share of bytes) | Rows kept, rank 0 | Kept per 20k-row page |
|---|---|---|---|---|
| Q12 lineitem | shipmode, 3 dates | l_orderkey (6%) | 77.9M / 15.0B | ~104 |
| Q19 lineitem | shipmode, shipinstruct, quantity, partkey (Bloom) | extendedprice, discount (39%) | 5.58M / 15.0B | ~7 |
| Q21 l1 | suppkey (Bloom), receipt/commit dates | orderkey (5%) | 391M / 15.0B | ~520 |
| Q21 l2 | orderkey (Bloom) | suppkey (92%) | 865M / 15.0B | ~1,150 |

A page is skipped only if none of its rows survive (chance at most e^-7 here), so the cuDF reader alone saves almost no decode. A real gain needs row-skipping decode inside pages (for dictionary pages: parse the index runs, gather only survivors), which cuDF 26.08.01 lacks.

**Current behavior.** Every projected column is decoded; constant predicates go to the cuDF filter (`parquet_gpu_ingestible.cpp:1778-1788`); column-to-column predicates and Bloom run afterward as operators.

**Design.** Add a route in `materialize_metadata_to_table` (`parquet_gpu_ingestible.cpp:1737`), behind a flag that is off by default:

1. Fetch the filter-column chunks to the device, reusing `fetch_chunks` (`parquet_materialize.cpp:129-167`); uring supports device reads.
2. Run `materialize_filter_columns` with the static filter, including the column-to-column conjuncts, kept out of stats pruning.
3. Compute the Bloom mask on the key column and `boolean_mask_scatter` it into the row mask.
4. Skip the payload fetch for row groups with no survivors.
5. Run `materialize_payload_columns` (`use_data_page_mask::YES`) and assemble the output in `scan_plan` order.
6. Return `ROW_FILTERED`, with a scan receipt naming the bindings applied, so the endpoint gate does not record biased measurements (`docs/super-sirius/dynamic-filters.md`, "Scan receipts").

- **Excluded:** virtual columns, Iceberg deletes, `disable_filter_pushdown` splits, pinned tables. Rank-local, so distributed plans are unaffected.
- **Filter timing:** each split snapshots filters (`:1791`). In the traces every Bloom-filtered probe scan started after its build ended (Q12 orders, Q19 lineitem, Q21 l2/l3), so they would see the filter from the first split.
- **Memory:** phase 1 adds a 1-byte-per-row mask; keep the current reservation estimate (`docs/super-sirius/scan.md`) until history justifies lowering it.

**Risks:**

NULL comparisons must drop rows and null tests stay out of stats pruning; string Bloom keys are fingerprints (no false negatives); filter and payload tables must stay row-aligned across multi-file splits; the all-pruned path (`:1754-1760`) must still work.

**PRs:**

- 1a. Log per-split fetch-wait, decode and filter time. No behavior change.
- 1b. Device fetch of column-chunk ranges on the uring path (`read_parquet` on device buffers); results must stay identical.
- 1c. Two-phase read with static filters only.
- 1d. Bloom mask between the two phases, plus receipts.
- 1e. Only if 1a shows decode dominates: a decode that skips rows inside dictionary pages, either upstream in cuDF or as a Sirius kernel.

**Tests.** New `test/cpp/scan/test_parquet_two_phase.cpp` comparing against single-pass reads (NULLs, strings, DECIMAL128, page-boundary survivors, multi-file and all-pruned splits); receipts in `test_dynamic_filter_merge.cpp`; TPC-H SF1/SF10 flag on/off against DuckDB (`test/tpch_performance/compare_results.py`); the 4-rank answer check (Q18's known tie expected).

**Benchmark.** Q19 and Q21, with Q12 as control: off vs on, one run per arm, cold and warm, using 4 MiB host blocks. Baselines (cold / warm, s): Q12 42.4 / 20.6, Q19 46.4 / 26.1, Q21 69.9 / 53.2. The older 1 MiB numbers (Q12 118.6 / 32.4, Q19 131.3 / 38.2, Q21 210.0 / 82.4, traced) are no longer the reference.

## Item 2: Skip row groups on clustered keys

**Goal.** Skip reading and decoding lineitem/orders row groups whose order-key range contains no build key.

**Expected gain.** It depends on how sparse the build is. A lineitem row group covers ~28k orders, so it can be skipped only when, on average, fewer than about one build key falls in its range.

- **Q12:** the orders Bloom keeps 1.9% of orders (73.1M / 3.75B per rank), about 1,900 keys per orders row group, so nothing is skipped.
- **Q3, Q4, Q5, Q7–Q10, Q21:** their filters are on dates, segments or suppliers, which are uncorrelated with order key. The kept fraction should be similar or larger (estimate), so again no skips.
- **Q18:** the HAVING set is the only plausibly sparse build. The gain depends on its key count (open question). Baseline with 4 MiB blocks: 56.3 / 36.6 s.

A global min/max zone map would cover the whole domain for every query above, and a Bloom filter cannot answer range questions (pushing it into the reader is item 1d).

**Design.**

- **2a: zone maps in distributed plans.** `all_gather` each rank's min/max next to the Bloom merge in `merge_across_ranks`. This is cheap and correct, but expected to change nothing on TPC-H.
- **2b–2d: a mergeable key-range bitmap.** Build it for integer keys: about 2 MiB of bits over the key domain. The domain comes from the 2a min/max exchange, so every rank uses identical buckets. Ranks combine their bitmaps with OR, in the same way as the Bloom merge.
  - The bitmap has no false negatives.
  - At materialize time, before the fetch, each row group's footer min/max is checked against the bitmap.
  - A split left with no row groups takes the existing all-pruned path.

Every rank must issue the same gathers in the same order, or the query hangs. This is the contract at `dynamic_filter_publisher.cpp:1030-1035`.

**Risks.** Missing stats keep the row group; narrowed carriers use the recorded storage type; NULL keys set no bits; strings unsupported; splits read before publication don't benefit.

**PRs.** 2a distributed zone maps; 2b bitmap filter and publisher (single process); 2c cross-rank OR merge; 2d row-group pruning at materialize; 2e page-level pruning through item 1's row mask (~5k orders per page).

**Tests.** Bitmap units under `test/cpp/operator`; merge in the style of `test/cpp/exchange/test_exchange_collectives.cpp`; a sorted synthetic parquet scan asserting pruned row-group count and identical results; TPC-H against DuckDB.

**Benchmark.** Q18, with Q12 as a control to show no overhead. Off vs on, cold and warm.

## Item 3: Scan a table once

**Repeated scans** (plan logs): Q21 lineitem ×3; Q17 lineitem ×2; Q18 lineitem ×2; Q2 partsupp, supplier, nation and region ×2 each; Q11 partsupp, supplier and nation ×2 each; Q22 customer ×2.

**Current behavior.**

- Every `LogicalGet` becomes its own scan operator (`sirius_physical_plan_generator.cpp:792-811`).
- Delim joins are planned as DuckDB emits them (`src/planner/sirius_plan_delim_join.cpp`); the distributed version is at `distributed_plan.cpp:276-313`.

**The constraint.** A later scan's consumer usually waits on a build that needs the earlier scan's output, so sharing the scan means buffering the later consumer's rows:

| Pair | What must be buffered (measured, per rank) | Verdict |
|---|---|---|
| Q21 l1 + l2/l3 | All of l2's input: 15.0B rows, 183.75 GB | Not viable |
| Q21 l2 + l3 | l3 after l2's Bloom and receipt>commit: 608M rows, 7.45 GB | Viable |
| Q18 scan 1 + scan 2 | The whole second scan | Not viable; use item 2 |

**Expected gain.**

- **Q21:** folding l2 into l3 removes l2's 17.2 s warm scan. Since the shared scan still decodes l3's 4 columns, the net saving is about 15 s warm (estimate). That figure was measured with 1 MiB blocks; with 4 MiB blocks Q21 runs 53.2 s warm, so expect a proportionally smaller saving.
- **Up to the summary's ~40 s:** requires removing l1 too, i.e. rewriting EXISTS / NOT EXISTS as one grouped pass over lineitem. That is a separate follow-up.
- **Q17, Q2, Q11, Q22:** not profiled. Q17 runs 39.2 s warm in total.

**Design.** A plan pass before the distributed rewrite groups scans on the same table, file list and pin state, and shares them when:

- (a) the consumers can run at the same time (Q11's identical subtrees), or
- (b) the later consumer's filter is **dominated**: its build keys come from the earlier consumer's join output on the same column, through key-preserving operators (Q21 l2→l3, Q17, Q2).

A `SHARED_SCAN` reads the column union, pushes only filters common to all ports, and gives each port its own projection, residual filter and `DYNAMIC_FILTER`.

- **Buffering:** a port whose consumer isn't ready deposits into a spillable repository, so the downgrade executor can spill it and the scan never blocks.
- **Filters on a buffered port:** it applies the dominating filter at scan time, and its own filter when it drains.
- **Refusal rule:** skip sharing when the estimated buffer exceeds a budget. The estimate uses plan-only inputs, so all ranks decide the same way.
- **Distributed plans:** both ports read the same rank file share (`distributed_plan.cpp:526-556`) and keep their own `EXCHANGE_SINK`; exchange stream ids must be allocated identically on every rank.

**Risks.** A wrong dominance proof drops matching rows (wrong results), the worst risk in this plan; deadlock if a buffer can't spill; ranks diverging on the sharing decision.

**PRs.** 3a candidate-logging pass with planner tests, no behavior change; 3b multi-port scan, concurrent ports (Q11); 3c buffered ports; 3d dominance rule (Q21, Q17, Q2); 3e distributed wiring; 3f, separately, the Q21 semi/anti rewrite.

**Tests.** Planner tests (`test/cpp/planner`), including cases where dominance must be refused; port fan-out and buffering under memory pressure (`test/cpp/pipeline`); TPC-H against DuckDB and the 4-rank answer check.

**Benchmark.** Q21 (primary), Q17 and Q11: off vs on, cold and warm, using 4 MiB host blocks. Baselines (cold / warm, s): Q21 69.9 / 53.2, Q17 39.4 / 29.9, Q11 13.0 / 9.3.

## 1a result: fetch dominates (measured 2026-10-25)

**How it was measured.** The timing is uncommitted, in `/tmp/aocsa/wt/dpo-rank-pin`: `src/op/scan/scan_timing.hpp`, hooks in `sirius_datasource.cpp`, `parquet_gpu_ingestible.cpp` and `sirius_physical_dynamic_filter.cpp`. It is gated on `SIRIUS_SCAN_TIMING`. For each scan task, inside `read_parquet`:
- **fetch** runs until the task's last column-chunk read completes;
- **decode** is the rest, with a stream sync;
- **filter** is `post_filter_and_project` plus the Bloom `DYNAMIC_FILTER` operator, also synced.

Run: SF10000, 4 ranks, 4 MiB host blocks, 2 iterations. Times are task-seconds summed over all ranks; about 4 tasks run at once per GPU.

| Query | Iteration | Read | Fetch | Decode | Filters |
|---|---|---|---|---|---|
| Q12 | cold | 498 GB | 398 s (91%) | 40 s (9%) | 0 s |
| Q12 | warm | 556 GB | 222 s (83%) | 45 s (17%) | 1 s |
| Q19 | cold | 832 GB | 429 s (87%) | 63 s (13%) | 2 s |
| Q19 | warm | 910 GB | 242 s (77%) | 72 s (23%) | 2 s |
| Q21 | cold | 1,860 GB | 688 s (89%) | 77 s (10%) | 4 s |
| Q21 | warm | 2,100 GB | 467 s (83%) | 91 s (16%) | 5 s |

**Caveats:**
- Totals are logged every 5 s, so the last few seconds of each iteration may be missing. The shares are more reliable than the absolute numbers.
- The syncs slow the queries slightly: Q12 warm was 27.6 s here against about 21 s without timing.

### Shuffle time (measured 2026-10-25)

**How it was measured.** A second run, with the same queries and config, added shuffle timing to the same `SIRIUS_SCAN_TIMING` totals (uncommitted):
- **Route:** `EXCHANGE_SINK::execute`, which hash-partitions each batch by destination rank on the GPU (stream-synced task time).
- **Send blocked:** pipeline time inside `exchange::transport::send`, mostly waits on the transport's queued-bytes limit (back-pressure).
- **Transfer in flight:** wall time per rank while any outgoing batch is still being sent over NIXL, summed over the 4 ranks.
- **Post-shuffle operators:** `EXCHANGE_SOURCE`, `PARTITION` and `CONCAT` on the receiving side, from per-rank trace logs (`tools/log_analyzer`).

All time columns except "Transfer" are task-seconds summed over ranks, like the scan columns.

| Query | Iteration | Fetch | Decode | Filters | Shuffled | Route | Send blocked | Transfer (wall) | Post-shuffle ops |
|---|---|---|---|---|---|---|---|---|---|
| Q12 | cold | 468 s | 42 s | 1 s | 33 GB | 3.2 s | 1.1 s | 5.1 s | 0.1 s |
| Q12 | warm | 192 s | 46 s | 1 s | 33 GB | 2.6 s | 1.2 s | 4.2 s | 0.1 s |
| Q19 | cold | 465 s | 69 s | 2 s | 2 GB | 2.3 s | 0.1 s | 2.4 s | 0.1 s |
| Q19 | warm | 215 s | 65 s | 2 s | 2 GB | 1.7 s | 0.0 s | 1.5 s | 0.0 s |
| Q21 | cold | 642 s | 75 s | 4 s | 252 GB | 2.0 s | 2.5 s | 4.0 s | 4.2 s |
| Q21 | warm | 459 s | 91 s | 5 s | 268 GB | 2.0 s | 3.8 s | 5.1 s | 4.0 s |

- **Shuffling is 1–2% of task time.** Route, send blocked and post-shuffle operators together are 1.8–9.8 task-seconds, against 280–555 s for scans. For Q21 warm that's 9.8 of 565 s, or 1.7%.
- **The network transfer is fast.** Q21 moves 252–268 GB in 4–5 s of transfer wall time summed over 4 ranks: about 1–1.3 s per rank, or roughly 50 GB/s per rank over NVLink. Back-pressure waits stay under 4 task-seconds.
- **Caveats:** the trace logging and timing syncs slow this run (Q21 warm took 68.3 s against 53–54 s without them), and the fetch/decode figures differ a little from the first run. The receive-side copies run on the transport thread and are covered only by the sender's in-flight time.

**What it means for the plan:**
- **Shuffle is not a lever on these queries.** Even removing all of it would save about 2%.
- **Item 1 (two-phase scan) drops out for now.** Decode is only 9–23% of scan task time. Even a perfect row-skipping decode would save at most that, and cuDF's hybrid reader can't skip pages here anyway, because almost every page keeps rows.
- **Reading fewer bytes is the lever.** Item 3 (scan sharing) removes whole fetches: Q21's second lineitem scan is about a third of its read volume. It moves up to first.
- **Item 2 (row-group skipping)** would also cut fetch, but only where a build is sparse, so Q18 remains the only plausible case.
- **The I/O path itself** (`cold-read-plan.md`: the small-read cap and its tuning) stays the biggest lever for every query.

## Dependencies, order, open questions

**Dependencies.** 2e needs item 1's two-phase route; 1a's timings also size 2d. Item 3 removes Q21 l2, item 1's highest-payload case.

**Order:**

1. **1a (measure): done.** Fetch is 77–91% of scan task time and decode 9–23%.
2. **Item 3 (3a–3e)**: the only measured, mechanism-certain gain, and it cuts fetched bytes.
3. **2a–2d**: small and contained.
4. **1c–1e**: deferred, because decode doesn't dominate.

**Open questions:**

1. ~~How warm scan time splits between fetch and decode.~~ Answered: fetch 77–91%, decode 9–23%. Shuffling adds 1–2% of task time.
2. Q18's HAVING key count at SF10000.
3. **Resolved:** the cold-read penalty came from too many concurrent small O_DIRECT reads per rank. Setting `memory.host.block_size: 4194304` caps them and cut the 22-query cold total from 2,128 s to 797 s (see `experiments-summary.md` and `cold-read-plan.md`). Cold times still vary between runs, so judge these three items mainly on warm times.
4. Whether row-skipping decode belongs in cuDF.
5. The Q17 and Q2 dominance relationships need confirming with EXPLAIN.

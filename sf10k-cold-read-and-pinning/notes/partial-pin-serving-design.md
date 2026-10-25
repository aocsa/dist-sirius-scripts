# Design: serve a scan's pinned columns from the pin, the rest from disk

Status: draft for review, 2026-10-25. Base: `dist-plan-opt-rank-pin` (626fe147, worktree `/tmp/aocsa/wt/dpo-rank-pin`). Paths below are relative to that tree. Measurements and the pin-size model are in [pin-sf10k-plan.md §3b](pin-sf10k-plan.md).

## 1. Problem

A pinned table serves a scan only if the pin holds **every** column the scan reads. `cache_entry_info::can_serve_with_columns` (`src/scan_manager/sirius_scan_manager.cpp:2647`) returns an empty projection when one requested column is missing, and the scan then reads everything from disk.

At SF10000 on one host, that rule caps a pin-once server:
- The 22 queries read about 372 GB per rank of compressed columns.
- A rank can pin about 155 GB on the host, and about 45 GB more on the GPU before queries slow down (measured).
- Under the full-coverage rule, the best pin set gives about **341 s** warm for the 22 queries (set E measured 345 s). Pinning lineitem twice, once per tier, only reaches about 290 s, because shared columns are stored twice.

Two examples:
- Q14 reads `{l_partkey, l_extendedprice, l_discount, l_shipdate}`. Set E pins none of those combinations, so Q14 reads all four from disk.
- Q9 reads six lineitem columns. Pinning five of them buys nothing today.

**Goal.** A scan whose table is pinned with some of its columns reads those columns from the pin, and only the rest from disk. Each row of the result must be identical to an all-disk read.

**Expected gain [e]:** from `tools/pin_model4.py`, which assumes disk time scales with the bytes still read from disk.

| Pins per rank | 22 queries warm, today's rule | With partial serving |
|---|---|---|
| 155 GB, host only | 341 s | **239 s** |
| 155 GB host + 45 GB GPU | about 290 s (lineitem split across tiers) | **about 205 s** (201 s modelled, plus the measured GPU-pin cost) |

The proportional-disk assumption is optimistic for scans that read a few narrow columns from disk (§7, R4). Phase 1 measures it before anything else depends on it.

## 2. How serving works today

**Pinning** (`src/pin_table.cpp:255`, `materialize_pin_batches`):
- The pin drives the table's own ingestible: `create_batch_coalescer`, then decodes each coalesced batch with `materialize_metadata_to_table`, unfiltered (`:340-348`).
- It hands each batch, in batch order, to a sink that stores it as one chunk:
  - `host_chunks` for the host tier, each chunk either Simpatico-compressed or uncompressed;
  - `device_chunks` or `data_batches_by_column` for the GPU tier.
- A parquet batch is a `parquet_split_info`. Its `rg_slices` hold, per file, the file path and row-group indices (`src/op/scan/parquet_gpu_ingestible.hpp:213`, `row_group_metadata.hpp:46`).
- The coalescer packs row groups by a byte budget computed **over the pinned columns**. It also interleaves row groups across batches (`pin_table.cpp:296-299`).
- The pin keeps the chunks but drops each batch's `rg_slices`. The entry records only `num_rows`, per-chunk zone maps and the per-chunk storage matrix. The matching code says so: "Cached parquet batches have no per-row file provenance" (`sirius_scan_manager.cpp:2668`).

**Matching** (`try_match_cached_entry`, `sirius_scan_manager.cpp:3835-4041`):
- It walks every pinned entry and takes the first whose `can_serve_with_columns` is non-empty. That requires the same parquet files and byte ranges (the rank share) and a column superset.
- It then builds a zone-map survivor plan, which drops whole chunks whose min/max can't pass the scan's filter.
- It returns a `cached_assignment`: the operator, the entry, the column gather and the plan.

**Serving** (`prepare_for_query`, `sirius_scan_manager.cpp:1585-1950`):
- A matched operator skips the disk split provider entirely (`:1600-1610`).
- `make_provider_for_pinned_entry` builds a `databatch_provider`. The coalescer drains it in `process_cached_entries` (`load_balancing_scan_batch_coalescer.cpp:284`). Each chunk becomes a `scan_operator_input` with the row filter still pending.
- `post_filter_and_project` then applies the query's WHERE clause, the same as for disk batches.
- Optional extras on this path:
  - **decompression pushdown:** answer equality tests from dictionaries, and drop rows during decompression;
  - **late materialization:** deferral of whole chunks;
  - **MVCC masks:** for duckdb-native pins only.

**Plan time** (`src/planner/sirius_plan_get.cpp:355-375`, `:600-680`):
- The residency gate narrows a scan's column types to the pin's carriers only when `column_projection_for` covers every column.
- Any other scan plans native types. Pinned chunks stored narrower are widened to native as they are served; this path exists and is tested.

**Disk reads** (`parquet_gpu_ingestible.cpp`):
- The split provider prunes row groups with parquet statistics (`:1268-1290`, `filter_row_groups_with_stats`).
- `materialize_table` may push the row filter into the cuDF reader, which drops rows during decode (`:1866-1970`, `filter_state::ROW_FILTERED`).
- The low-level helper `materialize_parquet(sources, opts)` (`parquet_materialize.hpp:112`) decodes an explicit list of `(datasource, metadata, row_group_indices)` with given reader options.

## 3. The core problem: lining rows up

Pinned columns and disk columns can be joined side by side only if both halves hold the same rows in the same order. Three things break that today:

1. **Different batching.** A query's coalescer packs row groups by the byte budget of **its** columns. A pin's chunk was packed by the pinned columns, so chunk *k* and the query's batch *k* hold different row groups.
2. **Rows dropped on the disk side.** Row-group statistics pruning and reader-side filter pushdown both remove rows from the disk half that the pinned half still has. Decompression pushdown can do the same on the pinned half.
3. **No record of which rows a chunk holds** once the pin finishes.

**Decision: replay the pin's batching.** For a partially served scan, the disk half reads exactly the row groups of each surviving pinned chunk, in the same order, with no row filter and no statistics pruning. Each pinned chunk then pairs with one disk read of the missing columns over the same row groups. Both halves decode the same row groups in the same order with the same parquet reader, so their rows line up. The WHERE clause runs once, afterwards, on the joined batch.

Rejected alternatives:
- **Join on a key column, such as l_orderkey and l_linenumber.** It needs a GPU join for every batch, plus a unique key that scans don't otherwise read. Too costly.
- **Re-batch at query time and slice pinned chunks by row offset.** This needs per-row-group row offsets inside every compressed chunk, and partial decompression of a chunk. That's a much larger change to Simpatico serving. It remains possible later (§8, phase 3).
- **Make pins batch the way queries do.** Not possible: every query has a different byte budget.

## 4. Design

### 4.1 Record where each chunk came from (pin time)

Add to `pinned_entry` (`sirius_scan_manager.hpp:215`):

```cpp
/// Parquet pins: the row groups each chunk holds, in decode order, positional with the chunks.
/// Empty for duckdb-native pins and for entries pinned before this field existed.
std::vector<pinned_chunk_source> chunk_sources;

struct pinned_chunk_source {           // src/pin_table.hpp
  struct file_row_groups { std::string path; std::vector<cudf::size_type> row_groups; };
  std::vector<file_row_groups> files;  // the batch's rg_slices, same order
  std::size_t num_rows;                // the decoded chunk's row count
};
```

- **Capture:** `materialize_pin_batches::handle_batch` already holds the batch. Cast it to `parquet_split_info` and copy its `rg_slices` as `(canonical path, row_group_indices)`. The three pin drivers return the list next to their chunks, and `pin_table` passes it in `pinned_entry_metadata`.
  - **Path, not index:** `row_group_slice::file_index` is a position in DuckDB's bound file list, which for a rank share needn't match the entry's own `resolved_file_paths`. Phase 1 needs the path to open the file anyway.
  - **Size:** about 400 chunks per rank at SF10000, a few tens of KB per entry.
  - **Non-parquet pins:** any batch that isn't a parquet split (a duckdb-native pin) leaves the list empty.
- **Merge path:** only uncompressed GPU pins can merge, and that path already requires identical placement and per-chunk row counts. When both the entry and the new pin have sources, they must also be equal; otherwise the merge throws, because equal row counts can still be different row groups. An entry pinned without sources stays without them.
- **Validation:** `chunk_sources_problem(entry, sources)` checks one source per stored chunk, row counts summing to `entry.num_rows`, and every path being one of the entry's files. It runs at insert (`adopt_chunk_sources`). Failing sources are dropped with a warning, so the entry still serves as today. It deliberately does not run in `validate_pinned_entry_for_serving`, where a throw would turn a fully served scan into a disk read.
- **Status: implemented 2026-10-25** in `/tmp/aocsa/wt/dpo-rank-pin`, uncommitted; patch in commit `1af4e21b` on branch `perf/sf10k-pinned-repro`. Test: `test/cpp/integration/test_pin_table_chunk_sources.cpp` (`[chunk_sources]`, 118 assertions). `[pin_table]` (46 cases) and `[scan_manager]` (186 cases) still pass.

### 4.2 Match a scan to a pin that holds part of its columns (prepare time)

Add a second, partial matcher, tried only when no entry covers the scan fully:

```cpp
struct partial_cover {
  std::vector<std::size_t> pinned_slots;   // positions in entry.cache_info.column_ids, in the scan's materialized order
  duckdb::vector<duckdb::ColumnIndex> disk_columns;  // the scan's columns the entry lacks
};
std::optional<partial_cover> cache_entry_info::partial_cover_for(const ingestible_table_info&) const;
```

- **Identity:** the same as today, `matches_parquet_files` with the same files and byte ranges.
- **Coverage:** at least one requested column is pinned.
- **Choosing an entry:** pick the one whose pinned columns hold the most Parquet bytes of this scan (column sizes from the footer). Decline when the share is below `pin_partial_min_fraction` (default 0.25). A small share costs a disk read per chunk and saves little.

**Phase 1 is deliberately narrow:**
- parquet pins only; duckdb-native and MVCC pins keep the full-coverage rule;
- one entry plus disk; two pins on two tiers are phase 2;
- no hive-partition columns and no user virtual columns, since the current matcher already declines those;
- no late-materialization deferral and no decompression-pushdown row dropping. The equality-answer half of pushdown is also turned off, because it changes the served column's type.

`try_match_cached_entry` then returns a `cached_assignment` with a new `partial` member set.

### 4.3 Serve the joined chunks (query time)

A new split type and a new provider. They reuse the disk read path, so readahead, the io_uring reactors, the small-read cap and memory reservation all apply unchanged.

```cpp
/// One pinned chunk plus the disk read of the columns the pin lacks, for the same row groups.
class hybrid_split_info : public parquet_split_info {
  std::shared_ptr<const pinned_entry> entry;   // keeps the pin alive
  std::size_t chunk_index;
  std::vector<std::size_t> pinned_slots;
};
```

- **Split provider.** `hybrid_split_provider` replaces the disk `split_provider` for a partially served operator. For each chunk index in the zone-map survivor plan, it builds a `hybrid_split_info`:
  - `rg_slices` come from `entry.chunk_sources[k]`, with file metadata and datasources from the ingestible's open files;
  - reader options are a copy of the scan's, projected to the **disk columns only**, with **no filter**;
  - `disable_filter_pushdown = true`;
  - `set_column_chunk_byte_ranges` is computed for the disk columns only, so readahead and fadvise fetch just those column chunks.
  It does no statistics pruning; the survivor plan has already dropped whole chunks.
- **Materialize.** `parquet_gpu_ingestible::materialize_table`, for a `hybrid_split_info`:
  1. Decode the disk columns with `materialize_parquet(sources, opts)`.
  2. Fetch chunk *k*'s pinned columns from the entry. This uses the code the cached provider uses: `slice()` or `select_columns()` on a host chunk, then decompression; the device-chunk path on the GPU. Pinned chunks narrower than native widen to native.
  3. Check that both halves have `entry.chunk_sources[k].num_rows` rows. If not, throw: the operator falls back to an all-disk read of this scan (§6).
  4. Interleave the columns into the scan's materialized order and return `filter_state::UNFILTERED`. `post_filter_and_project` then applies the WHERE clause as it does for any disk batch.
- **Estimates.** `estimated_bytes()` is the disk columns' decoded estimate plus the pinned slots' decompressed size, from `column_storage`. The memory reservation covers both halves.
- **Plan time.** Unchanged: a partially served scan is "not fully pinned", so it already plans native types (`sirius_plan_get.cpp:365-366`). Add one log line and a profile counter, "served N of M columns from pin `<name>`", so runs show hits as `pin_hits` does today.

Why read inside `materialize_table` rather than in a joiner stage: the pinned fetch then runs on the decode task's thread and stream, next to the disk decode. It is pipelined by the existing coalescer and readahead, and needs no new queue or ordering logic, because a split carries its own chunk index.

### 4.3a As built (phase 1a–1b, 2026-10-25)

Built in `/tmp/aocsa/wt/dpo-rank-pin`, uncommitted. Phases 0 and 1 together are in branch `perf/sf10k-pinned-repro` on `aocsa/sirius` (commits `7c99ebe1` and `1af4e21b`). That patch also carries the 2 small-read-cap lines in `sirius_config.cpp`, which share the file.

**Where the join happens.** Phase 1 does **not** add a hybrid split to `materialize_table`. A disk split must pass the scan's split validation (footer approvals, profile dependencies), and a hybrid split would have had to fake those. Instead the join is a **data representation**, which the scan admits like any pinned chunk:

- **`partial_pin_chunk_representation`** (`src/scan_manager/partial_pin_serving.{hpp,cpp}`) holds chunk *k*'s pinned columns, projected the way the cached provider projects them but without decode-time row selection. It also holds a shared `partial_pin_scan`, which opens each file and resolves its footer once.
- **Its converter to `gpu_table_representation`**, registered in `converter_registry::initialize`, runs on the scan task's GPU and stream inside `scan_operator_input::prepare_for_processing`. It:
  1. converts the pinned half (host compressed or uncompressed, device compressed; uncompressed GPU columns are copied, because they are shared with other queries);
  2. decodes the missing columns of the chunk's recorded row groups with `materialize_parquet`, using default reader options with only those column names and no filter;
  3. checks that both halves hold `chunk_sources[k].num_rows` rows, and throws if not;
  4. interleaves the columns in the scan's materialized order.
- **`partial_pin_provider`** walks the zone-map survivors and hands out one such batch per chunk. The scan then treats it as resident: it is admitted like a cached chunk, carriers are normalized after projection, and `post_filter_and_project` applies the WHERE clause.
- **Matching:** `partial_cover_for` plus a final pass in `try_match_cached_entry`, which runs only when no entry covers the scan fully. It picks the entry with the largest pinned share of the decoded row width, estimated from DuckDB type sizes; strings count as 16 bytes. Hits log `assigned pinned entry '<name>' to operator <id> partly: N of M column(s) from the pin`, so `run_suite`'s `pin_hits` counts them.
- **What a partial scan skips:** decompression pushdown, late materialization and decode-time membership probes. The provider is built without them.
- **Switch:** `executor.scan_manager.pin_partial_serving` (default `false`) and `pin_partial_min_fraction` (default `0.25`).
- **Readahead:** none for the disk half, which reads inside the converter. Readahead is off by default for the local io_uring backend anyway, and the disk path also reads in materialize.

**Tests:** `test/cpp/integration/test_pin_partial_serving.cpp` (`[partial_pin]`, 2 cases, 106 assertions).
- On host and GPU pins of `{k, v}`, every query matches DuckDB on the CPU: a full cover (counter unchanged), a filter on a disk column (counter grows), a filter on a pinned column, two row-alignment checks, a group-by on a string disk column, and a row-level sample.
- With the switch off, the counter doesn't move.
- `[pin_table]` (48 cases), `[scan_manager]` (188) and `[chunk_sources]` still pass.

**Found along the way:** an integer-to-VARCHAR cast in a filter (`(k % 17)::VARCHAR`) fails on the GPU ("Unary cast type must be fixed-width") with or without pins. It's unrelated, so the test avoids it.

### 4.3b Phase 1d measurement, SF10000 (2026-10-25)

Setup: `/tmp/aocsa/tpch22/pin10k/partial/`, 4 ranks, `pin-E.yaml` with a 190Gi pool, 2 iterations; top 5 plus Q19, in the order 9, 21, 18, 7, 14, 19. Warm times in seconds:

| Q | Set E, partial off | Set E, partial on | Rerun, on | 155-GB model set, on | Model set's pinned share of the scan |
|---|---|---|---|---|---|
| Q9 | 29.6 | **20.0** (−32%) | 20.1 | **15.7** | 4 of 6 lineitem columns |
| Q21 | 4.9 | 5.2 | 5.2 | 14.8 | 2 of 4; set E covered Q21 fully |
| Q18 | 29.8 | **13.4** (−55%) | 13.7 | 16.0 | 1 of 2 |
| Q7 | 22.6 | **13.2** (−42%) | 13.5 | **7.8** | 4 of 5 |
| Q14 | 18.6 | 19.7 | 19.8 | **11.2** | 2 of 4; set E pins none |
| Q19 | 19.7 | 26.9 | 23.4 | 29.8 | 2 of 6 (share 0.25); set E pins none |
| **Total** | **125.2** | **98.4 (−21%)** | **95.6** | **95.3** | |

- **Answers:** all ok. Q18 differs from the reference only among tied rows; its ORDER BY columns match row for row in every arm.
- **Set E with partial serving on:** l_orderkey and l_suppkey now serve Q9, Q18 and Q7 partly. Those scans got 32–55% faster, with no other change.
- **Q19 is not a cost of partial serving.** It takes no partial hit in the set E arms. Run alone with Q14 it takes 23.3 s (on) and 23.9 s (off); its 19.7 s in the first off arm came from the queries before it. Q14 is within about 1 s either way.
- **Memory got easier, not harder.** The partial-on arm downgraded GPU→host 389 GB in 12 requests, against 653 GB in 582 requests off, with nothing to disk. Lowest free memory was 47 / 70 GB per node, against 37 / 54 GB.
- **The 155-GB set** (lineitem {orderkey, suppkey, extendedprice, discount}, orders {orderkey, custkey, orderdate, orderstatus}, part's small columns, partsupp, supplier; 143 GB per rank pinned):
  - Q9, Q7 and Q14 gain more.
  - Q21 loses its full cover: 4.9 → 14.8 s, still far below its 54 s from disk.
  - **Q19 gets slower when served partly (23.4 → 29.8 s).** Its disk half reads two string columns (l_shipinstruct, l_shipmode) unfiltered for every row. The plain disk path pushes Q19's filter into the reader and prunes row groups.
  - Net: the same total as set E plus partial serving.

**Conclusions:**
1. **Go.** Partial serving is a clear win when the pin holds a large share of a scan. It cut 21–24% from these six queries at the same pinned memory.
2. **The model is optimistic for low shares.** A 25% share lost time on a scan with selective filters on its disk columns. Raise `pin_partial_min_fraction` to about 0.4, or weigh the disk columns' filter selectivity: the pinned half can't use the scan's reader filter, so it decodes every row.
3. **Cheap next improvement:** let the disk half drop row groups that the reader's statistics prune, and drop the same chunks' rows on the pinned side. That is phase 3, row-group-level pruning inside a chunk.
4. **Phase 2 matters for pin choice.** A second lineitem pin would let set E's Q21 cover live next to the model set's columns.

### 4.3c All 22 queries, set E, partial serving at 0.4 (2026-10-25)

Run `suite_E22p40` (`/tmp/aocsa/tpch22/pin10k/`): set E's pins, `pin-E.yaml` plus `pin_partial_serving: true` and `pin_partial_min_fraction: 0.4`, a 190Gi pool, 2 iterations. Compared with set E's own 22-query run (`suite_E22`, same pins, partial serving off). Times in seconds.

| | Set E | Set E + partial (0.4) |
|---|---|---|
| 22 queries, warm | 345.4 | **309.3 (−10.5%)** |
| 22 queries, cold | 354.3 | **308.2 (−13%)** |
| Lowest free memory, node 0 / node 1 | 32 / 43 GB | 33 / 52 GB |

**Partly served scans** (from the log): Q18 (1 of 2 lineitem columns pinned), Q5 (2 of 4), Q12 (3 of 5), Q13's orders scan (2 of 3; o_comment from disk).

| Q | Set E warm | + partial warm | Change |
|---|---|---|---|
| Q18 | 28.6 | 11.2 | −61% |
| Q5 | 22.0 | 10.1 | −54% |
| Q13 | 13.9 | 9.2 | −34% |
| Q12 | 13.2 | 9.4 | −29% |

The other 18 queries take no partial hit. They are within run-to-run noise: −9% to +13%, with Q6 at +13% and Q17 at +9%, neither of which touches a pin. Answers: all ok; Q3 and Q18 differ only in tied rows (ORDER BY columns match row for row).

**The threshold.** Pinned shares from the lineitem types (l_orderkey BIGINT; l_partkey and l_suppkey INTEGER; decimals as 8 bytes; strings as 16):

| Scan | Pinned share | At 0.25 | At 0.4 |
|---|---|---|---|
| Q7 | 0.38 | served, −42% | not served |
| Q9 | 0.30 | served, −32% | not served |
| Q19, model set | 0.27 | served, +27% | not served |

Neither 0.25 nor 0.4 is right: **0.3** keeps Q7 and Q9 and drops Q19. Estimate [e]: about 290 s warm for the 22 queries, about 19 s less than at 0.4.

### 4.3d All 22 queries, set E, partial serving at 0.3 (2026-10-25)

Run `suite_E22p30`: same pins and config as §4.3c, with `pin_partial_min_fraction: 0.3`. All ranks completed and the memory guard never fired. Lowest free memory was 32 / 44 GB per node. Pinning took 51 s per rank.

| 22 queries | Set E | Partial, 0.4 | **Partial, 0.3** |
|---|---|---|---|
| Warm | 345.4 | 309.3 | **274.7 (−20.5%)** |
| Cold | 354.3 | 308.2 | **283.1 (−20.1%)** |

**Partly served at 0.3, warm:**

| Q | Set E | Partial, 0.3 | Change | Newly served at 0.3 |
|---|---|---|---|---|
| Q18 | 28.6 | 11.8 | −59% | |
| Q5 | 22.0 | 9.9 | −55% | |
| Q7 | 22.1 | 12.1 | −45% | yes |
| Q8 | 27.4 | 16.1 | −41% | yes |
| Q9 | 30.9 | 19.9 | −36% | yes |
| Q13 | 13.9 | 9.4 | −33% | |
| Q12 | 13.2 | 9.4 | −29% | |

- **The other 15 queries** are within −9% to +2% of set E.
- **Answers:** all ok. Q3 and Q18 differ only in tied rows (ORDER BY columns match row for row).
- **0.3 is the recommended default** for these pins. None of the scans it adds got slower.
- **Next steps:**
  1. Phase 2, several pins per table, so a pin set can cover Q21 fully and still hold the model set's columns.
  2. Phase 3, row-group pruning for the disk half, so low-share scans with selective filters, such as Q19, can be served too.

### 4.4 Phase 2: several pins, one per tier

Let `partial_cover` hold a list of `(entry, pinned_slots)`, plus the disk columns. Every entry must have identical `chunk_sources` (§4.1), so they share chunk indices. Pins of the same table with the same rank share and the same pinned-column byte budget produce identical batching, but the budget differs between column sets. So phase 2 needs pins to batch by a shared, column-independent rule: a fixed number of row groups per chunk, set by a `pin_table` argument such as `row_groups_per_chunk`. Then lineitem's columns can be split between a host pin and a GPU pin with nothing stored twice. The model's 205 s case needs this.

## 5. Correctness rules

1. **Both halves see every row of the chunk's row groups:**
   - the disk half: no statistics pruning, no reader filter, `disable_filter_pushdown = true`;
   - the pinned half: no row-dropping decompression pushdown.
2. **Row counts must match per chunk:** `chunk_sources[k].num_rows`, the pinned chunk's rows and the disk table's rows. A mismatch is a hard error, followed by an all-disk retry of the scan, never a partial result.
3. **Same order:** the disk half decodes `chunk_sources[k].files` in recorded order with the same reader, the hybrid or `read_parquet` route chosen by the same rule (`materialize_parquet`), which is how the pin decoded them.
4. **Same files:** the matcher keeps the existing identity check. A replicated (whole-file) pin never serves a rank-share scan, nor the reverse.
5. **Types:** pinned columns widen to native; disk columns are native. Distributed ranks plan the same native types, so `rank_agreed_carriers` doesn't come into play.

## 6. Failure handling

- **Unusable entry:** a pin whose `chunk_sources` is empty or fails validation never partially serves; the scan reads from disk as today.
- **Failure while serving:** a row-count mismatch or a decode error throws from `materialize_table`. The existing per-operator fallback in `try_match_cached_entry` (`:4019-4038`) covers prepare time only. Serve-time failures need the same rule as the cached provider: fail the query with a clear error in phase 1, and rely on the CPU fallback. A silent all-disk retry mid-scan would emit duplicate rows for chunks already served, so it is not attempted.
- **Off switch:** `SET pin_partial_serving = false` (default on once validated) restores today's behaviour exactly.

## 7. Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | **Silent wrong answers from misaligned rows.** | Per-chunk row-count check (rule 2). A debug setting `pin_partial_verify = true` also reads one pinned column from disk and compares it element by element. CI runs the validation suite with it on. |
| R2 | A disk-side filter path not listed here drops rows: reader AST filter, stats pruning, dynamic (bloom) filters attached at decode, `ROW_FILTERED_AND_PROJECTED` for hive scans. | Hybrid splits go through one `materialize_table` branch that sets no filter, and hive scans are excluded. Audit `decode_consumer()` attachments: the late-mat code notes that deferral withholds them, and hybrid scans must withhold them too. |
| R3 | Re-pinning appends columns (the merge path) with different batching. | `chunk_sources` equality is part of the merge check; on a mismatch, partial serving is off for that entry. |
| R4 | **Slower than modelled.** A hybrid chunk reads only a few narrow columns from disk: many small column-chunk reads, the case the small-read cap exists for. Disk time may not shrink in proportion to bytes. Pin batches can also be a poor size for the disk half. | Phase 1's first measurement is exactly this: Q14 and Q19 at SF10000 with the set E host pins plus a partial lineitem pin (§9). Go or no-go before phase 2. `pin_partial_min_fraction` keeps low-value matches on disk. |
| R5 | GPU memory: both halves of a chunk are live at once during the join. | The estimate covers both halves; fetch the pinned half after the disk decode, so peak is about the joined batch plus decode working memory. |
| R6 | Interaction with late materialization and decompression pushdown, which assume whole-chunk serving. | Both are off for hybrid scans in phase 1 (§4.2). |
| R7 | Phase 2's fixed row-group batching changes pin batch sizes for every pin. | Opt-in `pin_table` argument; the default stays byte-budget batching. |

## 8. Effort

One engineer who knows the scan manager; estimates include tests and review.

| Phase | Work | Estimate |
|---|---|---|
| 0 | `chunk_sources` capture, storage, merge check and validation; a test that decodes a pinned chunk's recorded row groups from disk and compares | 3–4 days |
| 1a | `partial_cover_for`, entry choice, `pin_partial_*` settings, prepare-time wiring, log and counter | 2–3 days |
| 1b | `hybrid_split_info`, `hybrid_split_provider`, the `materialize_table` branch, pinned fetch reuse, estimates and reservation | 6–8 days |
| 1c | Exclusions (late-mat, pushdown, decode consumers, hive, MVCC), failure paths, `pin_partial_verify` | 2–3 days |
| 1d | Measure at SF10000 (§9); tune `pin_partial_min_fraction` | 2 days |
| 2 | Several entries per scan, `row_groups_per_chunk` pin batching, host plus GPU split | 5–7 days |
| 3 (optional) | Row-group-level pruning inside a chunk: record per-row-group row offsets, slice both halves | 4–6 days |

Phase 0–1: about **3–4 weeks**. Phase 2: about 1–1.5 weeks more. Phase 0 can land on its own: the metadata costs nothing when unused.

## 9. Test plan

**Unit tests (Catch2, `test/cpp/`):**
1. **Provenance round trip:** pin a small multi-file parquet table, then read each chunk's `chunk_sources` row groups from disk with the same reader. Every pinned column must be equal element by element. Cover:
   - host compressed, host uncompressed, a mix of both, and GPU device chunks;
   - rank-share byte ranges;
   - files with several row groups per chunk, and chunks spanning files.
2. **Partial cover choice:** an entry missing one column gives `partial_cover` with the right slots; full coverage still takes the existing path; below `pin_partial_min_fraction` it declines; mismatched files or ranges decline.
3. **Hybrid join equality:** for random splits of a table's columns into pinned and disk halves, a hybrid scan returns the same rows as an all-disk scan, with and without a WHERE clause that statistics or pushdown would otherwise prune. Include a filter on a pinned column and one on a disk column.
4. **Zone-map pruning:** chunks pruned by the pin's zone maps are not read from disk, and the answer is unchanged.
5. **Failure paths:** a corrupted `chunk_sources` row count fails the query with the expected error and no partial output; `pin_partial_serving = false` gives today's plan and hit counts.
6. **Merge path:** re-pinning with extra columns keeps partial serving only when the batching matches.

**Integration (`test/cpp/integration/`, next to `test_pin_table_merge_columns.cpp`):** TPC-H SF1 with lineitem pinned as `{l_orderkey, l_suppkey, l_commitdate, l_receiptdate}` (set E's lineitem). Run all 22 queries with `pin_partial_verify = true` and `pin_partial_min_fraction = 0`. Answers must match unpinned. The log must show full hits for Q4 and Q21, partial hits for Q3, Q5, Q7–Q10, Q12, Q15, Q18 and Q20, and no hit for the lineitem scans of Q1, Q6, Q14, Q17 and Q19, which read none of those columns.

**Distributed:**
- SF3000, 4 ranks: set E's pins plus partial serving, all 22 queries; answers must match `dpo/sf3k-hostpin` (Q3's tie allowed).
- SF10000, the go/no-go measurement (R4), one run per arm, top 5 (Q9, Q21, Q18, Q7, Q14) plus Q19:
  - arm A: set E pins, today's rule (measured: Q14 18.7 s, Q9 30.0 s);
  - arm B: set E pins plus partial serving;
  - arm C: the model's best 155-GB host set for partial serving (`pin_model4.py`: ps_*, l_extendedprice, o_custkey, p_type, l_discount, p_size, o_orderkey, l_suppkey, o_orderdate, l_shipdate, l_orderkey, …).
  - **Pass:** arm C's top-5 warm total within 15% of the model's prediction for those queries, and answers ok.
- **Then all 22 at SF10000** with arm C: the model predicts 239 s against set E's 345 s.

## 10. Open questions

1. Does `materialize_parquet` pick the same route (hybrid reader or `read_parquet`) for a subset of columns as for the pinned columns? If not, are row orders still identical? Both read row groups in index order, so they should be; test 1 settles it.
2. Should the pinned half fetch on the decode stream, or on the provider's stream as the cached path does today? It affects overlap, not correctness.
3. Is `pin_partial_min_fraction` better expressed in bytes saved per chunk than as a share?
4. For phase 2, is a fixed `row_groups_per_chunk` good enough for compression ratios? Simpatico works per chunk, and smaller chunks compress slightly worse.

# Proposal: split an oversized concat-all build instead of failing (TPC-H Q21 at SF30000)

Status: **implemented and verified**: commit
[`aocsa/sirius@a4f9e541`](https://github.com/aocsa/sirius/commit/a4f9e54128562c62a937d429d98a7e02c83cf75f)
on branch [`perf/build-probe-chunked`](https://github.com/aocsa/sirius/tree/perf/build-probe-chunked).
See [Outcome](#outcome) at the end. The proposal text below is
unchanged.

## The failure

At SF30000 on 4 ranks, both runs of Q21 fail on every rank with:

```
Sirius GPU execution failed: CONCAT partition exceeds the cuDF row-count limit
```

The error comes from `sirius_physical_concat::plan_pull_for_partition`
(`src/op/sirius_physical_concat.cpp:62`), at task creation, before any GPU work, from the row
counts the batches already carry. It is not memory pressure: the nodes still had more than 300 GB
free, and the host pool peaked at 41 GiB per rank.

The operator that fails is the build-side CONCAT (id 53) of HASH_JOIN 57, the **MARK** join that
DuckDB makes from Q21's `EXISTS (select * from lineitem l2 ...)` after flattening the correlated
subquery (the LEFT_DELIM_JOIN). The chain that leads there:

1. **MARK joins get one partition per GPU.** `compute_hash_join_partition_strategy`
   (`src/op/sirius_physical_hash_join.cpp:851`) forces MARK into BUILD_PROBE mode. Each rank owns
   one GPU, so the join gets `min(natural, num_gpus) = 1` partition. The reason, stated in
   `sirius_physical_hash_join.hpp:115`: `build_has_null` must be the same for every build batch.
2. **BUILD_PROBE folds each partition's build into one batch.**
   `bind_partition_inputs` (`src/op/partition/consumer_partition_policy.cpp:81`) calls
   `build_concat->set_concat_all(true)`. Then
   `get_next_task_input_data_for_build_probe` (`sirius_physical_hash_join.cpp:1243`) requires
   exactly one build batch per partition, and `execute()` builds one `cudf::filtered_join` from it.
3. **One rank's whole EXISTS build side must therefore fit in one cuDF table**, which holds at most
   2^31−1 (2,147,483,647) rows.

**Rough size (estimate, not measured).** The delim keys come from l1:
`l_receiptdate > l_commitdate` (about 63%), orders with status `F` (about 49%), Saudi suppliers
(1/25). At SF30000 that is about 2.2 billion l1 rows. Each matching order has about 4 lineitems, so
the build side has about 9 billion rows across the cluster, or **about 2.2 billion per rank: just
over the limit.** At SF10000 the same build is about 0.7 billion rows per rank, so it never hit the
limit.

**No config key gets around it.**
- `hash_partition_bytes` and `max_build_hash_table_bytes` don't change the count, because the MARK
  join's partition count is forced.
- More ranks per GPU isn't an option.

## Proposed fix: when a concat-all group would pass the row limit, split it into chunks and build one table per chunk

You asked for a retry that splits the concat inputs and computes again. The overflow is found
before any work is done, so the retry costs nothing: CONCAT packs the partition into as few
batches as fit under the limit, instead of throwing. The join then builds one table per chunk and
probes each probe batch against every chunk. Every query that fits today takes exactly the code
path it takes now; the split only happens past the limit.

### 1. CONCAT: pack into row-bounded chunks instead of throwing

`src/op/sirius_physical_concat.cpp`

- **`plan_pull_for_partition`:**
  - For a concat-all group, stop throwing when the running row total would pass the limit.
  - Take the whole partition as one task input, as today.
  - Keep the throw only for one batch that alone exceeds the limit. A cuDF table can't hold that
    many rows, so no such batch can exist.
- **`execute()`:**
  - Pack the input batches greedily into groups of at most `row_limit` rows.
  - Concat each group with `gpu_merge_impl::concat`.
  - Return all of them in the one `partitioned_operator_data`; the output is already a vector of
    batches.
  - The check against the actual tables (for batches whose row counts were unknown) uses the same
    packing, so it no longer throws either.
- **`sink()`:** no change. It already pushes every output batch to the same partition.
- **Make the limit a parameter.** Add `operator_params.concat_max_rows`, default
  `std::numeric_limits<cudf::size_type>::max()`, so tests can trigger chunking with small tables.
  Reasonable chunk sizes are no larger than the limit; no new tuning is needed.

### 2. Hash join, BUILD_PROBE: a partition may hold several build chunks

`src/op/sirius_physical_hash_join.{hpp,cpp}`

**State.** `per_partition_build_state` gets a vector of chunks. Each chunk holds its own
`filtered_table`, `hash_table` or `distinct_hash_table`, `build_table` and cast-column holder. One
chunk is exactly today's layout.

**Scheduling** (`select_build_probe_action`, `snapshot_build_probe_slots`,
`get_next_task_input_data_for_build_probe`):
- Today the code asserts `repo->size(p) == 1`. Instead, a partition becomes schedulable once the
  build port's source pipeline (the CONCAT) has finished.
  - A concat-all CONCAT emits only after its own input has finished, and the join's build barrier
    is already `FULL`. So waiting for CONCAT to finish adds no delay; it only guarantees that every
    chunk has arrived.
- The build task pops all `repo->size(p)` chunks and builds one table per chunk, in a loop inside
  the one task, on the same GPU.
  - Chunks could instead be built in separate tasks. That only helps if a single build task can't
    reserve memory for all chunks at once; start with one task.
- The partition becomes `BUILT` only after every chunk is built, as today, with the single
  release-store after `stream.sync()`.

**Probe**, per join type, with K chunks:

| Join | Combine across chunks | Notes |
|---|---|---|
| MARK | A probe row is matched if any chunk matches it. Probe chunk 1, then probe only the still-unmatched rows against chunk 2, and so on. Pass the union of matched indices to `resolve_mark_join_result`. | `build_has_null` = OR over all chunks. Treat the build as empty only if every chunk is empty. Both values are known before any probe, because all chunks are built before `BUILT`. |
| SEMI | Same narrowing as MARK; output the union of matched rows, each row once. | Narrowing also stops a row from being output twice. |
| ANTI | Probe the rows still unmatched against each chunk in turn; output what is left at the end. | |
| INNER | Concatenate the per-chunk join results. | Every chunk is a disjoint set of build rows, so no duplicates. |
| LEFT | Per-chunk inner matches, plus the probe rows no chunk matched, padded with NULLs. | Most work; phase 2 unless needed. |

- **`set_build_has_null`.** Today it is a compare-and-swap that throws when two build batches
  disagree. With chunks it becomes an OR across the chunks of one build task. Its other callers
  (STANDARD and MIXED) keep the consistency check.
- **Teardown** (`DESTROYED`, `sirius_physical_hash_join.cpp:2338`): free every chunk's table.
- **Broadcast** (`_broadcast`) uses the same slot structure, so it gets chunking for free.

### 3. What stays as it is

- Concat-all on the STANDARD path:
  - the SEMI/ANTI/LEFT build side;
  - the RIGHT-family probe side;
  - OUTER;
  - the nested-loop join.

  These partition by bytes (`hash_partition_bytes`, 5 GiB), so they reach 2^31 rows only at under
  about 2.5 bytes per row. They keep the throw, with a message naming the operator and the row
  count so the next case is easy to place.
- How the planner partitions, the exchange, and DuckDB plans: none of them change.

## Cost and risk

- **Unaffected queries:** nothing changes. With K = 1 the code takes today's path; only the
  per-partition state type changes.
- **Q21 probe cost:** each probe batch probes up to K tables. Narrowing to unmatched rows means
  later chunks see fewer rows. For Q21 K is about 2, and the probe side (the delim LHS, about 0.5
  billion rows per rank) is smaller than the build.
- **GPU memory is the next risk, not the row limit.**
  - About 2.2 billion build rows of at least two int64 keys is at least 35 GB per rank, before the
    hash tables (filtered_join's tables are a few times the key bytes).
  - The MARK join skips the `max_build_hash_table_bytes` eligibility check, so nothing caps it, and
    the GPU has 189 GB.
  - Chunking doesn't make this worse (same total bytes), but it doesn't help either. If Q21 then
    fails on GPU OOM, the fix is the alternative below.

## Alternative considered: more hash partitions for MARK (prevent the overflow up front)

The planner could choose `num_partitions >= ceil(build_rows / limit)` for the BUILD_PROBE / MARK
build. `partition_sizing_input` already carries `sizing.rows`. It would also have to:
- make `build_has_null` and "build empty" join-wide: an OR over partitions, known before any probe,
  with probes held until every partition is built;
- hash-partition the probe side into the same K partitions.

**Pros:** each probe row probes one table, not K. Partitions are independent, so the downgrade
executor could spill idle partitions, which would also address the GPU memory risk above.

**Cons:**
- It changes the partition strategy and placement code that every MARK join goes through.
- It needs a barrier over all partitions before the first probe.
- It doesn't help when the row counts are unknown at planning time or one key is heavily skewed.

**Recommendation:** do the chunked build first. It is local to CONCAT and the BUILD_PROBE slot, it
costs nothing below the limit, and it also covers skew and unknown row counts. Do more partitions
for MARK as a follow-up only if Q21 at SF30000 then fails on GPU memory.

## How to test

1. **Unit tests** (Catch2, `test/cpp/`), with `concat_max_rows` set to a few rows so small tables
   split into 2–3 chunks:
   - MARK:
     - a NULL build key in only one chunk (the mark must be NULL for unmatched probe rows);
     - one empty chunk;
     - all chunks empty;
     - NULL probe keys.
   - SEMI and ANTI with a probe key that matches in two chunks: it must appear exactly once in
     SEMI, and not at all in ANTI.
   - INNER: compare with the unsplit result.
   - CONCAT: the packing respects the limit, and the batches keep their order within a partition.
2. **SF10000 correctness:**
   - Run Q21 with `concat_max_rows` set to 200M, which forces about 4 chunks per rank.
   - Compare with the existing SF10000 reference answer at
     `/tmp/aocsa/tpch22/dpo/sf10k/run-fix-orig/q21/exchange_rank_0.log` (`run_query.py --ref`).
   - Also run Q4, Q16, Q20, Q22, which use SEMI/ANTI/MARK, to check nothing else changed.
3. **SF30000:**
   - Q21, cold and warm, with the settled config.
   - Check for GPU OOM; if it hits, the alternative above is the next step.
   - Then the full 22-query run.

## Outcome

**The fix works.** Q21 completes at SF30000, and every check of its answers passes. The
alternative (more hash partitions for MARK) wasn't needed: GPU memory held.

### What was built

- **The patch:** [q21-chunked-build.patch](../patches/q21-chunked-build.patch), 11 files, the same
  change as commit `a4f9e541`, without the build worktree's unrelated scan-timing diagnostics.
- **Planned but not implemented:** LEFT joins with a split build. The join throws a clear error if
  one ever arrives in chunks.

**Built as proposed:**

- **`operator_params.build_chunk_max_rows`:** the most rows in one chunk. Default and maximum
  2,147,483,647; a lower value forces splits for testing.
- **CONCAT** (`sirius_physical_concat.*`):
  - With `set_build_split_allowed(true)`, a concat-all partition above cuDF's row limit is claimed
    instead of rejected.
  - `execute()` packs its batches greedily into row-bounded chunks; one-batch chunks are forwarded,
    and row order is kept.
  - `sink()` calls `note_build_chunked()` on the hash join before it pushes the first chunk.
  - Without permission, CONCAT behaves as before.
- **Binding** (`consumer_partition_policy.cpp`): the split is allowed only in BUILD_PROBE mode, and
  only for MARK, SEMI, ANTI and INNER (`build_probe_can_chunk()`).
- **Hash join** (`sirius_physical_hash_join.*`):
  - `per_partition_build_state` holds a vector of `build_chunk`s.
  - The build task builds one table per chunk, and the MARK `build_has_null` is the OR over them.
  - `probe_chunked_build()`:
    - **MARK, SEMI, ANTI:** OR each chunk's `semi_join` matches into one BOOL8 mask, then select
      matched rows (MARK, SEMI) or unmatched rows (ANTI). This is simpler than the narrowing in the
      proposal and gives the same result.
    - **INNER:** concatenates the per-chunk joins.

**Added during implementation:**

- **The join waits for all chunks.** Once a split is announced, no partition counts as having its
  build until the CONCAT pipeline has finished, so the build task always receives every chunk.
- **No partial runtime filter.** `note_build_chunked()` also clears `_build_arrives_whole`, so a
  chunk is never published as the whole build. Otherwise the filter would drop probe rows that
  match a later chunk.

### Evidence

1. **Unit tests** (`sirius_unittest`):
   - **New tests:**
     - `test_build_probe_chunked.cpp` (6 cases) runs a split build end to end through hint, claim,
       build and probe:
       - MARK: plain, a NULL key in one chunk only, and an empty chunk with a NULL probe key;
       - SEMI with a key in two chunks;
       - ANTI with the same build;
       - INNER with a key in two chunks.
     - Each case also checks that the join waits for the build producer.
     - `test_physical_concat.cpp` gets 2 cases: the packing, and claiming a partition above the
       limit.
   - **Pass:** `[build_probe]`, `[physical_concat]`, `[physical_mark_join]`,
     `[physical_partition]`, `[hash_join]` and `[physical_hash_join]`, every case.
   - **Two failures, not caused by this change:** two `[dynamic_filter]` cases fail on
     `REQUIRE(fault::available())`. They need the fault-injection helper that
     `scripts/run_unit_tests.py` preloads, and the binary run directly doesn't have it.
2. **SF10000 with forced splits.** Each answer was checked against the SF10000 reference run
   (`/tmp/aocsa/tpch22/dpo/sf10k/run-fix-orig`). Results are in [sf10k-forced-chunks.jsonl](../results/sf10k-forced-chunks.jsonl) and
   [sf10k-forced-chunks.txt](../results/sf10k-forced-chunks.txt).

   | Query | Cap | Chunks per rank (from the logs) | Cold / warm (s) | Answers |
   |---|---|---|---|---|
   | Q21 | 200M | EXISTS MARK: 4–5; NOT EXISTS MARK: 3 | 80.4 / 55.0 | match |
   | Q21 | 10M | EXISTS MARK: 12; NOT EXISTS MARK: 8 | 72.6 / 54.6 | match |
   | Q20 | 10M | join 44: 4 | 35.4 / 21.0 | match |
   | Q4 | 10M | no split (no BUILD_PROBE build that large) | 36.3 / 18.6 | match |
   | Q22 | 10M | no split | 10.3 / 4.7 | match |

   - **No slowdown:** Q21 ran as fast as the unsplit SF10000 run (79.8 / 54.1 s).
   - **Why chunks exceed the cap ratio:** at the 10M cap the chunk count is set by batch size. A
     batch above the cap becomes its own chunk.
3. **SF30000, Q21,** with the chosen config (180 GiB host pool, spill to /scratch). Results are in
   [sf30k-q21-chunked.jsonl](../results/sf30k-q21-chunked.jsonl) and
   [sf30k-q21-chunks.txt](../results/sf30k-q21-chunks.txt).

   | Cap | Chunks per rank | Cold / warm (s) | Rows | Answers |
   |---|---|---|---|---|
   | default (2^31−1) | the failing CONCAT 53 → MARK join 57: 2 | 211.6 / 165.0 | 100 | run 2 = run 1 |
   | 1B | join 57: 3; NOT EXISTS join 83: 2 | 217.0 / 163.7 | 100 | both runs equal the default-cap run |

   - **Different splits, identical answers:** different chunk boundaries, including splitting the
     second MARK join, give identical results. With no reference at SF30000, this is the strongest
     check available.
   - **Memory:** the GPU pool peaked at 169–172 GiB per rank (Q9 peaks at 175 GiB). The host pool
     peaked at 41 GiB per rank, and nothing spilled to disk.

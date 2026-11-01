# Plan: make TPC-H Q3, Q10 and Q13 scale to SF100000 on 8 GPUs

At SF100000 these three queries take about 20× their SF30000 time on 3.33× the data:

| Query | SF30000 cold (s) | SF100000 cold (s) | Ratio |
|---|---:|---:|---:|
| Q3 | 82.0 | 1572 | 19× |
| Q10 | 94.6 | 2395 | 25× |
| Q13 | 34.4 | 815 | 24× |

All three slow down for the same reason. A rank first collects everything shuffled to it, then
partitions and concatenates it, and only then runs the join or aggregate. At SF100000 that data
outgrows the GPU (188 GB) and the host pool (180 GiB). It then spills to GPFS at about 450 MB/s
per rank.

Every number below comes from the telemetry and logs of these runs:

- **Base:** `runs/sf100k/` (Q13).
- **Bloom sizing fix:** `runs/sf100k-bloomfix/` (Q3, Q10).
- **SF30000:** `runs/sf30k/`.

`pipes.py` gives each pipeline's time span; `batches.py` gives the bytes it produced.

## What the telemetry shows

### Q3: the Bloom filter is too small to filter anything

- **Lineitem received per rank:** 962 GB at SF100000, against 102 GB at SF30000.
  - The ship-date predicate alone leaves about 970 GB per rank (54% of 600 billion rows, 24 bytes
    each, over 8 ranks), so the filter removes almost nothing.
  - The filter would need about 14.6 billion keys. The 4 GiB filter (the per-GPU cap) holds
    about 2.3 bits per key, so nearly every bit is set.
  - At SF30000 the filter still let about 35% through.
- **Join output:** 25.5 GB per rank, so almost all of the 962 GB is thrown away after being
  spilled.
- **Time:** the lineitem phase runs from 58 s to 1117–1568 s depending on the rank. The join can
  only start once a rank's share of lineitem has been partitioned and concatenated.
- **Join mode:** the final join runs STANDARD, partitioned in 198 pieces.
  - BUILD_PROBE (one resident hash table per GPU, probe batches joined as they arrive) was refused.
  - The reason: the build side, 44 GB per rank, is above `max_build_probe_resident_bytes`. That
    budget is 8× the derived batch size (2.5% of GPU memory), about 37 GB.
  - The hash table estimate, 1.8 billion rows × 16 bytes = 29 GB, is within its own budget.

### Q10: customer's text columns travel through two shuffles

- **Wide customer rows:** customer's wide rows arrive at every rank, about 98 GB per rank at
  SF30000. Its build-side CONCAT reserves up to 233 GB at SF100000, more than a GPU holds.
  - Two of the eight ranks needed until about 1030 s to finish that CONCAT; the others finished at
    287–412 s.
  - Every rank's orders shuffle waits for the slowest rank, which loses about 620 s.
- **Retries:** 687 out-of-memory warnings on gcn-17 alone. CONCAT of the joined customer and orders
  rows took 1548 s of compute, against 2.3 s at SF30000, and CONCAT of the lineitem probe took
  717 s.
- **Bad estimates:** DuckDB estimated the 3-month `o_orderdate` range at 20% of orders. The real
  count is about 5.7 billion rows.
  - As a result, the 15-billion-row wide customer table became the build side of the
    customer-orders join.
  - The lineitem join's build side was estimated at 60 billion rows.

### Q13: all orders are held before a 16-second join

- **Orders received per rank:** 297 GB at SF100000, against 89 GB at SF30000. That's 18.5
  billion rows of `(o_custkey, o_orderkey)`, 16 bytes each.
- **Time:** partitioning them (110–476 s) and concatenating them (476–647 s) dominates. The join
  takes 16 s. The ranks then wait about 160 s for the slowest one.
- **Barrier:** the probe side waited for the whole exchange before partitioning. The code does
  this for RIGHT-family joins (the build side is preserved), which keeps the join out of
  BUILD_PROBE too.
- **Tiny batches:** the partition step wrote 127,856 batches averaging 2.3 MB, and each is spilled
  on its own.
- **Fused count-join:** declined at SF30000 too, because its histogram spans the whole key range,
  240 GB at SF100000. It is not what changed between the two scales.

## 1. In progress

### 1a. Q3: stream the probe side, BUILD_PROBE (config only)

- **Change:** raise `max_build_probe_resident_bytes` and `max_build_hash_table_bytes` to 64 GiB in
  a copy of `p180.yaml`.
- **Effect:** Q3's final join takes BUILD_PROBE. Its 44 GB build and 29 GB table stay resident, and
  lineitem is joined batch by batch as it arrives. Nothing is held, so nothing spills.
- **Risk:** other joins with builds of 37–64 GB per rank also switch, and keep up to 64 GB resident
  each. Check Q5, Q7, Q8, Q9, Q18 and Q21 for out-of-memory retries.

### 1b. Q3: a larger Bloom filter, merged in chunks (code)

The cross-rank merge sends each filter as one UINT32 column, which tops out at 8 GiB. It also
gathers all 8 ranks' copies at once: 8× the filter in temporary GPU memory.

- **Merge in chunks:** `merge_across_ranks` sends each filter in up to 64 chunks of at least
  512 MiB, each on its own exchange stream.
  - The first chunk carries the header. Every rank sees every header, so they all decide alike
    whether the rest follows.
  - Memory beyond the filter itself is 8 copies of one chunk.
  - **Status:** written, not yet built.
- **Supporting changes:**
  - `merge_from(words, first_word, count)` ORs one chunk at its offset.
  - The planner reserves `max_filter_keys × 64` stream ids per join.
  - `max_bloom_keys` goes above 2^31; the per-GPU cap is what bounds the size.
- **Config:** `max_dynamic_filter_bloom_bytes_per_gpu: 16Gi`. Q3 gets 9 bits per key, for about a
  1% false-positive rate.
  - The expected received lineitem is about 25 GB per rank, against 962 GB: the true matches plus
    about 1% of the date-filtered rows.
  - Together with 1a, the shuffle shrinks too, not just the materialization.
- **Cost:**
  - Each large join holds a filter up to the cap on every GPU while its probe runs.
  - Q10's lineitem join is planned for 60 billion keys and gets the full 16 GiB, though 5.7
    billion would need 11 GiB.
  - **Later:** size the filter from the actual build rows, gathered across ranks, not the plan.

**Expected for Q3:**
- **Limit:** the lineitem scan and shuffle, which take 240 s cold today.
- **Time:** about 300 s cold, about 4× SF30000.
- **Side effects:** Q3 at SF30000 also improves, and Q4, Q5, Q10 and Q21 get filters with fewer
  false positives.

### 1c. Q13: stop shuffling `o_orderkey` (code, small)

`count(o_orderkey)` counts the orders matched to each customer.

- **Rewrite:** when Parquet statistics show `o_orderkey` has no NULLs, `count(o_custkey)` on the
  orders side counts the same rows. The join key is NULL exactly where no order matched.
- **Mechanism:**
  - Rewrite the aggregate in the captured logical plan, in `sirius_optimizer_hook` after the copy.
  - Clear the join's projection maps.
  - Re-run DuckDB's `RemoveUnusedColumns`, so the orders scan stops reading `o_orderkey`.
- **Effect:** 297 GB per rank becomes 148 GB. That is below GPU plus host memory, so the disk
  spill should mostly go.
- **Expected:** a few times faster, not a full fix; the orders still pass through partition and
  concat.

## 2. Late materialization (Q10, and the wide outputs of Q2, Q18, Q20)

Q10 outputs 20 customers, but carries `c_name`, `c_address`, `c_phone` and `c_comment` (about
150 bytes per row) through:
- **The customer shuffle**, about 330 GB per rank at SF100000.
- **The customer-orders join, as its build side.**
- **The second shuffle, by `o_orderkey`**, one copy per order.
- **The lineitem join's build side.**
- **The final grouping.**

**Plan:**

1. **Carry only the keys:** the scan of the wide table produces `c_custkey`, `c_nationkey`,
   `c_acctbal`, and a row reference (file id and row number), not the text columns.
2. **Run the plan on narrow rows:** the joins and the grouping key on `c_custkey`, so the text
   columns' share of the grouping key falls away. This is valid when `c_custkey` determines the
   other customer columns, that is, when `c_custkey` is unique.
3. **Check uniqueness at runtime:** the join building on customer counts rows and distinct keys,
   which a hash table build does cheaply. A duplicate key fails the query back to the current
   plan. Parquet views declare no keys, so this check is what makes the rewrite safe.
4. **Fetch the text columns afterwards:** after the TOP-N, read the 20 references. Group them by
   file, read the row groups that hold them with a row selection, and join them back on rank 0.
5. **Planner rule:**
   - Applies to a GROUP BY that includes a column whose other grouped columns come from the same
     scan (customer's columns, grouped with `c_custkey`), under a TOP-N or LIMIT.
   - Columns used only in the output are deferred: not used in predicates or join keys, and not
     used in an aggregate.

**Expected for Q10:**
- **Shuffle and build:** customer's share drops about 10×, to about 30 GB per rank.
- **Memory:** no 233 GB build, no out-of-memory retries, and no 620 s wait for slow ranks.
- **Time:** about 3–4× SF30000, roughly 300–400 s cold.

**Also fix the build-side choice:** Q10's customer table should not be the build side.

- **Estimate from statistics:** DuckDB's default 20% for a range predicate came out at about 5×
  the real count. Parquet row-group min/max of `o_orderdate` gives about 3.6%.
- **Compare actual sizes:** alternatively, at partition time compare the measured sizes of both
  sides and swap build and probe for inner joins when the build is the larger. The measured sizes
  are already known at the FULL barrier, when `get_partition_strategy` runs.

## 3. Faster shuffles

These help every large query, Q3, Q9, Q10, Q13 and Q18 included.

### 3a. Partition on the way in

**Today:** a rank receives exchange batches, holds them, then a separate PARTITION pass reads them
all and writes them again into local hash partitions. A CONCAT pass then reads them a third time.

- **Q13:** the PARTITION pass alone took 110–476 s.
- **Q3:** the lineitem probe went through the same three passes.

**Plan:**
- **One pass at the sender:** the sender hashes rows once into ranks × local partitions, using the
  local seed for the second level.
- **No extra pass at the receiver:** the receiver deposits each slice straight into the join's
  partition repository, skipping the separate PARTITION.
- **Fewer copies:** two fewer passes over the data and one fewer materialized copy.
- **Partition count first:** each side's partition count must be known before sending. Ranks agree
  on it with a small all-gather of per-destination byte counts, taken once the senders' scans
  finish. Alternatively, fix it from the plan for both sides, as `partition_exchange` already does
  between build and probe.

### 3b. Coalesce small batches before they spill

- **Problem:** the receive side's slices are small. In Q13, 2096 received batches became 127,856
  partition pieces of 2.3 MB each, and each spilled to GPFS on its own.
- **Plan:**
  - **Merge before spilling:** concatenate the pieces of one partition into a batch of at least
    `concat_batch_bytes` before it leaves the GPU or host tier.
  - **Fewer files:** spill in large sequential files per partition, not one file per piece.

### 3c. Probe as data arrives, for RIGHT joins and aggregates

BUILD_PROBE already streams the probe side for inner, left and semi joins.

- **RIGHT and FULL joins:** a RIGHT join (Q13's preserved customer side) would need a bit per build
  row, set as probe batches match. Unmatched build rows are emitted once, at the end.
- **Aggregate after the join:** Q13's aggregate after the join also needs to merge into one running
  table per partition, rather than emitting a partial result per probe batch.
- **Q13's other route:** a distributed fused count-join. Shuffle orders by `custkey mod 8`, so each
  rank's keys are dense with stride 8. Its histogram is then 1.9 billion slots, about 15 GB with
  4-byte counts and an overflow check, and counts land as batches arrive.

### 3d. Smaller shuffled rows

- **Drop unneeded columns:** shuffle only the columns used after the exchange. 1c is one case;
  look for others with the per-pipeline byte counts.
- **Narrow keys across hosts:**
  - Keys and dates usually span less than their type's range: `o_custkey` is below 2^34 at
    SF100000, and dates fit in 16 bits.
  - Frame-of-reference encoding on the TCP lanes would cut cross-host bytes.
  - It would reuse the compressed-materialization carriers that already exist inside a rank.

### 3e. Skew and stragglers

- **Q3:** ranks finished their join between 1117 s and 1568 s.
- **Q10:** two ranks spent 750 s more than the others on one CONCAT.
- **Telemetry:** log each rank's received bytes per exchange, to tell data skew from memory
  pressure.
- **Admission:** when one rank's partition is far above the others', split it, as CONCAT already
  can for build sides over the cuDF row limit.

### 3f. Spill target

- **Current target:** GPFS, at about 450 MB/s per rank.
- **Local NVMe:** check whether the hosts have local NVMe. If they do, point `downgrade_root_dirs`
  at it.
- **Benefit:** this doesn't fix any query, but it makes every spill cheaper.

## Order of work and checks

| Step | Change | Check at SF30000 | Check at SF100000 |
|---|---|---|---|
| 1 | BUILD_PROBE budgets (1a) | Q3, Q5, Q7, Q8, Q21: same answers, no new out-of-memory retries | Q3 |
| 2 | Chunked Bloom merge and 16 GiB cap (1b) | Q3, Q4, Q10: same answers | Q3, Q4, Q5, Q10, Q21 |
| 3 | Drop `o_orderkey` in Q13 (1c) | Q13: `sum(custdist)` = 4,500,000,000 | Q13 |
| 4 | Late materialization (2) | Q10, Q2, Q18: same rows | Q10 |
| 5 | Partition on the way in, and batch coalescing (3a, 3b) | all 22 queries | Q13, Q18, Q9 |
| 6 | Streaming RIGHT joins, or a distributed count-join (3c) | Q13 | Q13 |

- **Hosts:** before each run, confirm both are quiet.
- **Per-query report:** time, sanity check, disk spill, host-pool peak, and pipeline spans from
  `pipes.py`.
- **Baseline:** compare with `runs/sf100k-bloomfix/REPORT.md`.

**Results (2026-10-31, rule: SF100000 at most 3.5x SF30000):**

| Query | SF30000 cold / hot | SF100000 cold / hot | Ratio | Run |
|---|---|---|---|---|
| Q13 | 26.2 / 24.1 s | 76.2 / 75.8 s | 2.91x / 3.15x | `runs/sf100k-q13recheck`, sanity ok |
| Q3 | 75.3 / 44.1 s | 248.0 / 143.7 s | 3.29x / 3.26x | `runs/sf100k-q3max8`, rows equal earlier runs |

Both use `SIRIUS_PRIORITY_DOWNSTREAM_FIRST=1` and `p180-bp64-bloom8.yaml` (8 GiB Bloom).

- **Q13 (3c):**
  - The RIGHT hash join counts build matches itself. `sirius_plan_aggregate` fuses
    `COUNT` over a LEFT join into it when the join streams.
  - Downstream-first priority runs the join and the aggregation as their input arrives.
- **Q3, the final join's build task waited 182 s in the scheduler queue**, while probe data
  spilled 5.8 TB. Two causes, both fixed:
  - **Dispatch:** the scheduler took tasks pinned to the ready GPU before unpinned ones. Pinned
    scan splits starved the unpinned build. It now takes the better priority of the two
    (`try_pop_for_device`).
  - **Priority:** a join shared by its build and probe branches took the earlier branch's
    priority, which downstream-first's reversal then ranked behind the probe-side scan. It now
    takes the later branch's priority.
  - **Effect:** cold 424 s became 277 s, and spill fell to 0.6 TB.
- **Q3, the Bloom filter size:**
  - **4 GiB (2.3 bits/key, 8 pattern bits):** most rows pass, so the gate drops the filter.
  - **16 GiB:** the merge all-gather stalls. The GPU is 38 GB short with nothing left to evict,
    and every rank times out after 120 s.
  - **8 GiB:** merges, and cuts the lineitem shuffle enough. This gives the 248 s.
- **Still open for Q3:**
  - Pick the Bloom's pattern bits from bits per key. That needs another filter type per
    pattern-bits value.
  - Merge on host, or reduce-scatter the merge, so that a 16 GiB filter fits.

**Earlier state:**
- **Q18:** stopped by hand after about 36 minutes, still in its cold run, which took 97 s at
  SF30000. By then it had spilled 12 TB to disk and filled the host pools. Step 5 covers it.
- **Rebuild:** the hosts are free for it.
- **1b:** `merge_across_ranks` is rewritten. The `merge_from` overload, the planner's stream
  reservation, `max_bloom_keys` and the config copy are next.

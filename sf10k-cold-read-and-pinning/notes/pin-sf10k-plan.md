# Plan: pin most of TPC-H SF10000 in memory, compressed

Status 2026-10-25. Build `dist-plan-opt-rank-pin` (626fe147), 4× GB200, one rank per GPU. Labels: **[m]** measured, **[c]** computed from measurements, **[e]** estimate. The model behind the [e] numbers is `tools/pinmodel.py`, with `footers.json` (Parquet footers of this dataset), `phases.py` and `sets.py` next to it.

**Summary.** Every column the 22 queries read takes about 529 GB per rank compressed with today's plans [c], or 376 GB with plan-file fixes [e]. One resident pin set for all 22 queries can have about 165 GB per rank [c]. A single set therefore can't hold "almost all". Five pin sets, one per query group and re-pinned between groups, can serve 19 of the 22 queries from memory. That is estimated at about 150 s warm against 510 s today [e], plus about 200 s of pinning per pass. Q8, Q9 and Q20 stay disk-bound for lineitem. The first step is set E (config only), which pins Q21's lineitem columns and tests the memory budget.

## 0. Building and pinning by hand

These are the steps from the usage notes, corrected for this setup.

**Build.**
- **Use `dist-plan-opt-rank-pin`, not a community build.** The `install sirius; load sirius;` extension from DuckDB's community repository lacks this branch's rank-share pinning, `replicate` and pin memory limit.
- **The repo's default branch is now `main`, not `dev`.** That matters only for a fresh clone.

```bash
cd /tmp/aocsa/wt/dpo-rank-pin            # or: git clone …; git checkout main
git submodule update --init --recursive
pixi run make                            # this host's NIXL build: /tmp/aocsa/wt/dpo-rank-pin.build.sh
./build/release/duckdb                   # single process, one GPU
```

**Distributed runs.**
- At 4 ranks every rank runs the same SQL file, through `scripts/exchange_sql_ranks.sh` or `bench/dist-tpch/run_suite.py`.
- **Each rank must issue the same `pin_table` calls in the same order.** With rank share, each rank pins its own share of the files, and the ranks agree on narrowing and the memory limit together.
- A `pin_table` run in only one rank's shell would pin that rank's share only.

**Pin calls**, from the notes:

```sql
CREATE VIEW lineitem AS SELECT * FROM read_parquet('/scratch/sirius/datasets/tpch_sf10000/lineitem/*.parquet');
.timer on
-- Host, raw: the columns of one query (Q1 here).
CALL pin_table('/scratch/sirius/datasets/tpch_sf10000/lineitem/*.parquet', tier='host', name='lineitem',
               cols=['l_returnflag','l_linestatus','l_quantity','l_extendedprice','l_discount','l_tax','l_shipdate']);
CALL unpin_table('lineitem');            -- frees the memory; the view stays
-- GPU: the same call with tier='gpu'.
-- Compressed: set the plan directory first. Use an absolute path; tpch_sf1000 plans work at SF10000.
SET pin_table_compression = true;
SET pin_table_input_compression_plan_dir = '/tmp/aocsa/wt/dpo-rank-pin/src/compression/simpatico_codegen/plans/tpch_sf1000';
CALL pin_table('/scratch/sirius/datasets/tpch_sf10000/lineitem/*.parquet', tier='host', name='lineitem',
               cols=['l_returnflag','l_linestatus','l_quantity','l_extendedprice','l_discount','l_tax','l_shipdate']);
```

The notes pin Q1's 7 lineitem columns. At SF10000 those are about 650 GB of Parquet. Uncompressed they don't fit a rank's host pool (`pinned-results.md`, "Pin speed and the memory limit"), so pin them compressed or in a smaller group, as in §3.

**Read path.** Pinned runs still read every unpinned table from GPFS. Use the cold-read fix in their config:
- `memory.host.block_size: 4194304`, or
- the small-read cap: `scan_manager.uring: { max_inflight_small_reads: 16, small_read_bytes: 4MiB }`. It's uncommitted on this branch and committed as `52a33f26` on `perf/uring-small-read-cap`.

## 1. Memory budget per rank

### 1a. What the 22 queries need

Facts about this dataset, from footers [m]:
- lineitem: 600 files, 60.0 B rows, 872 row groups per file, 2.59 TB.
- Only `l_orderkey` and `o_orderkey` are INT64. The part, supplier and customer keys are INT32.
- Decimals are INT64-backed DECIMAL(15,2), although `gen-info.json` says decimal128.
- Pin narrowing makes these columns 4-byte DECIMAL32, and dates INT16 (`src/helper/numeric_narrowing.cpp:311-345`).

The table counts each table's columns that the 22 queries read (`columns.py`), without o_comment. Sizes are GB per rank, rank share; supplier is counted replicated.

| Table (cols) | Parquet [m] | Raw after narrowing [c] | Compressed, repo plans [e] | Compressed, improved plans [e] |
|---|---|---|---|---|
| lineitem (14) | 441 | 1,085 | 355 | 287 |
| orders (7) | 82 | 136 | 37 | 37 |
| partsupp (4) | 33 | 28 | 8 | 8 |
| part (7) | 12 | 54 | 54 (plan disabled, pins raw) | 9 |
| customer (8) | 35 | 66 | 66 (plan disabled, pins raw) | 25 |
| supplier (7), replicated | 9 | 15 | 9 | 9 (2.2 with rank share) |
| **Total** | **612** | **1,384** | **529** | **376** |
| o_comment (Q13 only) | 76 | 198 | 198 (`identity` plan) | 76 (snappy) |

Lineitem with the repo plans [e], GB per rank: l_shipmode 73 (`str_split` leaves its characters raw; a dictionary makes it about 6), l_partkey 58 and l_suppkey 51 (random keys, 31 and 27 bits: at entropy), l_extendedprice 45, l_quantity 25 (Parquet: 11), the three dates 23 each, l_orderkey 10, l_discount and l_tax 8 each, the flags and l_shipinstruct 2–4 each.

Checks on the estimates:
- **SF3000, all pinned** [m]: CPU memory in use grew by 639 GB across 4 ranks (`/tmp/aocsa/tpch22/dpo/validate/mem-final.log`, 00:11:34–00:12:31), about 160 GB per rank (lineitem 102, the rest 55). ×10/3 gives about 533 GB per rank [c], against the model's 529.
- **SF10000, set D** [m]: the tables pinned before lineitem used 49 GB per rank (`validate10k/mem-sf10k-hot.log`); the model gives 54.
- `pinned-results.md`'s "757 GB per rank" matches 529 GB plus o_comment uncompressed.

### 1b. Capacity per rank

**Host:** each 478 GiB (513.6 GB) NUMA node has about 63 GB in use before Sirius starts (127 GB on the idle host [m]), leaving about 225 GB per rank: the 210Gi pool. `pin_table_memory_limit` (0.9) caps pins at about 203 GB of the pool, and the NUMA-node check (`src/pin_table.cpp:675-707`) at about 199 GB per rank.

**Working memory, measured unpinned** (host peak above idle, per rank; `dpo-best/sf10k-22-b4/mem_guard.log`) [m]:
- Q18 56 GB, Q9 50, Q13 25, Q10 23, Q21 19, Q14 15. Every other query 11 GB or less.
- Spill from GPU to host over 3 iterations: Q9 520 GB, Q21 426, Q18 355 [m].

**GPU:** 184 GiB per GPU; Sirius uses 95% (about 188 GB) and pins may fill 90% of that. But Q9, Q18, Q21, Q7 and Q14 already fill the GPU and spill, so each GB pinned there is roughly one more GB they spill to host: it comes out of the same host headroom. Only about 17 GB per rank of GPU pins has run without harm (sets A–C) [m].

| Scope | Realistic pin budget per rank |
|---|---|
| One set for all 22 queries, which must leave Q18 its 56 GB | **about 165 GB in total, host and GPU together** [c] |
| A group without Q9 and Q18 (≤25 GB working) | about 190 GB on the host [c], plus GPU still to be measured (start with 17–40 GB) |

### 1c. The gap

With one resident set the gap is 529 − 165 ≈ **365 GB per rank** with the repo plans, or 376 − 165 ≈ **210 GB** with improved plans. One set holds 30–45% of the needed bytes. To hold everything at once, per-rank memory has to roughly double: 8 ranks on 2 hosts, or smaller working sets.

## 2. Ways to close the gap

| Lever | Evidence | Estimate |
|---|---|---|
| **Per-group pin sets** (re-pin between query groups) | `run_suite.py --queries` already pins only the union of the chosen queries' columns (`run_suite.py:96-112`), so each group is a separate invocation | 19 of 22 queries served; about 148 s warm against 510 [e] |
| **l_shipmode plan:** dictionary + bitpack, as l_shipinstruct already uses (61.8×) | plan files, `tpch_sf1000/lineitem.txt` | −67 GB per rank [e]; makes the Q12 and Q19 groups fit |
| **Enable the part and customer plans** | renamed `*_disabled` in cbd95163, "pending performance/correctness validation" | −45 GB and −40 GB raw → compressed [e] |
| **Column subsetting** | no query reads l_comment, ps_comment or p_comment; o_comment only Q13; c_address and c_comment only Q10; l_partkey and l_suppkey (109 GB together) only Q5, Q7–Q9, Q14, Q15, Q17, Q19–Q21 | as in the sets below |
| **Supplier by rank share instead of replicated** | set C measured no gain from replicating it | −6.7 GB per rank [c] |
| **Two tiers** | `pin_table` takes a tier per call (`src/sirius_extension.cpp:1093-1101`), but `run_suite.py:140` uses one `--pin-tier` for every table | speed rather than room: at SF1000 on GB300, lineitem and orders GPU-compressed ran the suite in 16.66 s against 22.20 s all host-compressed (`compressed-pinning.md`). Room only where the GPU has slack |
| **Narrowing** | already on by default | the raw column above; little more to gain |
| **Better compression for l_quantity** | Simpatico's dictionary is strings-only (`simpatico_codegen/src/explore/compression_explorer.cpp:407`) | up to −13 GB per rank, if a numeric dictionary or scale strip is added |

Lineitem is the whole problem. The orders, part, partsupp, customer and supplier columns together are under 90 GB per rank once compressed.

**Risks:**
- **Spill-heavy queries:** groups with Q18 or Q9 must stay near 165 GB in total; GPU pins push more spill into the host pool.
- **Host downgrade trigger:** 0.9 of the pool by default (`src/sirius_config.cpp:592-593`). If pins count toward it, Q18 starts evicting to disk at about 203 GB.
- **Disk spill space:** `/tmp/aocsa/sirius-disk` is on `/`, with 78 GB free; `host-pin.yaml` allows 60 GB per rank.
- **Pin time:** about 6–7 GB/s of Parquet per rank [m] (D: orders 82 GB in 13.7 s, lineitem 226 GB in 32 s). All 22 queries' columns: about 94 s per rank [e]; each group in §3: 28–50 s [e].
- **Memory-limit failures:** the pin fails on every rank and the queries read from disk; `run_suite` returns rc=1, and the process keeps the pool memory it grew (818 GB in use after D's failed pin [m]).
- **Host-compressed scans** pay a host-to-GPU copy and a decode per batch: 7% slower than raw host pins at SF1000 (`compressed-pinning.md`). Raw doesn't fit at SF10000.

## 3. Staged plan

Common setup:

```bash
cd /tmp/aocsa/wt/dpo-rank-pin; K=bench/dist-tpch; OUT=/tmp/aocsa/tpch22/pin10k; mkdir -p $OUT
DATA=/scratch/sirius/datasets/tpch_sf10000; REF=/tmp/aocsa/tpch22/dpo/sf10k/run-fix-orig
# host-pin.yaml plus 4 MiB host blocks (the cold-read fix) and a later host downgrade trigger.
# Keep the space before the comma: --host-pool rewrites the capacity with a regex.
sed 's/host: { capacity_bytes: 250Gi }/host: { capacity_bytes: 250Gi , block_size: 4194304, downgrade_trigger_fraction: 0.97, downgrade_stop_fraction: 0.93 }/' \
  $K/host-pin.yaml > $OUT/pin-b4.yaml
RS="pixi run python $K/run_suite.py --out $OUT --config $OUT/pin-b4.yaml --host-pool 210Gi --data $DATA --sf 10000 --pin-tier host --ref-dir $REF"
```

The same pins in plain SQL, which every rank must run:

```sql
SET pin_table_input_compression_plan_dir = '/tmp/aocsa/tpch22/dpo/plans-sf10k';
CALL pin_table('/scratch/sirius/datasets/tpch_sf10000/lineitem/*.parquet', tier = 'host',
               name = 'lineitem', cols = ['l_orderkey', 'l_suppkey'], compression = true);
CALL unpin_table('lineitem');
```

Use an absolute plan path (whether `~` is expanded isn't verified). `SET pin_table_compression = true` only sets the default; an explicit `compression` argument wins. `tier = 'gpu'` pins on the GPU.

**Stage 1. Set E: Q21's lineitem, config only, about 160 GB per rank [e]**

E is D's tables plus lineitem {l_orderkey, l_suppkey, l_commitdate, l_receiptdate}. That serves Q21 (53 s warm, the slowest query) and Q4. It also tests whether Q18 and Q9 still run next to 160 GB of pins.

```bash
$RS --tag _E --pin-plans /tmp/aocsa/tpch22/dpo/plans-sf10k --queries 21,4,18,9 \
  --pin-only lineitem,orders,partsupp,supplier,nation,region --pin-replicate nation,region \
  --pin-exclude l_partkey,l_quantity,l_extendedprice,l_discount,l_tax,l_returnflag,l_linestatus,l_shipdate,l_shipinstruct,l_shipmode,o_comment
```

**Result, 2026-10-25** (`/tmp/aocsa/tpch22/pin10k/`). Config: `pin-E.yaml`, which is `host-pin.yaml` with these changes:
- the small-read cap;
- host downgrade at 0.97 / 0.93;
- disk spill cut to 15 GB per rank, because `/` has 78 GB free;
- a 210Gi host pool.

| | Measured |
|---|---|
| Pin time per rank | nation 0.05 s, supplier 1.4 s, partsupp 5.8 s, **orders 102 s**, **lineitem (4 cols) 34 s** |
| CPU memory, 4 ranks | 123 GB before pins → 198 GB after the small tables and orders → **677 GB after lineitem** |
| Pinned per rank | about 19 GB for the small tables and orders, plus about 120 GB for lineitem: **about 138 GB** |
| Q21, cold / best warm | **8.7 / 5.1 s**, against 79.8 / 54.1 unpinned. Answers ok |
| Q4 | **1.4 / 1.24 s**, against 33.5 / 17.1. Answers ok |
| Q18 | 34.8 / 31.9 s for 2 of 3 iterations, against 53.2 / 37.0. Answers differ only in the known tie |
| Q9 | did not run |

- **What failed:** during Q18's third iteration, rank 0 was killed with SIGKILL, most likely by the kernel's OOM killer on NUMA node 0. The ranks peaked at 846 GB in use, 110 GB free across both nodes. Ranks 1–3 then failed with `NIXL_ERR_REMOTE_DISCONNECT`.
- **The cause:** the memory guard (50 GB threshold) did not fire, so a node ran out on its own while total free memory looked fine. Q18 used about 42 GB per rank above the pins, which is less than the 56 GB measured unpinned, but node 0 was already full.
- **Next:** rerun Q18 and Q9 with a 190Gi host pool, as set D used, to leave the nodes more room. Also log per-node free memory.

**Rerun with a 190Gi host pool, Q18 and Q9** (`suite_E2`, same pins and config; free memory per NUMA node logged in `numa_E2.log`). All 4 ranks completed and the guard never fired.

| | Measured |
|---|---|
| Pin time per rank | nation 0.2 s, supplier 0.6 s, partsupp 6.5 s, orders 10.0 s, lineitem 25.3 s. orders took 102 s in the first run, probably because its files weren't in the storage cache yet |
| CPU memory, 4 ranks | 104 GB before pins → 256 GB before lineitem → **485 GB after all pins**, so about **95 GB per rank at most**. The first run showed about 138 GB per rank for the same pins |
| Lowest free memory per node | node 0 115 GB, node 1 95 GB, of 478 GB each, during Q18 and Q9 |
| Q18, cold / warm (3 iterations) | **34.1 / 29.1 s**, against 53.2 / 37.0 unpinned. Answers differ only in the known tie |
| Q9 | **30.6 / 30.0 s**, against 52.8 / 37.1 unpinned. Answers ok |

**Notes on these numbers:**
- **Pinned bytes are not logged.** CPU memory growth measures how far the host pool grew, temporary pin buffers included, so it's an upper bound. That's why the two runs differ. PR C (§4) would log the real sizes.
- **"Cold" here isn't a fresh process.** It's the first iteration after pinning in the same process, so compare warm times with the unpinned runs. Q18 gains from orders being pinned; Q9 from orders, partsupp and supplier. Their lineitem columns still come from disk.
- **The 190Gi pool left 95–115 GB free per node at the peak.** That's enough for set E next to Q18 and Q9. Use 190Gi for the all-22 run.

If it passes, run all 22 queries once (drop `--queries`). Estimate [e]: 383 s warm and 590 s cold against 510 / 797, plus about 47 s of pinning. Q21 should go from 53 to about 6 s, Q4 from 18 to 2.

**All 22 queries with set E, 190Gi pool, 2 iterations** (`suite_E22`, 2026-10-25; `numa_E22.log`, `mem_E22.log`). All ranks completed. The guard never fired. Wall time was 763 s: startup, 47 s of pins, then 44 query runs.

| Q | Unpinned (cap) cold | Unpinned warm | E cold | E warm | Warm speedup | Pinned scans |
|---|---|---|---|---|---|---|
| Q1 | 22.3 | 20.4 | 17.4 | 18.4 | 1.11× | 0 |
| Q2 | 14.0 | 7.7 | 2.0 | 1.3 | 5.92× | 49 |
| Q3 | 42.9 | 25.5 | 17.2 | 16.0 | 1.60× | 23 |
| Q4 | 33.5 | 17.1 | 1.4 | 1.3 | 12.9× | 10 |
| Q5 | 47.2 | 26.3 | 19.9 | 22.0 | 1.20× | 38 |
| Q6 | 32.4 | 17.3 | 12.6 | 12.9 | 1.35× | 0 |
| Q7 | 49.4 | 30.5 | 22.3 | 22.1 | 1.38× | 32 |
| Q8 | 46.4 | 31.9 | 27.7 | 27.4 | 1.16× | 40 |
| Q9 | 52.8 | 37.1 | 34.2 | 30.9 | 1.20× | 32 |
| Q10 | 42.8 | 31.4 | 20.3 | 20.2 | 1.56× | 16 |
| Q11 | 13.9 | 9.3 | 4.2 | 4.2 | 2.20× | 48 |
| Q12 | 40.4 | 20.4 | 15.0 | 13.2 | 1.55× | 8 |
| Q13 | 19.0 | 13.7 | 14.0 | 13.9 | 0.98× | 0 |
| Q14 | 37.5 | 20.5 | 18.0 | 19.1 | 1.07× | 0 |
| Q15 | 40.1 | 20.8 | 17.5 | 17.6 | 1.18× | 8 |
| Q16 | 9.9 | 4.6 | 1.6 | 1.6 | 2.95× | 16 |
| Q17 | 43.9 | 29.2 | 28.1 | 27.7 | 1.05× | 0 |
| Q18 | 53.2 | 37.0 | 32.9 | 28.6 | 1.29× | 8 |
| Q19 | 44.1 | 26.6 | 19.6 | 19.5 | 1.36× | 0 |
| Q20 | 32.7 | 21.2 | 20.3 | 19.5 | 1.09× | 24 |
| Q21 | 79.8 | 54.1 | 5.6 | 5.8 | 9.40× | 48 |
| Q22 | 8.6 | 4.7 | 2.5 | 2.5 | 1.90× | 8 |
| **Total** | **806.7** | **507.3** | **354.3** | **345.4** | **1.47×** | |

The unpinned columns are the small-read-cap sweep (`dpo-best/sf10k-22-cap4m`). The 4 MiB-block sweep was 797.1 / 510.4.

**What the numbers show:**
- **Warm: 345 s against 507 s, 1.47×.** That beats the 383 s estimate. Most of the gain comes from Q21 (−48 s), Q4 (−16 s), Q3, Q10 and Q7 (−8 to −11 s each).
- **Cold isn't comparable.** Here it's the first iteration in a process that already ran the earlier queries; unpinned, each query started a fresh process. End to end, one pass took about 47 s of pins plus 354 s, about 400 s, against 807 s unpinned.
- **Queries with no pinned scans also got faster.** Q6 went from 17.3 to 12.9 s warm, Q19 from 26.6 to 19.5, and Q1 from 20.4 to 18.4. The control below confirms the cause is running in one process, so part of the 1.47× isn't from pinning.
- **Answers are ok** except Q3 and Q18. In both, the ORDER BY columns match the reference row by row; only the choice among tied rows at the LIMIT differs.
- **Memory:** the host pool grew from 122 GB to 696 GB during pinning, about 143 GB per rank. That matches the first set E run, not the 95 GB the Q18/Q9 rerun showed. The peak was 880 GB in use. The lowest free memory per node was 32 GB on node 0 (14:47:57, during Q16) and 43 GB on node 1 (14:49:18, around Q18). That's much tighter than the 95–115 GB the Q18/Q9 rerun left. A larger pin set needs either a smaller host pool or Stage 2's smaller plans.
- **Pin time per rank:** nation 0.01 s, region 0.2 s, supplier 0.8 s, partsupp 4.5 s, orders 10.5 s, lineitem 31.0 s; 47 s in all.

**Control: the five queries set E doesn't serve, in one process with no pins** (`suite_C5`, 2026-10-25). It used the same `pin-E.yaml`, the 190Gi pool and 2 iterations, with `--queries 1,6,14,17,19 --pin-skip lineitem,part`, so `pin_table` was never called. Answers were ok, the guard never fired, and the lowest free memory was 370 / 381 GB per node.

| Q | Cap sweep cold | Cap sweep warm | Control cold | Control warm | E cold | E warm |
|---|---|---|---|---|---|---|
| Q1 | 22.3 | 20.4 | 22.2 | 21.5 | 17.4 | 18.4 |
| Q6 | 32.4 | 17.3 | 12.6 | 13.0 | 12.6 | 12.9 |
| Q14 | 37.5 | 20.5 | 21.4 | 19.0 | 18.0 | 19.1 |
| Q17 | 43.9 | 29.2 | 29.8 | 28.6 | 28.1 | 27.7 |
| Q19 | 44.1 | 26.6 | 19.8 | 19.3 | 19.6 | 19.5 |
| **Total** | **180.3** | **114.0** | **105.9** | **101.4** | **95.8** | **97.5** |

- **The gain on these queries comes from running in one process, not from pinning.** Without pins their warm total is 101.4 s, against 97.5 s in set E and 114.0 s in the cap sweep. Q6 (17.3 → 13.0) and Q19 (26.6 → 19.3) improve just as much as they did in set E.
- **Cold improves once an earlier query has read the same files.** Q1, the first query in the process, is as slow cold as in the cap sweep (22.2 s against 22.3 s). Q6, which runs after it, drops from 32.4 to 12.6 s.
- **Why warm runs also improve is not known.** The likely causes are state the process keeps across queries, such as Parquet metadata, pools or allocations. The config differs a little too: a 190Gi pool against 210Gi, the downgrade fractions, and the disk spill limit. Q6 doesn't spill, though, so these probably don't matter.
- **Gain from pinning alone [e]:** these five queries got about 11% faster warm just from sharing a process. If all 22 got the same, unpinned queries in one process would be about 450 s warm, so set E's 345 s is about **1.3×**, not 1.47×. Q21 and Q4 are the exception: they come almost entirely from pins (54.1 → 5.8 s and 17.1 → 1.3 s). Measuring this properly needs all 22 queries unpinned in one process.

**Stage 2. Plan files (config only), validated at SF3000 first**

```bash
P=src/compression/simpatico_codegen/plans/tpch_sf1000; V=$OUT/plans-v2; mkdir -p $V
cp $P/lineitem.txt $P/orders.txt $V/
for t in partsupp supplier part customer; do cp $P/${t}_disabled.txt $V/$t.txt; done
# In $V/lineitem.txt, replace the body of the l_shipmode block with:
#   input -> dictionary -> keys_offsets, keys_chars, indices
#   dictionary.indices -> bitpack -> chunk_min, chunk_count, chunk_bits, packed
```

Check them with the README's SF3000 all-pinned command and `--pin-plans $V` (about 1 minute of pinning): answers must match; warm within about 5% of 22.9 s; every rank logs `compressing with plan` for all 6 tables and `X/X chunk(s) compressed` per file; pinned memory well below today's 160 GB per rank.

**Result, 2026-10-25** (`/tmp/aocsa/tpch22/pin10k/sf3k-v2/`, plans in `/tmp/aocsa/tpch22/pin10k/plans-v2/`). Both arms ran on the current dpo build, one after the other, using the README command: 210Gi pool, `host-pin.yaml` with disk spill cut to 15 GB per rank, all 8 tables pinned on the host, o_comment excluded, and nation, region and supplier replicated.

| | Repo plans (lineitem, orders) | v2 plans (all 6 tables) |
|---|---|---|
| Tables compressed | lineitem 352/352 chunks, orders 44/44 | those plus part 16/16, partsupp 12/12, customer 20/20, supplier 4/4; no fallback or warning |
| Pin time per rank | 79.5 s (part 37.3, lineitem 23.9) | **62.9 s** (part 27.1, lineitem 18.3) |
| Host pool growth from pinning, 4 ranks | 147 → 759 GB: **153 GB per rank** | 136 → 543 GB: **102 GB per rank (−34%)** |
| Lowest free memory per node | 90 / 103 GB | 197 / 211 GB |
| 22 queries, warm | 22.9 s (the old build's run was also 22.9) | **22.7 s** |
| 22 queries, cold | 23.7 s | 28.7 s: Q2 and Q3 each take 2.8 s more on their first run |
| Answers | ok, except Q3's tie | ok, except Q3's tie |

- **All four checks pass:** answers match; warm time is within 1%; all 6 tables log `compressing with plan` on every rank, and every file logs all its chunks as compressed; pinned memory drops by a third.
- **Slower warm, per query:** Q19 1.15 s against 0.99 (p_brand, p_container and p_size from compressed part), Q22 +8% and Q13 +4% (compressed customer). The rest are equal or 5–19% faster.
- **Slower cold:** Q2 and Q3 are the first queries to read the newly compressed part, partsupp, supplier and customer. The 2.8 s is probably compiling their decoders, once per process. This hasn't been checked.
- **The `ans` question (§6.4):** customer's c_address and c_phone use `ans` and compressed fine at SF3000, where each rank holds 5 of 20 chunks. SF10000 chunks are larger, so it still needs checking there.
- **For Stage 3:** scaling 102 GB × 10/3 gives about 340 GB per rank to pin all 8 tables at SF10000 with v2, against about 510 GB with the repo plans. This is pool growth, so it's an upper bound.

**Stage 3. Five groups, each a separate `run_suite` invocation with plans v2**

Run the groups one after another. Each is a fresh process, so the previous group's pins are freed.

| Set | Queries | Extra flags | GB per rank, repo / v2 plans [e] | Pin time [e] | Warm, today → est. [e] |
|---|---|---|---|---|---|
| P1 | 1, 3, 6, 10, 13, 22 | `--pin-exclude o_comment,c_address,c_comment` | 171 / 153 | 41 s | 120 → 31 |
| P2 | 2, 4, 11, 12, 16, 21 | — | 259 / 163 | 44 s | 113 → 18 |
| P3 | 5, 7, 15 | — | 165 / 163 | 50 s | 77 → 7 |
| P4 | 14, 17, 19 | — | 262 / 170 | 39 s | 76 → 8 |
| P5 | 8, 9, 18, 20 | `--pin-exclude l_partkey,l_suppkey,l_extendedprice,l_discount,l_shipdate` | 125 / 91 | 28 s | 125 → 84 |

```bash
$RS --tag _P2 --pin-plans $V --queries 2,4,11,12,16,21 --pin-replicate nation,region
```

- P2 and P4 fit only with the v2 l_shipmode plan.
- P5 serves Q18 fully. Q8, Q9 and Q20 need l_partkey and l_suppkey together, 109 GB per rank, which doesn't fit next to Q9's working memory. They keep reading lineitem from disk.
- **Total [e]:** about 148 s warm, 198 s cold, plus about 202 s of pinning, against 510 / 797 today.

**P2 result, 2026-10-25** (`suite_P2`; set E config, 190Gi pool, plans-v2, 2 iterations, `--pin-replicate nation,region`). All pins succeeded, and every file was compressed: lineitem 404/404 chunks, orders 92/92, partsupp 36/36, part 28/28, supplier 4/4. Answers ok, no spill, and the guard never fired. Free memory per node fell to 106 / 130 GB, right after pinning.

| Q | Unpinned (cap) cold / warm | Set E cold / warm | P2 cold / warm |
|---|---|---|---|
| Q2 | 14.0 / 7.7 | 2.0 / 1.3 | 0.7 / 0.6 |
| Q4 | 33.5 / 17.1 | 1.4 / 1.3 | 1.2 / 1.2 |
| Q11 | 13.9 / 9.3 | 4.2 / 4.2 | 4.1 / 4.1 |
| Q12 | 40.4 / 20.4 | 15.0 / 13.2 | **2.4 / 2.3** |
| Q16 | 9.9 / 4.6 | 1.6 / 1.6 | 1.1 / 1.1 |
| Q21 | 79.8 / 54.1 | 5.6 / 5.8 | 5.2 / 5.3 |
| **Total** | **191.5 / 113.3** | **29.9 / 27.3** | **14.8 / 14.5** |

- **Warm: 14.5 s against 113.3 s unpinned, 7.8×.** The estimate was 18 s. Q12 now reads from pins, because l_shipdate and l_shipmode are pinned too.
- **Pin time per rank: 56.7 s.** lineitem took 39.3 s, orders 9.1 s, partsupp 4.4 s, part 2.2 s and supplier 0.6 s.
- **Host pool growth per rank, from the 2-s samples:**
  - lineitem, 6 columns: **126 GB**. Set E's 4 columns took 107 GB, so l_shipdate and l_shipmode together add about 19 GB.
  - orders, 4 columns: 10 GB.
  - partsupp: 8 GB.
  - part and supplier: 3 GB.

**Stage 4 (optional). Two tiers**

1. **Small tables on the GPU:** put partsupp, supplier, nation and region on the GPU in every group, 17 GB per rank as in set B. This frees host memory and avoids host-to-GPU copies for them.
2. **Lineitem on the GPU:** then try P3's or P4's lineitem on the GPU, since neither group spills much. Set `memory.gpu.downgrade_trigger_fraction: 0.9` and `downgrade_stop_fraction: 0.85`, as `compressed-pinning.md` advises.
3. **Watch:** `spill_bytes` and pinned-scan hits in `results.jsonl`.

**How to measure each stage:**
- Narrow first (the set's target queries, one run per arm), then all 22 once. Cold is iteration 1, warm the best of 2–3.
- Compare with the 4 MiB-block sweep (`dpo-best/sf10k-22-b4`: 797.1 / 510.4) and the small-read-cap run (806.7 / 507.3).
- Sample CPU memory every 2 s, as `mem_guard.log` did: growth per pin and peak per query.
- Pin failures: `could not pin their share` in `suite_<tag>/exchange_rank_0.log`, `"pin_ok": false` in `<tag>-pins.json`.
- Answers: `vs_ref` must be `ok`; Q3, and Q18 at SF10000, may differ in tied rows only.

## 3b. Pin-once server: can the 22 queries run in 200 s?

The goal: pin once at startup, keep the pins resident, and run the 22 queries against them in 200 s or less, warm, with pinning time not counted. `run_suite.py` already works this way: the ranks stay up, pin once, and then run every query. So no server code is needed to measure it. What limits the result is how much can be pinned at once.

**Real pinned sizes.** After each pin statement, rank logs print a `[host_pool] … allocated=` line, so the bytes held per rank can be read off directly (script: `tools/pin_bytes.py`).

| Run | Bytes pinned per rank | By table |
|---|---|---|
| SF3000, all 8 tables, repo plans | 169.0 GB | lineitem 109.4, customer 19.1, part 16.3, orders 10.9, partsupp 8.7, supplier 4.4 (replicated) |
| SF3000, all 8 tables, v2 plans | **110.7 GB** | lineitem 84.4, orders 10.9, customer 7.3, part 3.1, partsupp 2.4, supplier 2.3 (replicated) |
| SF10000, set E (repo plans) | 153.3 GB | lineitem, 4 columns: 105.7; orders, 7 columns: 37.3; partsupp 8.1; supplier 2.0 |
| SF10000, P2 (v2 plans) | 156.3 GB | lineitem, 6 columns: 134.1; orders, 4 columns: 10.5; partsupp 8.1; supplier 2.0; part, 5 columns: 1.4 |

Every column the 22 queries read, except o_comment, comes to about **372 GB per rank** at SF10000 with v2. That is SF3000 v2 × 10/3, with lineitem's columns split using the measured 4- and 6-column pins and the plans' compression ratios. The large columns, per rank:

| Column | GB per rank |
|---|---|
| l_partkey | 55 |
| l_suppkey | 47 |
| l_extendedprice | 47 |
| l_quantity | 26 |
| each lineitem date | 24 |
| o_custkey | 15.5 |
| o_totalprice | 11.3 |

**Model** (`tools/pin_model.py`; [e]):
- **Pinned time** is SF3000 v2 pinned warm × 10/3. This holds against P2: it predicts 17.3 s, and P2 measured 14.5 s.
- **Unpinned time** is the cap sweep's warm time × 0.89, the one-process factor from the C5 control.
- **Partly served queries** count in proportion to the Parquet bytes the pins serve.
- **Pin rule:** a table's pin serves a scan only if it holds every column the scan reads, as the engine requires today.
- **Check:** at set E's budget, 155 GB per rank, the model predicts 341 s; set E measured 345 s.

| Pin budget per rank | Best 22-query warm [e] | What it pins |
|---|---|---|
| 155 GB, the host's safe limit | 341 s | about set E |
| 200 GB | 294 s | |
| 255 GB | **194 s** | lineitem, 12 columns, about 233 GB: all but l_commitdate and l_receiptdate. Plus small tables; Q21 and Q4 go back to disk |
| 300 GB | 128 s | |
| 372 GB, everything | 76 s | |

**Why one host can't reach 255 GB in one pin:**
- **Host:** two ranks share each 478 GB NUMA node. A rank's pool is 190Gi (204 GB), and queries need about 50 GB of it, as set E's 22-query peak showed. That leaves about 155 GB per rank for pins.
- **GPU:** has 189 GB, and queries fill it. The GPU pool's per-query peak reached 187 GB in set E. How much a GPU pin costs the queries is untested (§6.2).
- **Tiers:** one pin lives on one tier. lineitem's 233 GB fits neither tier, so it would have to be pinned twice under two names, one per tier. A scan uses the first entry that holds all its columns (`sirius_scan_manager.cpp:3854-3858`), so this works. But columns that many queries share, such as l_extendedprice and l_discount, would be stored twice.

**Best splits the model finds [e], assuming GPU pins cost the queries nothing:**

| Host, GPU per rank | Layout | 22-query warm |
|---|---|---|
| 155 + 0 | set E's layout | 341 s |
| 155 + 100 | lineitem split across tiers | 312 s |
| 155 + 120 | lineitem split across tiers | 290 s |
| 175 + 100 | lineitem on the host, small tables on the GPU | 279 s |

**Conclusion:** with today's engine, on this one host, a pin-once server gets to about 280–340 s, not 200 s. Two ways get under 200 s:

1. **Serve a scan's pinned columns from the pin and the rest from disk** (§4 item 4, an engine change in `can_serve_with_columns` and the cached-scan path). Then any subset of columns helps and nothing is stored twice. Model with that rule (`tools/pin_model4.py`), assuming disk reads scale with the bytes left to read:

   | Budget per rank | 22-query warm [e] |
   |---|---|
   | 155 GB, host only | 239 s |
   | 200 GB, host plus about 45 GB on the GPU | 201 s |
   | 255 GB | 132 s |

   The proportional assumption is optimistic for scans that still read some columns from disk.
2. **A second host: 8 ranks.** That halves the data per rank to about 186 GB, which fits on host plus GPU. Estimate: about 80–100 s warm.

**First experiment either way:** pin 40, 80 and 120 GB per rank on the GPU next to set E, and measure what it costs the unpinned queries (§6.2). The GPU budget decides whether option 1 reaches 200 s.

**GPU-pin cost, measured 2026-10-25** (`/tmp/aocsa/tpch22/pin10k/gpu-budget/`). Each arm used set E's host pins and config, with a 190Gi pool, and ran the top 5 for 2 iterations. Each arm also pinned a second copy of lineitem on the GPU, named `lineitem_gpu`, with columns that no top-5 scan can be served from. The logs confirm it served no scan, so the arms measure only the memory cost.
- **Pin sizes** are the `[gpu_pool] allocated=` value after the pin, per rank.
- **Bench change:** this used a local `--pin-extra NAME:TABLE:TIER:cols` option in `run_suite.py` (uncommitted).
- **Plan lookup:** the plan file is found by the pin's name, so the plan directory needs a `lineitem_gpu.txt` (`plans-sf10k-g`).
- **Compression threshold:** l_suppkey alone compresses only 1.33×, which misses `max_compressed_fraction` 0.75, so the 62 GB arm stayed uncompressed.

| GPU pin per rank (columns) | Q9 | Q21 | Q18 | Q7 | Q14 | Warm total | Cold total | Downgraded, 4 ranks: GPU→host / →disk |
|---|---|---|---|---|---|---|---|---|
| 0 GB | 30.0 | 5.2 | 29.3 | 21.9 | 18.7 | **105.3** | 108.2 | 733 / 20 GB |
| 45 GB (l_extendedprice) | 29.9 | 5.3 | 33.2 | 21.5 | 18.6 | **108.5 (+3%)** | 124.6 | 1,796 / 105 GB |
| 62 GB (l_suppkey, uncompressed) | 30.8 | 5.1 | 38.6 | 22.3 | 18.5 | **115.4 (+10%)** | 127.4 | 2,481 / 155 GB |
| 83 GB (l_partkey, l_quantity) | 33.5 | 5.7 | **76.4** | 22.3 | 18.5 | **156.4 (+49%)** | 149.2 | 3,384 / 344 GB |
| 134 GB (l_partkey, l_suppkey, l_quantity) | 73.4 | 6.4 | failed | failed | failed | — | — | 3,941 / 429+ GB |

- **Q18 pays first.** It groups all of lineitem by l_orderkey, so it has the largest GPU working set. It slows 13% at 45 GB, 32% at 62 GB and 2.6× at 83 GB. Q9 slows from 83 GB on. Q21, Q7 and Q14 don't change.
- **134 GB fails.** Q9 took 73 s. Q18 then spilled so much to disk that `/`, with 74 GB free, filled up at 16:27. Rank 3 stopped making progress, and the other ranks timed out on the exchange after 600 s, so Q18, Q7 and Q14 returned errors and 0 rows. Nothing was left behind: the spill directory was cleared, and `/` has 74 GB free again.
- **Cold runs pay more:** +16 s cold at 45 GB, against +3 s warm.
- **Host memory gets tighter too.** GPU downgrades land in the host pool. The lowest free memory on node 0 was 15–18 GB in every GPU arm, against 39 GB without one. The memory guard checks total free memory, not each node, so it doesn't catch this.
- **Usable GPU pin budget: about 45 GB per rank,** where the top 5 lose 3% warm. Past about 60 GB, Q18 degrades fast.

**What this means for 200 s:** with option 1 (partial serving), host 155 + GPU 45 GB is the 200-GB row of the partial-serving model: about 201 s, plus a few seconds of GPU-pin cost on Q18. So option 1 reaches about 205–210 s, only roughly 200 s. Getting clearly under 200 s needs more memory per rank. That means a second host, or plans that shrink the large lineitem columns: l_partkey, l_suppkey and l_extendedprice, about 150 GB per rank together, compress only 1.1–2.7×.

## 4. Code changes

No code is needed for any set above, the plan files, the downgrade fractions, 4 MiB host blocks, or re-pinning per group (each group is its own run).

The pin features themselves (rank share, `replicate`, the memory limit; 445a6640) are only on `dist-plan-opt-rank-pin`, not on `main`. Small PRs against `main`, after those land:

1. **PR A, bench:**
   - `run_suite.py`: a `--pin-tier-for T=tier,...` option overriding the tier at `:140`.
   - Order pins by this dataset's footer sizes rather than `colsizes.json` (`:108-112`). colsizes.json is the CI generator's layout: for example l_partkey is 244 GB there and 362 GB here.
   - Add `block_size: 4194304` to `host-pin.yaml`.
   - Optionally, `--phases FILE` to pin, run and unpin several groups in one process. `:197` assumes every pin comes before the first query.
2. **PR B, plans:** `src/compression/simpatico_codegen/plans/tpch_sf10000/` with the l_shipmode dictionary, and partsupp, supplier, part and customer enabled, once stage 2 passes.
3. **PR C, observability:** log, or return from `pin_table`, the bytes each rank stored per pin, compressed and uncompressed. The payload size is known at `src/pin_table.cpp:795-800`; log it next to the `chunk(s) compressed` line (`:579`). Today pinned sizes can only be inferred from memory samples.
4. **Larger, not now:** let a scan read its pinned columns from the pin and the rest from disk. Today an entry serves a scan only if it holds every column the scan reads (`src/scan_manager/sirius_scan_manager.cpp:2647-2680`). This would let Q8, Q9 and Q20 benefit from the pinned columns they share with other queries.

## 5. Expected gains (estimates)

**Basis:**
- Pinned SF10000 time is assumed to be SF3000 pinned warm × 10/3.
- A query whose tables are only partly served saves in proportion to the Parquet bytes the pins serve.
- **Check against D:** this model predicts 437 s warm for set D against today's 510 s. The measured D, on an older build, was 395 s against 432 s for A: orders saved 35 s.

| Case | Warm (s) | Cold (s) | Pinning per pass |
|---|---|---|---|
| Today, unpinned [m] | 507–510 | 797–807 | — |
| D, older build [m] | 395 | 420 | 24 s, plus a 32-s lineitem pin that failed |
| E [e] | ≈383 (1.3×) | ≈590 | ≈47 s |
| E, all 22 in one process [m] | 345 (1.47×; about 1.3× from pins, the rest from sharing one process) | 354, not a fresh process | 47 s |
| P1–P5 [e] | ≈148 (3.4×) | ≈198 | ≈202 s |
| Everything pinned at once, which doesn't fit [e] | ≈77 (6.6×) | ≈80 | ≈94 s |

Largest per-query gains with P1–P5, warm [e]: Q21 53 → 6 s, Q18 37 → 7, Q17 30 → 2.5, Q7 29 → 3, Q5 29 → 2.5, Q3 27 → 2.4, Q19 26 → 3, Q1 25 → 4. Q8, Q9 and Q20 stay at 19–32 s. SF3000 all-pinned was 8.9× faster warm [m]; SF10000 gets less because it can't be pinned all at once, and every pass spends time pinning.

## 6. Open questions

1. Do host pins count toward the host downgrade trigger? If they don't, `downgrade_trigger_fraction: 0.97` is unnecessary.
2. How much can be pinned on the GPU in groups without spill-heavy queries before spill grows? *Answered for the top 5 (§3b): about 45 GB per rank costs 3% warm; at 83 GB Q18 slows 2.6×; 134 GB fills the disk with spill.*
3. What is Q18's and Q9's real peak of host memory in use? The 2-s sampling may miss short peaks.
4. Are the part and customer plans correct at SF10000? Some of their columns use `ans`, which fails on batches over 2 GiB.
5. Is host-compressed slower than host-raw on GB200 at this scale? This decides whether the dimension tables go raw or on the GPU.
6. Would `tier='parquet'` (`src/sirius_extension.cpp:1333-1346`, undecoded bytes in the IO cache) help? It's smaller than Simpatico only for l_quantity, and it's per table, so it isn't pursued.
7. Is a second host (8 ranks) available? It is the only way to hold almost all columns at once.

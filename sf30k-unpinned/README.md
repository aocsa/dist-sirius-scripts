# TPC-H SF30000 on 4× GB200, unpinned

How to reproduce the first **SF30000** runs of distributed Sirius: all 22 TPC-H queries, nothing
pinned, one cold and one hot run each. It covers:

- **Tuning:** the config tuned for this scale, chosen on the five most expensive queries.
- **The Q21 fix:** a code fix that SF30000 needed, because Q21 fails on the unfixed build.

**Result, 22 queries:**

| Run | Cold | Hot |
|---|---:|---:|
| **SF30000, config `p180`, build with the Q21 fix** | **2542.7 s** | **1696.8 s** |
| SF10000 best unpinned ([sf10k-unpinned-cold-read-fix](../sf10k-unpinned-cold-read-fix/README.md)) | 806.7 s | 507.3 s |
| Ratio | 3.15× | 3.34× |

- **Answers:** every query completes, and the hot run's answer matches the cold run's (no SF30000
  reference answers exist).
- **Memory:** no errors and no memory-guard kills. The lowest free memory on a NUMA node was 28 GB.
- **Disk spill:** 1.3 TB, all from Q9, Q10 and Q18.

"Hot" is the second run of each query, in the same processes; the `results/*.jsonl` files call it
`warm_s`.

## Per query (seconds)

| Query | Cold | Hot | × SF10K cold | × SF10K hot | Disk spill |
|---|---:|---:|---:|---:|---:|
| Q1 | 63.0 | 54.6 | 2.83 | 2.67 | |
| Q2 | 40.4 | 23.3 | 2.89 | 3.04 | |
| Q3 | 129.0 | 83.9 | 3.01 | 3.29 | |
| Q4 | 90.0 | 52.1 | 2.69 | 3.04 | |
| Q5 | 132.9 | 76.9 | 2.81 | 2.92 | |
| Q6 | 87.4 | 49.1 | 2.70 | 2.83 | |
| Q7 | 138.8 | 89.5 | 2.81 | 2.94 | |
| Q8 | 128.0 | 105.4 | 2.76 | 3.31 | |
| Q9 | 217.1 | 139.2 | 4.12 | 3.75 | 89 GB |
| Q10 | 198.8 | 124.8 | 4.65 | 3.98 | 115 GB |
| Q11 | 40.4 | 25.8 | 2.90 | 2.79 | |
| Q12 | 128.9 | 60.1 | 3.19 | 2.94 | |
| Q13 | 55.9 | 50.7 | 2.93 | 3.71 | |
| Q14 | 123.9 | 68.7 | 3.31 | 3.36 | |
| Q15 | 105.7 | 62.5 | 2.64 | 3.00 | |
| Q16 | 20.9 | 9.6 | 2.11 | 2.08 | |
| Q17 | 134.4 | 103.4 | 3.06 | 3.54 | |
| Q18 | 235.4 | 188.7 | 4.42 | 5.10 | 1105 GB |
| Q19 | 130.7 | 77.8 | 2.96 | 2.92 | |
| Q20 | 102.6 | 70.9 | 3.14 | 3.34 | |
| Q21 | 217.4 | 166.1 | 2.72 | 3.07 | |
| Q22 | 21.0 | 13.7 | 2.45 | 2.91 | |
| **Total** | **2542.7** | **1696.8** | **3.15** | **3.34** | **1309 GB** |

- **How it scales:** most queries take 2.7–3.3× their SF10000 time, about linear in the data.
- **The exceptions:**
  - **Q9, Q10 and Q18:** these fill the host pool on every rank and spill to disk, and run 4–5×.
  - **Q13 hot (3.7×):** slower than linear without spilling.
- **More columns:** rows, host-pool peak and free memory per NUMA node per query are in
  [results/sf30k-22-p180-report.md](results/sf30k-22-p180-report.md).

## Setup

- **Hardware:** one host, 4× NVIDIA GB200 (189 GB each), 144 Grace cores on 2 NUMA nodes of
  478 GiB.
  - One rank per GPU, two ranks per NUMA node.
  - Rows are exchanged over NVLink with NIXL/UCX.
- **Data:** TPC-H SF30000 Parquet, 12.7 TB, at `/scratch/prestouser/aocsa/tpch_sf30000` on GPFS,
  read with O_DIRECT (no page cache).
  - **Generator:** the same as SF10000 (sirius-db tpchgen-rs fork, `tpchgen 2.0.2`, decimal128,
    4.3 GB target part size).
  - **Size:** 1800 lineitem files of about 4.6 GB each, Snappy, about 7 MiB row groups.
  - **Key types:** unlike SF10000, `p_partkey`, `ps_partkey`, `l_partkey`, `c_custkey` and
    `o_custkey` are int64, because their values exceed int32.
- **Build:** `aocsa/sirius` at
  [`a4f9e541`](https://github.com/aocsa/sirius/commit/a4f9e54128562c62a937d429d98a7e02c83cf75f)
  (branch [`perf/build-probe-chunked`](https://github.com/aocsa/sirius/tree/perf/build-probe-chunked)).
  That is `perf/sf10k-pinned-repro` plus the Q21 chunked-build fix. Of its earlier commits:
  - the small-read cap (`7c99ebe1`) applies to these runs;
  - partial pin serving (`1af4e21b`) is off by default and unused here.
- **Harness:**
  - `bench/dist-tpch/run_query.py` on that branch runs one query on 4 ranks in fresh processes.
  - [scripts/sf30k_ab.sh](scripts/sf30k_ab.sh) wraps it with safety checks.

```bash
git clone git@github.com:aocsa/sirius.git && cd sirius
git checkout a4f9e541                 # perf/build-probe-chunked
git submodule update --init --recursive
pixi run make
export SIRIUS=$PWD                    # sf30k_ab.sh runs from here (default /tmp/aocsa/wt/dpo-rank-pin)
```

## The config: `p180`

[configs/sf30k-p180.yaml](configs/sf30k-p180.yaml) with
[configs/sf30k-p180.args](configs/sf30k-p180.args):
`--host-pool 180Gi --spill-dir /scratch/prestouser/aocsa/sirius-spill-sf30k`.

```yaml
sirius:
  memory:
    host: { capacity_bytes: 250Gi }   # replaced by --host-pool 180Gi
    disk: { disk_id: 0, capacity_bytes: 1000000000000, downgrade_root_dirs: "@SPILL_DIR@" }
  executor:
    scan_manager:
      num_threads: 32
      uring_n_reactors: 4
      cache: { mode: none }
      uring: { max_inflight_small_reads: 16, small_read_bytes: 4MiB }
  operator_params:
    max_dynamic_filter_bloom_bytes_per_gpu: 4Gi
```

It is the SF10000 best config with two changes:

- **Host pool 180Gi instead of 210Gi.**
  - At 210Gi each rank fills its pool to the 0.9 downgrade trigger (about 189 GiB) and uses about
    40 GiB more outside the pool.
  - Two ranks share a 478 GiB NUMA node, so node 0 ran out during Q18.
  - 180Gi leaves at least 28 GB free per node in the heaviest queries.
- **Disk spill on /scratch with a 1 TB cap per rank,** instead of `/tmp` with 30 GB.
  - Q18 alone writes about 1 TB over its two runs.
  - `/` has 71 GB free, and the spill tier takes a single directory.
  - Change the path in `sf30k-p180.args` for another machine.

## Reproduce

[scripts/sf30k_ab.sh](scripts/sf30k_ab.sh) is the SF30000 copy of the SF10000 `spread_ab2.sh`:

```
sf30k_ab.sh OUT ITERS Q:ARM[,ARM...]...
```

**Configs:**
- Arm `X` uses `OUT/arm-X.yaml`, plus extra `run_query.py` arguments from `OUT/arm-X.args`.
- **Environment variables:**
  - `SIRIUS`: the sirius checkout.
  - `SF`: default 30000.
  - `DATA`: the dataset directory.
  - `REF_DIR`: a run directory holding `qNN/exchange_rank_0.log` reference answers.

**Before each query, the script:**
- waits until no `duckdb` process and no GPU process exist;
- waits until both NUMA nodes have 350 GB free;
- empties the spill directory.

**While the query runs:**
- [scripts/numa_guard.sh](scripts/numa_guard.sh) logs free memory per NUMA node every 2 s.
- The guard kills that build's ranks if a node drops below 15 GB.

**Afterwards:**
- The script counts `Error:` lines in the rank logs, so a query that prints an error and exits 0
  is still flagged.
- `run_query.py`'s answer check alone would not catch one: it compares two empty results as equal.

A full sweep takes about 85 minutes; run it in the background.

```bash
cd sf30k-unpinned
OUT=/tmp/sf30k-22; mkdir -p $OUT
cp configs/sf30k-p180.yaml $OUT/arm-p180.yaml
cp configs/sf30k-p180.args $OUT/arm-p180.args          # edit --spill-dir first
scripts/sf30k_ab.sh $OUT 2 $(for q in $(seq 1 22); do printf '%s:p180 ' $q; done)

# Summaries
python3 scripts/status.py $OUT                          # per run: times, rows, answers, spill, memory
python3 scripts/report.py $OUT ../sf10k-unpinned-cold-read-fix/results/sf10k-unpinned-cap.jsonl $OUT/report.md
python3 ../sf10k-unpinned-cold-read-fix/tools/totals.py results/sf30k-22-p180.jsonl $OUT
```

## Tuning: top 5, one run per arm

At SF10000 the most expensive queries were Q21, Q9, Q18, Q7 and Q14. Times are cold / hot in
seconds:

| Arm | Host pool | Spill | Q21 | Q9 | Q18 | Q7 | Q14 |
|---|---|---|---|---|---|---|---|
| base ([sf30k-base.yaml](configs/sf30k-base.yaml)) | 210Gi | `/tmp`, 30 GB | fails (see below) | 182.1 / 132.4 | killed by the memory guard (node 0 at 11 GB) | 135.8 / 83.7 | 113.2 / 65.5 |
| p170 | 170Gi | /scratch, 1 TB | – | 340.1 / 138.1 | 229.3 / 192.1 | – | – |
| **p180** | 180Gi | /scratch, 1 TB | – | 191.2 / 203.1 | 226.7 / 175.5 | – | – |

- **Why Q7 and Q14 weren't rerun:** their host pools peak at 18 and 47 GiB, so the pool size
  doesn't affect them.
- **Q9 is noisy:** its disk spill (14, 156, 222 GB) doesn't follow the pool size, and its time
  swings by 60+ seconds between single runs.
- **So the pool size was chosen for memory safety:**
  - p180 keeps at least 28 GB free per node;
  - a 185Gi pool would leave about 20 GB, too close to the guard.
- **Not tried:** the downgrade fractions, `scan_task_batch_size`, the bloom cap and the small-read
  cap. Spill noise would hide their effect in single runs.

Raw data:
- per-run results: `results/sf30k-top5-*.jsonl`;
- per-run summary, with host-pool peak, disk spill and lowest free memory:
  [results/sf30k-top5-status.txt](results/sf30k-top5-status.txt);
- timeline and memory log: `results/sf30k-top5-timeline.txt` and
  `results/sf30k-top5-numa-mem.log`.

## Q21 and the chunked-build fix

**The failure.** On the unfixed build, Q21 fails on every rank with "CONCAT partition exceeds the
cuDF row-count limit".

- DuckDB turns Q21's `EXISTS` subquery into a MARK hash join.
- Sirius always runs a MARK join in BUILD_PROBE mode with one partition per GPU, and merges that
  partition's whole build side into one cuDF table.
- At SF30000 that table needs about 2.2 billion rows per rank, past cuDF's limit of 2^31−1.
- No config key changes it.

**The fix** (commit `a4f9e541`):
- **CONCAT:** such a build is split into row-bounded chunks, but only for joins that can merge
  chunks: BUILD_PROBE MARK, SEMI, ANTI and INNER.
- **The join:**
  - builds one table per chunk;
  - for MARK, SEMI and ANTI, ORs the chunks' matches;
  - for INNER, concatenates the per-chunk joins.
- **Safeguards:**
  - The join waits for every chunk before building.
  - A split build never feeds a runtime filter as if it were the whole build.
- **`operator_params.build_chunk_max_rows`** caps a chunk. It defaults to cuDF's limit; lower
  values force splits for testing.
- **Builds under the limit** take the unchanged path.

**Evidence:**

| Check | Result |
|---|---|
| SF30000 Q21, default cap | 211.6 s cold / 165.0 s hot, 2 chunks per rank, 100 rows |
| SF30000 Q21, 1B-row cap ([config](configs/sf30k-p180-chunk1b.yaml)) | 3 chunks, plus 2 in the NOT EXISTS join; the same 100 rows as the default-cap run |
| SF10000 Q21 with forced splits, 200M- and 10M-row caps ([200M](configs/sf10k-chunk200m.yaml), [10M](configs/sf10k-chunk10m.yaml)) | 3–12 chunks per rank in both MARK joins; the answer matches the SF10000 reference; time unchanged (80.4 / 55.0 s against 79.8 / 54.1 s) |
| SF10000 Q4, Q20, Q22 with forced splits, 10M-row cap | Answers match the reference; Q20's join was built from 4 chunks |
| Unit tests | 6 new end-to-end chunked-join tests (MARK with NULL keys and an empty chunk, SEMI, ANTI, INNER) and 2 CONCAT tests. The CONCAT, hash join, MARK join, partition, `[pin_table]` and `[scan_manager]` tags all pass |

- **Design, alternatives and full evidence:**
  [notes/q21-chunked-build.md](notes/q21-chunked-build.md).
- **The same change as a patch:** [patches/q21-chunked-build.patch](patches/q21-chunked-build.patch).

## Files

| Path | Contents |
|---|---|
| [configs/](configs) | `sf30k-p180.yaml` + `.args` (the chosen config), `sf30k-base.yaml` (the SF10000 best), `sf30k-p170.yaml` + `.args`, `sf30k-p180-chunk1b.yaml`, and `sf10k-chunk*.yaml` (forced-split checks) |
| [scripts/sf30k_ab.sh](scripts/sf30k_ab.sh) | Runs queries per arm with the safety checks; any scale through `SF` / `DATA` / `REF_DIR` |
| [scripts/numa_guard.sh](scripts/numa_guard.sh) | Free memory per NUMA node every 2 s; kills the ranks below 15 GB on a node |
| [scripts/status.py](scripts/status.py) | Per run: times, rows, answers, `Error:` count, disk spill, host-pool peak, lowest free memory per node |
| [scripts/report.py](scripts/report.py) | The per-query Markdown report with totals and the SF10000 ratio |
| [scripts/pool_peaks.py](scripts/pool_peaks.py), [scripts/chunk_evidence.py](scripts/chunk_evidence.py) | Host and GPU pool peaks per rank; CONCAT splits and multi-chunk join builds found in the logs |
| [results/](results) | `results.jsonl` lines of every run, run timelines, memory logs, status summaries, the 22-query report |
| [notes/q21-chunked-build.md](notes/q21-chunked-build.md) | The Q21 failure, the fix design, the alternative considered, and the evidence |
| [patches/q21-chunked-build.patch](patches/q21-chunked-build.patch) | The fix as a patch (identical to `a4f9e541`) |

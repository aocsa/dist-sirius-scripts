# dist-plan-opt at SF10000 on 4x GB200: the five suggested improvements

Baseline: `dist-plan-opt` @ `59d40bdc`, all 22 TPC-H queries at SF10000 from `/scratch` GPFS (O_DIRECT),
4 ranks, config `configs/cfg10k.yaml` (host 250 GiB, 30 GB spill cap, 32 scan threads, 4 uring
reactors, 4 GiB Bloom cap): **1,837 s cold, 850 s warm** (sum over queries), no spill. Every query
returns the same answer cold and warm; Q3, Q11 and Q18 differ only in the order of tied rows (SF10000
repeats orders with identical values, and these queries do not sort on the order key). No reference
answers exist at SF10000.

## 3. Rank-0 straggler: found and fixed (biggest win)

Cause: DuckDB pins each worker thread to one core on hosts with more than 64 cores
(`pin_threads = auto`; this host has 144). Sirius starts its pools from whichever thread runs the
first statement. On ranks 0 and 2 that was a pinned DuckDB worker (CPU 1 and CPU 0), so the 48
scan-manager threads, the 4 io_uring reactors, all `iou-wrk` workers, the downgrade and task-creator
threads of that rank shared **one core**. Ranks 1 and 3 started from an unpinned thread. NICs and
NUMA are symmetric (GPU0/1 + mlx5_0/1 on node 0, GPU2/3 + mlx5_4/5 on node 1); they were not the
cause.

Fix: [`432a7c88`](https://github.com/aocsa/sirius/commit/432a7c88) on `aocsa/sirius@dist-plan-opt`
("fix(exec): threads Sirius starts without CPUs run on the process's CPUs"): pools and reactors given no CPU list take the process's mask (`thread_util::get_process_affinity()`)
instead of their creator's. Regression test in `test/cpp/exec/test_bounded_thread_pool.cpp`; the
30 `[bounded_thread_pool]`/`[thread_util]` tests pass.

| Q5 SF10000 from /scratch, warm | before | after |
|---|---|---|
| time | 43.5-44.8 s | 34.6-36.5 s |
| per-rank lineitem scan end spread | 6-8 s | < 1 s |
| GPFS average | 28 GB/s | 34-36 GB/s |
| lineitem scan starts at | 10.1 s | 5.5 s |

Q9/Q18/Q21 warm: 51.1/46.5/101.4 s -> 43.6/44.3/68.3 s.

## 4. scan_manager knobs: nothing more on top of the fix

Configs: `configs/knobs/{base,readahead,threads64,batch256}.yaml`, run with `scripts/run_q5.sh`.

Q5 from /scratch, warm (2 runs): base 35.6/36.5; readahead eager x8 33.8/36.6; 64 scan threads
34.6/36.6 (all within noise); 256 MiB scan batches 44.5/45.1 (worse). With the straggler gone GPFS
already averages ~35 GB/s.

## 5. Bloom filters on Q9, Q18, Q21 (fixed build), warm (cold)

Configs: `configs/knobs/{base,no_multipart,no_filters}.yaml`.

| config | Q9 | Q18 | Q21 |
|---|---|---|---|
| all filters | 43.6 (126) | 44.3 (126) | 68.3 (170) |
| no multi-partition Blooms (`59d40bdc` off) | 42.1 (120) | 45.9 (108) | 88.8 (169) |
| no dynamic filters | crash in run 2 (99) | 36.4 (119) | 67.0 (171) |

- `59d40bdc` pays off on Q21 (-23%), not measurably on Q9 or Q18 at SF10000.
- Q18 is faster with no filters at all (36.4 vs 44.3 s): its filters cost more than they save.
- Q9 with no filters crashed on its second run: SIGSEGV in `cuMemFreeAsync` freeing a `data_batch`
  after the query released its memory, the same signature `5127c81a` fixed on another path. A real
  bug, separate from this work (rank logs on the bench box, not in this repo).

## 2. Overlapping the startup scans: no gain as-is

The task creator walks the plan from the root and follows the first unready input, so build
branches start one after another. Turning on its existing `lookahead` strategy (rebuild needed; the
config key was removed) starts every scan within ~1 s (lineitem at 0.9-1.4 s instead of 5.5 s) and
answers stay correct, but Q5 got no faster (46.1/38.0 s vs 35.6/36.5 s) and rank skew came back.
The remaining startup is the dynamic-filter chain (nation -> customer -> orders -> lineitem: each
probe scan waits for the filter from the build before it), so starting probe scans early forfeits
their filters and buffers unfiltered batches. Only unfiltered build scans can safely start early,
and they already take < 0.5 s. Reverted.

## 1. Smaller input files: rewritten, measurement pending

`/scratch/nvidia/aocsa/tpch_sf10000_zstd`: every table rewritten file by file with DuckDB
(same files, same row-group size, same column types; ZSTD instead of Snappy) by
`scripts/rewrite_zstd.sh` (12 files at a time, `scripts/rewrite_one.sh` each). 3.7 TB -> 2.3 TB
(-38%): lineitem 2.53 -> 1.61 TB, orders 750 -> 424 GB. Row counts of all 8 tables match; one
lineitem file was also checked by schema and checksum. Per column on that file: l_orderkey -88%,
l_extendedprice -51%, l_suppkey -38%, l_comment -37%, l_partkey -34%. This changes what is benchmarked
(not the CI generator's files).

## Pending

Full 22-query sweep on the fixed build (`432a7c88`) from the original data: running on 2026-10-23
when this was published. The ZSTD round is not scheduled; the ZSTD copy stays at
`/scratch/nvidia/aocsa/tpch_sf10000_zstd`.

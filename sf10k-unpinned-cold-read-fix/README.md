# TPC-H SF10000, unpinned: the io_uring cold-read fix

How to reproduce the **unpinned** runs of distributed Sirius at TPC-H SF10000 on 4× GB200, and the
investigation behind them: why first reads from GPFS were slow, and the fix, a cap on concurrent small
reads per io_uring reactor. Nothing is pinned in these runs.

**Result, 22 queries:**

| Run | Cold | Warm |
|---|---|---|
| Original integration build, no fix (Oct 24) | 2128.3 s | 684.5 s |
| Fix A: 4 MiB pinned host blocks | 797.1 s | 510.4 s |
| **Fix B: small-read cap (16 reads under 4 MiB per reactor)** | **806.7 s** | **507.3 s** |

Both fixes cut cold time 2.6× and warm time 1.35×. The cap is the one to use: 4 MiB blocks slow some warm
runs (Q1 24.7 s against 20.4 s, Q6 21.7 s against 17.3 s). Every answer matches the reference run; Q3 and
Q18 differ only among tied rows, as in every SF10000 run.

## What was wrong

Each io_uring reactor stages reads through a fixed 64 MiB of host blocks, so with the default 1 MiB blocks
it keeps up to 64 reads in flight; 4 ranks × 4 reactors make about 1,000. Parquet column chunks are often a
few hundred KB, and those reads land in neighbouring blocks of the same GPFS file. When the storage has not
cached those blocks, the concurrent small O_DIRECT reads contend: cold scans ran at 5–7 GB/s per node and
spent most of their time in kernel CPU in the io-wq workers. A standalone probe reproduces it outside
Sirius: 256 slots per reactor read at 1.22 GB/s; spreading the same reads out reached 34 GB/s.
Details, ruled-out causes and measurements: [notes/cold-read-plan.md](notes/cold-read-plan.md).

## Code

Build **`aocsa/sirius` at commit `7c99ebe1`**: branch `dist-plan-opt-rank-pin` (`626fe147`) plus one
commit, the small-read cap. It is the first commit on branch
[`perf/sf10k-pinned-repro`](https://github.com/aocsa/sirius/tree/perf/sf10k-pinned-repro); the next commit
on that branch adds pinning features that these runs do not use. The same cap rebased on `main` is commit
`52a33f26`.

The cap adds two keys under `sirius.executor.scan_manager.uring`, both off by default:

| Key | Default | Meaning |
|---|---|---|
| `max_inflight_small_reads` | 0 (no cap) | Most reads smaller than `small_read_bytes` one reactor keeps in flight |
| `small_read_bytes` | `1MiB` | Read size below which the cap applies |

The original integration build was a different branch (`dist-rank-pin-sf3k-integ`). To reproduce a
no-fix baseline on the same build, run commit `7c99ebe1` with
[configs/unpinned-baseline.yaml](configs/unpinned-baseline.yaml) (no cap, 1 MiB blocks).

## Setup

- **Hardware:** one host, 4× NVIDIA GB200 (189 GB each), 144 Grace cores on 2 NUMA nodes of 478 GB, one
  process per GPU, ranks exchanging batches through NIXL/UCX over NVLink.
- **Data:** TPC-H SF10000 Parquet from tpchgen-rs (3.7 TB) at `/scratch/sirius/datasets/tpch_sf10000`
  (SF3000 at `/scratch/sirius/datasets/tpch_sf3000`), on GPFS (16 MiB blocks), read with O_DIRECT, so
  no page cache. Change `--data` in the scripts for another location.
- **Harness:** `bench/dist-tpch/run_query.py` on the sirius branch runs one query on 4 ranks in fresh
  processes, `--iters` times: the first run is cold, the best later run is warm. It writes
  `results.jsonl` and checks answers against `--ref`.

```bash
git clone git@github.com:aocsa/sirius.git && cd sirius
git checkout 7c99ebe1                 # dist-plan-opt-rank-pin + small-read cap
git submodule update --init --recursive
pixi run make
export SIRIUS=$PWD                    # the scripts run from here (default /tmp/aocsa/wt/dpo-rank-pin)
```

## Reproduce

`scripts/spread_ab2.sh OUT ITERS Q:ARM...` runs each query/arm pair in its own fresh 4 processes, with
`OUT/direct-io-spread-ARM.yaml` as the config. Before each run it waits until no `duckdb` runs, no GPU
process exists and both NUMA nodes have 350 GB free, and it empties the spill directory. It also runs
`mem_guard_dpo.sh`, which logs CPU memory every 2 s and kills this build's ranks below 50 GB free.
Answers are checked against `REF` (set at the top of the script); point it at your own first run, or
remove `--ref`. A full 22-query sweep takes 15 to 40 minutes; run it in the background.

```bash
cd sf10k-unpinned-cold-read-fix
QS=$(seq 1 22)

# Fix B: small-read cap, all 22 queries, 2 iterations each
OUT=/tmp/sf10k-unpinned-cap; mkdir -p $OUT
cp configs/unpinned-cap.yaml $OUT/direct-io-spread-cap4m.yaml
scripts/spread_ab2.sh $OUT 2 $(for q in $QS; do printf '%s:cap4m ' $q; done)

# Fix A: 4 MiB host blocks
OUT=/tmp/sf10k-unpinned-b4; mkdir -p $OUT
cp configs/unpinned-4mib-blocks.yaml $OUT/direct-io-spread-b4.yaml
scripts/spread_ab2.sh $OUT 2 $(for q in $QS; do printf '%s:b4 ' $q; done)

# No fix, same build
OUT=/tmp/sf10k-unpinned-base; mkdir -p $OUT
cp configs/unpinned-baseline.yaml $OUT/direct-io-spread-base.yaml
scripts/spread_ab2.sh $OUT 2 $(for q in $QS; do printf '%s:base ' $q; done)

# Compare with the saved runs
python3 tools/totals.py results/sf10k-unpinned-cap.jsonl /tmp/sf10k-unpinned-cap
```

Smaller checks from the same study:

| Check | Command | Saved result |
|---|---|---|
| Cap threshold, 2 MiB vs 4 MiB, top 5 + Q1 | `spread_ab2.sh` with [unpinned-cap-2mib.yaml](configs/unpinned-cap-2mib.yaml) as arm `cap2m` and unpinned-cap.yaml as `cap4m` | [2 MiB](results/sf10k-top5-cap-2mib.jsonl): 302.2 s cold, 192.1 s warm; [4 MiB](results/sf10k-top5-cap-4mib.jsonl): 298.1 s, 198.0 s |
| Does the cap hold at SF3000? Top 5 | `spread_ab_sf3k.sh OUT 2 9:base,cap4m 21:base,cap4m ...` with unpinned-baseline.yaml as `base` | [baseline](results/sf3k-top5-baseline.jsonl): 280.1 s cold, 78.8 s warm; [cap](results/sf3k-top5-cap-4mib.jsonl): 99.4 s, 57.0 s |
| The cause, outside Sirius | `gcc -O2 -o uring_probe probe/uring_probe.c -luring -lpthread`; `SF=10000 python3 probe/manifest.py lineitem l_orderkey,l_shipdate 0 20 > reads.man` (pyarrow; byte ranges a scan of those columns reads, for files 0–19); `./uring_probe reads.man` (options as `key=value`, listed at the top of the source). Run on files GPFS has not read recently | see cold-read-plan.md |

A read-shuffle fix was tried and reverted; its patch is
[patches/spread-reads-and-uring-stats.patch](patches/spread-reads-and-uring-stats.patch).

## Files

| Path | Contents |
|---|---|
| [notes/cold-read-plan.md](notes/cold-read-plan.md) | The problem, causes ruled out, the standalone reproduction, both fixes, tuning, SF3000 check |
| [notes/experiments-summary.md](notes/experiments-summary.md) | Every unpinned sweep and config compared, per query, with the recommended config |
| [configs/](configs) | `unpinned-cap.yaml` (Fix B), `unpinned-4mib-blocks.yaml` (Fix A), `unpinned-baseline.yaml` (no fix), `unpinned-cap-2mib.yaml` (tuning) |
| [scripts/](scripts) | `spread_ab2.sh` (SF10000), `spread_ab_sf3k.sh` (SF3000), `mem_guard_dpo.sh` |
| [tools/totals.py](tools/totals.py) | Per-query and total cold/warm times of several runs side by side |
| [results/](results) | Raw `results.jsonl` of every run above, one line per query: `cold_s`, `warm_s`, `vs_ref`, config |
| [probe/](probe) | The standalone io_uring read probe and its read-list generator |

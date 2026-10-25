# TPC-H SF10000: cold-read fix and pinned tables

Notes, scripts, configs and raw results for the October 2026 work on distributed Sirius at TPC-H SF10000:
first the cold first-read penalty in the local io_uring read path, then pinning tables in host memory,
then serving a scan from a pin that holds only some of its columns.

**Result, 22 queries, 4× GB200:**

| Run | Warm | Cold |
|---|---|---|
| Unpinned, original integration build (Oct 24) | 684.5 s | 2128.3 s |
| Unpinned, cold-read fix (small-read cap) | 507.3 s | 806.7 s |
| Pinned (set E, 153 GB per rank) | 345.4 s | 354.3 s |
| **Pinned (set E) + partial pin serving** | **274.7 s** | **283.1 s** |

Pinning takes 51 s per rank, once per process, and is not in the pinned totals. Unpinned runs start
fresh processes for every query; pinned runs run every query in the processes that pinned (see
[notes/pin-sf10k-plan.md](notes/pin-sf10k-plan.md) §3). A rendered comparison of Pinned and Unpinned is in
[notes/sf10k-pinned.html](notes/sf10k-pinned.html).

## Code

| Branch on `aocsa/sirius` | What | Use |
|---|---|---|
| [`perf/sf10k-pinned-repro`](https://github.com/aocsa/sirius/tree/perf/sf10k-pinned-repro) (`1af4e21b`) | `dist-plan-opt-rank-pin` (`626fe147`) + small-read cap (`7c99ebe1`) + partial pin serving (`1af4e21b`) | **Build this to reproduce everything here.** |
| [`perf/uring-small-read-cap`](https://github.com/aocsa/sirius/tree/perf/uring-small-read-cap) (`52a33f26`) | The small-read cap alone, on `main` | PR candidate |
| [`perf/pin-partial-serving`](https://github.com/aocsa/sirius/tree/perf/pin-partial-serving) (`4c874cc0`) | Cap + chunk row-group records + partial pin serving, on `main` | PR candidate. `main` lacks the per-rank pin sharing the 4-rank pinned runs use. |

Two experiments used local changes that are not on any branch; apply them on top of
`perf/sf10k-pinned-repro` with `git apply`:

- [patches/scan-timing-diagnostics.patch](patches/scan-timing-diagnostics.patch): per-split fetch, decode, filter
  and shuffle timers, logged when `SIRIUS_SCAN_TIMING=1` (the scan-time split and shuffle tables).
- [patches/run-suite-pin-extra.patch](patches/run-suite-pin-extra.patch): `run_suite.py --pin-extra NAME:TABLE:TIER:cols`,
  one more pin such as a second copy of a table on the GPU (the GPU-pin budget test).
- [patches/spread-reads-and-uring-stats.patch](patches/spread-reads-and-uring-stats.patch): the reverted read-shuffle
  experiment, kept for reference.

## Setup

```bash
git clone -b perf/sf10k-pinned-repro git@github.com:aocsa/sirius.git && cd sirius
git submodule update --init --recursive
pixi run make
export SIRIUS=$PWD                       # every script runs from here (default /tmp/aocsa/wt/dpo-rank-pin)

cd /path/to/dist-sirius-scripts/sf10k-cold-read-and-pinning
scripts/setup.sh                         # copies configs and plan files under /tmp/aocsa/tpch22 (TPCH_OUT)
```

- **Hardware:** one host, 4× NVIDIA GB200 (189 GB each), 144 Grace cores on 2 NUMA nodes of 478 GB.
- **Data:** TPC-H SF10000 Parquet from tpchgen-rs (3.7 TB) at `/scratch/sirius/datasets/tpch_sf10000`, SF3000 at
  `/scratch/sirius/datasets/tpch_sf3000`, on GPFS, read with O_DIRECT. Change the `--data` paths in the scripts for
  another location.
- **Harness:** `bench/dist-tpch/run_query.py` (one query, fresh processes) and `bench/dist-tpch/run_suite.py` (pin
  once, then many queries in one process) on the sirius branch. Both write `results.jsonl` and check answers.
- **Reference answers:** the scripts compare against an earlier unpinned run (`REF=/tmp/aocsa/tpch22/dpo/sf10k/run-fix-orig`,
  `.../dpo/sf3k-hostpin`). Point `REF` and `--ref-dir` at your own first unpinned run, or drop them.

Every script waits until no `duckdb` runs, no GPU process exists and each NUMA node has 350 GB free, empties
the spill directory, and runs `mem_guard_dpo.sh` (logs CPU memory every 2 s and kills this build's ranks below
50 GB free) and `numa_log.sh` (free memory per NUMA node). Run each one in the background; most take 5 to 20 minutes.

## Experiments

| # | Question | Result | Script | Config | Results | Notes |
|---|---|---|---|---|---|---|
| 1 | Why are first reads slow? | Many small O_DIRECT reads into neighbouring GPFS blocks contend when the storage has not cached them | [probe/uring_probe.c](probe/uring_probe.c), [probe/manifest.py](probe/manifest.py) | — | — | [cold-read-plan.md](notes/cold-read-plan.md) |
| 2 | Fix: 4 MiB host blocks, 22 queries unpinned | 2128 → 797 s cold, 685 → 510 s warm | `spread_ab2.sh` | [unpinned-4mib-blocks.yaml](configs/unpinned-4mib-blocks.yaml) | [sf10k-unpinned-4mib-blocks.jsonl](results/sf10k-unpinned-4mib-blocks.jsonl) | [experiments-summary.md](notes/experiments-summary.md) |
| 3 | Fix: small-read cap, 22 queries unpinned | 807 s cold, 507 s warm, no Q1/Q6 warm regressions | `spread_ab2.sh` | [unpinned-cap.yaml](configs/unpinned-cap.yaml) | [sf10k-unpinned-cap.jsonl](results/sf10k-unpinned-cap.jsonl) | same |
| 4 | Cap threshold 2 MiB vs 4 MiB, top 5 | 4 MiB | `spread_ab2.sh` | [unpinned-cap-2mib.yaml](configs/unpinned-cap-2mib.yaml), unpinned-cap.yaml | [sf10k-top5-cap-tuning.jsonl](results/sf10k-top5-cap-tuning.jsonl) | cold-read-plan.md |
| 5 | Does the cap hold at SF3000? | top 5: 280 → 99 s cold, 79 → 57 s warm | `spread_ab_sf3k.sh` | unpinned-cap.yaml | [sf3k-top5-cap.jsonl](results/sf3k-top5-cap.jsonl) | cold-read-plan.md |
| 6 | Where does scan time go? | fetch 77–91%, decode 9–23%, filters ~0; shuffle 1–2% | `profile_shuffle.sh` + scan-timing patch, then [tools/scan_split.py](tools/scan_split.py), [tools/shuffle_table.py](tools/shuffle_table.py) | 4 MiB blocks | — | [scan-optimization-plan.md](notes/scan-optimization-plan.md) |
| 7 | Pin set E: Q21, Q4, Q18, Q9 | Q21 54 → 5 s warm; first try OOM-killed at 210Gi, fine at 190Gi | `pin_E.sh`, `pin_E2.sh` | [pin-E.yaml](configs/pin-E.yaml), [plans-sf10k/](configs/plans-sf10k) | [sf10k-pinned-set-e-and-partial.jsonl](results/sf10k-pinned-set-e-and-partial.jsonl) (`_E`, `_E2`) | [pin-sf10k-plan.md](notes/pin-sf10k-plan.md) |
| 8 | Set E, all 22 queries | 345.4 s warm | `pin_E22.sh` | pin-E.yaml | same file, `_E22` | same |
| 9 | How much is the one-process effect? | ~11% on queries that read no pins | `pin_C5.sh` | pin-E.yaml | same file, `_C5` | same |
| 10 | Smaller plans (v2) at SF3000 | all 6 tables compressed, 169 → 111 GB per rank pinned, same warm time | `sf3k_v2.sh` | [sf3k-host-pin.yaml](configs/sf3k-host-pin.yaml), [plans-v2/](configs/plans-v2) | [sf3k-plans-v2.jsonl](results/sf3k-plans-v2.jsonl) | same |
| 11 | Group P2 at SF10000 | 113.3 → 14.5 s warm for 6 queries | `pin_P2.sh` | pin-E.yaml, plans-v2 | `_P2` | same |
| 12 | How much can the GPU pin? | ~45 GB per rank costs 3%; 83 GB makes Q18 2.6× slower; 134 GB fills the disk | `gpu_budget.sh`, `gpu_budget2.sh` + pin-extra patch, [tools/gpu_budget_table.py](tools/gpu_budget_table.py) | pin-E.yaml, [plans-sf10k-gpu/](configs/plans-sf10k-gpu) | [sf10k-gpu-pin-budget.jsonl](results/sf10k-gpu-pin-budget.jsonl) | same §3b |
| 13 | Pin sizes and what fits | 372 GB per rank for every needed column; models per budget | [tools/pin_bytes.py](tools/pin_bytes.py), [tools/pin_model.py](tools/pin_model.py), [pin_model4.py](tools/pin_model4.py) | — | — | same §3b |
| 14 | Partial pin serving, top 5 + Q19, off vs on | 125.2 → 98.4 s warm | `partial_ab.sh off on`, `partial_ab_q.sh`, `partial_m155.sh` | pin-E.yaml, [pinned-partial25.yaml](configs/pinned-partial25.yaml) | [sf10k-partial-serving-ab.jsonl](results/sf10k-partial-serving-ab.jsonl) | [partial-pin-serving-design.md](notes/partial-pin-serving-design.md) |
| 15 | Partial pin serving, 22 queries, threshold 0.4 | 309.3 s warm | `pin_E22p40.sh` | [pinned-partial40.yaml](configs/pinned-partial40.yaml) | `_E22p40` | same §4.3c |
| 16 | Partial pin serving, 22 queries, threshold 0.3 | **274.7 s warm, 283.1 s cold** | `pin_E22p30.sh` | [pinned-partial30.yaml](configs/pinned-partial30.yaml) | `_E22p30` | same §4.3d |

Commands for the main runs:

```bash
cd sf10k-cold-read-and-pinning

# 3. Unpinned, small-read cap, all 22 queries, each in fresh processes, 2 iterations
OUT=/tmp/aocsa/tpch22/dpo-best/sf10k-22-cap4m; mkdir -p $OUT
cp configs/unpinned-cap.yaml $OUT/direct-io-spread-cap4m.yaml
scripts/spread_ab2.sh $OUT 2 $(for q in $(seq 1 22); do printf '%s:cap4m ' $q; done)

# 8. Pinned, set E, all 22 queries
scripts/pin_E22.sh

# 16. Pinned, set E + partial pin serving at 0.3, all 22 queries
scripts/pin_E22p30.sh

# Compare per query
python3 tools/cmp_E22.py
```

Results land in `results.jsonl` under each output directory: one line per query with `cold_s` (first run),
`warm_s` (best later run), `vs_ref` (answer check), `pin_hits` (scans served from pins) and the config used.
Q3 and Q18 differ from the reference only among tied rows in every run, pinned or not.

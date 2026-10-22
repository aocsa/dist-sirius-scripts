# Results on the reference box

**Box:** 2x NVIDIA RTX PRO 4500 Blackwell (32 GB each), 48 cores, 181 GB RAM, one 1.7 TB instance
NVMe (AWS), driver 580.126.09, CUDA 13.0. Sirius `exchange-operator-v2` built with NIXL 1.3.2 /
UCX 1.21.0. SF1000 answers match DuckDB on the CPU; SF3000 answers agree across 1 GPU, 2 GPUs
and both Q5 plans but were not checked against the CPU (`expected/` has both).

Config unless noted: `CACHE_MODE=os`, 300 GB disk tier, other settings default (`make_config.sh`).
"Warm" is the 2nd/3rd run in the same process. "First run" is the 1st run in a new process: at
SF3000 it followed a page-cache drop (cold); at SF1000 the cache was not dropped. On 2 GPUs the
first run also includes connecting NIXL to the peer.

## Q5 (TPC-H spec SQL, `sql/q5.sql`)

| SF | 1 GPU first run | 1 GPU warm | 2 GPUs first run | 2 GPUs warm | 2-GPU speedup (warm) |
|---|---|---|---|---|---|
| 1000 | 3.8 s | 3.40-3.49 s | 8.9 s | 1.83-1.85 s | 1.9x |
| 3000 (cold) | 61.1 s | 38.1-38.6 s | 43.1 s | 30.6-31.2 s | 1.25x |

## Q5, Theseus-shaped SQL (`sql/q5_theseus.sql`)

Same query rewritten so DuckDB plans it like the Theseus plan: nation x region first, customers
semi-joined to the ASIA nations, a partial aggregate on (l_suppkey, c_nationkey) before the
supplier join.

| SF | 1 GPU first run | 1 GPU warm | 2 GPUs first run | 2 GPUs warm | 2-GPU speedup (warm) |
|---|---|---|---|---|---|
| 1000 | 4.3 s | 3.63-3.65 s | 3.5 s | 1.88-1.93 s | 1.9x |
| 3000 (cold) | 28.1 s | 10.4-10.6 s | 28.7 s | 6.27-6.37 s | 1.66x |

At SF3000 the Theseus shape is 3.6x faster on 1 GPU and 4.9x faster on 2 GPUs than the spec SQL.

## Q12 (`sql/q12.sql`), SF1000

| Scan mode | 1 GPU warm | 2 GPUs warm |
|---|---|---|
| `CACHE_MODE=none` (O_DIRECT, one NVMe)* | 5.0 s | 5.2 s |
| `CACHE_MODE=os` (page cache) | 3.3-3.5 s | 1.96-2.03 s |

\* Measured on the branch before the DuckDB v2 rebase; the page-cache row is on v2.

With O_DIRECT both are capped by the single NVMe (~32 GB of columns in ~5 s), so a second GPU
gains nothing; through the page cache the exchange path scales.

## Why SF3000 scales less than SF1000

Q5 reads ~156 GB of columns at SF3000; with 181 GB of RAM only ~78 GB stayed in the page cache,
so warm runs still read half their data from one NVMe, and the original plan spills heavily
(GPU -> host). See `TUNING.md` for what to change with more RAM, VRAM or GPUs.

## Reproduce

```bash
QUERIES="q5 q5_theseus" SFS="1000 3000" GPU_COUNTS="1 2" RUNS=3 scripts/bench.sh
```

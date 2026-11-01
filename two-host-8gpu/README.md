# Two hosts, 8 GPUs: TPC-H SF30000 and SF100000

Distributed Sirius on 8 GB200 GPUs across two hosts (gcn-17 and gcn-18, 4 ranks each).

- **Sirius branches:**
  - SF30000 baseline: [`aocsa/sirius@distributed-exchange-cross-node-tcp-lanes`](https://github.com/aocsa/sirius/tree/distributed-exchange-cross-node-tcp-lanes)
    (`5bc904c9`), on top of `distributed-sirius-gateway` (`a900d827`).
  - SF100000, all 22 queries: [`aocsa/sirius@wip/sf100k-q9-q10-q18`](https://github.com/aocsa/sirius/tree/wip/sf100k-q9-q10-q18)
    (`161338b6`), one WIP commit on top of `5bc904c9`. It adds the scheduler fixes, the UCX
    CUDA-hook fix (Q14), the dead-pid exchange shared-memory sweep, Q18's complete-group HAVING
    pushdown and the whole-table Bloom-filter skip.
- **Exchange paths:** ranks on the same host exchange over NVLink. Ranks on different hosts use
  NIXL/UCX TCP "lanes" over pinned host memory, on bond0.

| Read | For |
|---|---|
| [notes/REPORT-sf100k-22q.md](notes/REPORT-sf100k-22q.md) | **All 22 queries at SF100000 against SF30000**, the 4x rule, the Q14 regression, the Q9/Q10/Q18 summary |
| [notes/PLAN-sf100k-q9-q10-q18.md](notes/PLAN-sf100k-q9-q10-q18.md) | Q9, Q10, Q18: every step, measurement and telemetry finding; pitfalls |
| [notes/PLAN-sf100k-q3-q10-q13.md](notes/PLAN-sf100k-q3-q10-q13.md) | Q3, Q10, Q13: the earlier round (Bloom size, BUILD_PROBE budgets, Q13 memory) |
| [results/sf30k.md](results/sf30k.md) | All 22 queries at SF30000: times, answers checked against the 4-GPU run, spill, memory |
| [results/sf100k.md](results/sf100k.md) | The first SF100000 round: the five queries without `lineitem` |
| [results/sf30k-4gpu.md](results/sf30k-4gpu.md) | The 4-GPU, one-host SF30000 baseline (scripts on this repo's `sf30k-unpinned` branch) |

## Headline

**SF30000** (`5bc904c9`, `configs/p180.yaml`): all 22 queries match the 4-GPU answers, with no disk
spill. Totals are 1409.3 s cold and 969.9 s hot.

**SF100000** (`161338b6`, `configs/p180-bp64-bloom8.yaml`): the goal is each query within **4x**
its SF30000 time, in both the cold and the hot run.

- 21 of 22 queries pass. Q1's cold run passes (3.94x), but its hot run hit the earlier 3.5x cap,
  so Q1 needs a rerun under the 4x cap.
- Every query that finished passed `sanity_sf100k.py`.
- Q2–Q22 total 4329.2 s cold and 2942.5 s hot, against 1370.2 s and 937.9 s at SF30000
  (**3.16x / 3.14x** for 3.33x the data).

| Query | SF30000 cold / hot (s) | SF100000 cold / hot (s) | Ratio | Run |
|---|---|---|---|---|
| Q1 | 39.1 / 32.0 | 153.9 / - | 3.94x / - | `sf100k-rest`, hot run capped |
| Q2 | 27.4 / 17.9 | 80.5 / 47.9 | 2.94x / 2.68x | `sf100k-rest` |
| Q3 | 75.3 / 44.1 | 248.0 / 143.7 | 3.29x / 3.26x | `sf100k-q3max8` |
| Q4 | 53.1 / 26.7 | 171.6 / 89.9 | 3.23x / 3.37x | `sf100k-rest` |
| Q5 | 78.0 / 47.5 | 250.8 / 151.7 | 3.22x / 3.19x | `sf100k-rest` |
| Q6 | 47.0 / 27.1 | 151.2 / 83.9 | 3.22x / 3.09x | `sf100k-rest` |
| Q7 | 75.4 / 53.9 | 275.3 / 168.9 | 3.65x / 3.13x | `sf100k-rest` |
| Q8 | 81.3 / 63.3 | 248.1 / 202.7 | 3.05x / 3.20x | `sf100k-rest` |
| Q9 | 134.0 / 113.4 | 430.6 / 374.0 | 3.21x / 3.30x | `sf100k-lanes8b` (stats, 8 lanes) |
| Q10 | 94.6 / 65.8 | 243.6 / 157.6 | 2.58x / 2.40x | `sf100k-stats2` (stats) |
| Q11 | 23.5 / 15.0 | 66.2 / 49.1 | 2.82x / 3.27x | `sf100k-rest` |
| Q12 | 70.2 / 33.2 | 218.9 / 109.0 | 3.12x / 3.28x | `sf100k-rest` |
| Q13 | 26.2 / 24.1 | 76.2 / 75.8 | 2.91x / 3.15x | `sf100k-q13recheck` |
| Q14 | 63.1 / 37.5 | 210.2 / 121.8 | 3.33x / 3.25x | `sf100k-q14fix` |
| Q15 | 56.0 / 31.4 | 179.2 / 106.0 | 3.20x / 3.38x | `sf100k-rest` |
| Q16 | 16.6 / 9.1 | 42.0 / 22.6 | 2.52x / 2.47x | `sf100k-rest` |
| Q17 | 77.1 / 59.2 | 233.7 / 168.7 | 3.03x / 2.85x | `sf100k-rest` |
| Q18 | 97.4 / 68.6 | 305.0 / 271.0 | 3.13x / 3.95x | `sf100k-q18having` (stats, 8 lanes, HAVING pushdown) |
| Q19 | 70.0 / 41.3 | 246.6 / 140.9 | 3.52x / 3.41x | `sf100k-rest` |
| Q20 | 62.5 / 48.4 | 204.6 / 135.8 | 3.28x / 2.80x | `sf100k-rest` |
| Q21 | 126.2 / 99.6 | 405.8 / 294.3 | 3.22x / 2.96x | `sf100k-rest` |
| Q22 | 15.3 / 10.8 | 41.1 / 27.2 | 2.68x / 2.53x | `sf100k-rest` |

Each run's `results.jsonl` is in [results/runs/](results/runs). Q18's SF30000 time with the
HAVING pushdown is 67.4 / 59.4 s (`sf30k-having3`); the table keeps the original SF30000 times as
the reference.

## Layout

| Path | What |
|---|---|
| `scripts/build_sirius.sh` | Builds Sirius with NIXL in `WT`; `LIB_ONLY=1` relinks and installs only `libsirius.so` |
| `scripts/run_sf30k.sh`, `run_sf100k.sh` | Runners: NUMA guards on both hosts, then each query twice (cold, hot) |
| `scripts/run_rest_sf100k.sh` | SF100000 runner that caps each query at `RATIO` (4) x its SF30000 cold+hot time + 60 s |
| `scripts/xnode_tpch.sh` | One query: waits for quiet hosts, sweeps stale exchange shared memory, writes the SQL and a config per host, starts 4 ranks per host, summarizes |
| `scripts/host_ranks.sh` | Starts one host's ranks with the exchange environment |
| `scripts/gen_sql.py` | Views for the tables the query uses, plus the query; `STATS=1` adds column statistics |
| `scripts/analyze_stats.sh` | Writes `stats/<dataset>.csv`: exact min/max/null count of the key columns, from the Parquet footers |
| `scripts/summarize.py`, `results.py` | Parses rank 0's answers and checks them against a reference |
| `scripts/sanity_sf100k.py` | SF100000 checks for all 22 queries (no reference answers exist at that scale) |
| `scripts/report.py`, `mem_stats.py` | The SF30000 table; spill and host-pool peaks |
| `scripts/numa_guard.sh` | Kills this build's ranks if a NUMA node drops below 15 GB free |
| `scripts/cpu_ref.sh` | DuckDB CPU answers, for small scale factors |
| `tools/telemetry/` | Telemetry readers: per-pipeline and per-operator timing, reservations, one task's events |
| `configs/` | Sirius configs (below) |
| `stats/` | Column statistics of the SF30000 and SF100000 datasets |
| `notes/` | The SF100000 report and the two plan/investigation logs |
| `results/` | Reports, per-run `results.jsonl`, the ranks' environment |

### Configs

| Config | Used for | Differs from `p180.yaml` |
|---|---|---|
| `p180.yaml` | SF30000 baseline | 180 GiB host pool, 1 TB disk spill per rank, 4 GiB Bloom filter |
| `p180-bp64.yaml` | experiments | BUILD_PROBE resident and hash-table budgets of 64 GiB |
| `p180-bp64-bloom8.yaml` | **all SF100000 results** and the Q18 SF30000 reruns | 64 GiB budgets, 8 GiB Bloom filter |
| `p180-bp64-bloom16.yaml` | Q3 experiment | 64 GiB budgets, 16 GiB Bloom filter |
| `p180-bp64-bloom8-gpu160.yaml` | workaround attempt for leaked GPU memory (did not help) | as `bloom8`, GPU pool capped at 160 GiB |

## Reproduce

### Setup

- **Hosts:** presto-gb200-gcn-17 (10.87.140.52) and presto-gb200-gcn-18 (10.87.140.53), 4x GB200
  each; driver 580.105.08, CUDA 13.0, UCX 1.21.0 (`~/tools/ucx-install`), NIXL in
  `~/tools/nvda_nixl`. Details in [results/sf30k.md](results/sf30k.md).
- **Shared filesystem:** clone this repo where both hosts see it at the same path (`/scratch`
  here), along with the build, the data, the spill and the output directory.
- **SSH:** run from gcn-17, with passwordless SSH to both hosts' bond0 addresses (gcn-17 to itself
  included).
- **Data:** `tpchgen-cli` 2.0.2, decimal128, about 4.3 GB per Parquet file:
  `/scratch/prestouser/aocsa/tpch_sf30000` and `/scratch/prestouser/aocsa/tpch_sf100000` (lineitem
  in 6,000 files).
- **Shell:** `export PATH=$HOME/.pixi/bin:$PATH` and `unset AI_AGENT CLAUDECODE CLAUDE_CODE
  CURSOR_AGENT` before any DuckDB CLI.

| Variable | Default |
|---|---|
| `WT` (Sirius checkout and build) | `/scratch/prestouser/aocsa/wt/xnode-staging` |
| `HOSTS` (bond0 addresses) | `10.87.140.52 10.87.140.53` |
| `DATA` | `/scratch/prestouser/aocsa/tpch_sf30000` or `/scratch/prestouser/aocsa/tpch_sf100000` |
| `SPILL_ROOT` | `/scratch/prestouser/aocsa/sirius-spill-xnode` |
| `REF_DIR` (rank 0 logs of the 4-GPU SF30000 run) | `two-host-8gpu/refs/sf30k` |
| `CONFIG` | `configs/p180.yaml` |
| `STATS`, `STATS_DIR` | off; `two-host-8gpu/stats` |
| `SIRIUS_PRIORITY_DOWNSTREAM_FIRST`, `SIRIUS_EXCHANGE_TCP_LANES`, `SIRIUS_EXCHANGE_RECV_FRAMES_PER_PEER` | unset (Sirius defaults: off; 16 lanes shared by a host's ranks); passed on to the ranks when set |

The reference answers and raw run directories are not in git (Q11's answer alone is 1.4 GB).
On the cluster:
- References: `/scratch/prestouser/aocsa/bench-xnode/tpch2host/refs/sf30k/`.
- Runs: `/scratch/prestouser/aocsa/bench-xnode/tpch2host/runs/<run name>/`.
- A backup of the scripts, refs and stats is in
  `/scratch/prestouser/aocsa/backups/2026-11-01-sf100k/`.

### Build

```bash
git clone --recursive -b wip/sf100k-q9-q10-q18 git@github.com:aocsa/sirius.git $WT   # or a worktree
WT=$WT scripts/build_sirius.sh                 # configure once, then pixi run make
WT=$WT LIB_ONLY=1 scripts/build_sirius.sh      # after a C++ change: relink and install libsirius.so only
```

- The ranks run `sirius-duckdb/build/release/duckdb` with the Sirius extension, which loads
  `build/release/install/lib/libsirius.so`.
- `LIB_ONLY=1` installs the library by copy then rename. Never `cp` over the installed file:
  ranks that have it mapped crash.
- Do not build while ranks run, and check `git log -1` in `WT` before a run: results in the table
  above are from `161338b6`.

### Test

1. **Hosts idle:** on both boxes, `nvidia-smi` shows no other process and
   `pgrep -u $(id -u) -x duckdb` finds nothing. Ports 27200–27207 must be free.
   `xnode_tpch.sh` also waits for this before each query.
2. **Small-scale answers:** `./cpu_ref.sh 18 ../refs` writes DuckDB's CPU answer at SF100, then
   `SF=100 REF=../refs/q18_sf100.log ./xnode_tpch.sh 18 ../runs/sf100` runs Q18 on the 8 ranks
   and checks it.
3. **SF30000 answers:** rank 0's logs of the 4-GPU run go in `refs/sf30k/q<NN>_sf30000.log` (or
   `REF_DIR`). Any change to the planner should be run at SF30000 against them first, e.g.:

   ```bash
   cd two-host-8gpu/scripts
   CONFIG=../configs/p180-bp64-bloom8.yaml SIRIUS_PRIORITY_DOWNSTREAM_FIRST=1 \
     ./run_sf30k.sh ../runs/sf30k-check 18
   ```

   `summarize.py` writes `vs_ref: ok` per run into `../runs/sf30k-check/results.jsonl`.

### Bench

```bash
cd two-host-8gpu/scripts
./run_sf30k.sh ../runs/sf30k $(seq 1 22)                    # SF30000 baseline, configs/p180.yaml

./analyze_stats.sh /scratch/prestouser/aocsa/tpch_sf100000   # once per dataset; not next to a benchmark
export CONFIG=../configs/p180-bp64-bloom8.yaml SIRIUS_PRIORITY_DOWNSTREAM_FIRST=1
./run_rest_sf100k.sh ../runs/sf100k-rest 1 2 4 5 6 7 8 11 12 14 15 16 17 19 20 21 22
./run_rest_sf100k.sh ../runs/sf100k-q3 3
./run_rest_sf100k.sh ../runs/sf100k-q13 13
STATS=1 ./run_rest_sf100k.sh ../runs/sf100k-stats 10
STATS=1 SIRIUS_EXCHANGE_TCP_LANES=8 ./run_rest_sf100k.sh ../runs/sf100k-lanes8 9 18
```

- Each query gets fresh processes and runs twice (cold, then hot). The cap is per query; a capped
  query's ranks are killed and the next query starts.
- The runners append to `<out>/run.log` and `<out>.driver.log`, and `summarize.py` appends one
  line per query to `<out>/results.jsonl`.
- `STATS=1` changes the plans of Q2, Q3, Q7, Q8, Q21 and Q22 as well. Those were measured without
  it, and have not been rerun with it.

### Analysis

```bash
python3 sanity_sf100k.py ../runs/sf100k-rest     # SF100000 answer checks
python3 report.py ../runs/sf30k                   # the SF30000 table against the 4-GPU run
python3 mem_stats.py ../runs/sf100k-rest/q13      # disk spill and host-pool peak per host
```

The SF100000-to-SF30000 ratios in the table are each run's `times_s` from `results.jsonl`, against
[results/sf30k-results.jsonl](results/sf30k-results.jsonl) (the last line per query counts).

### Profile

Each query directory holds `rank_<r>.log` (rank 0 has the answers), `logs/<host>/` (Sirius logs),
`env-<host>.txt`, `q.sql`, the per-host configs and `telemetry_data/<uuid>/`, one directory per
rank. A pipeline's label lists its operators with their ids, e.g.
`GPU_SCAN(0) -> EXCHANGE_SINK(1)`; the tools below take an operator id or a label substring.

```bash
T=../runs/sf100k-q18having/q18/telemetry_data/<uuid>
python3 ../tools/telemetry/pipes.py $T                         # per pipeline: tasks, span, wait / prepare / compute
python3 ../tools/telemetry/pipeline_ops.py $T                  # the same, keyed by operator ids
python3 ../tools/telemetry/pipeline_op_split.py $T 'CONCAT(4)'   # compute per operator in the pipelines whose label has CONCAT(4)
python3 ../tools/telemetry/operator_compute.py $T 9            # compute spans of operator 9 (e.g. a CONCAT)
python3 ../tools/telemetry/operator_tasks.py $T 9              # event chains of the tasks that ran operator 9
python3 ../tools/telemetry/pipeline_reservations.py $T 'CONCAT(4)'   # reservation sizes and waits
python3 ../tools/telemetry/largest_task.py $T 'CONCAT(4)'  # the largest task's full event chain
```

- **CPU profile of a slow phase** (this found the Q14 UCX hook problem), on rank 0's host while
  the phase runs:

  ```bash
  perf record -F 199 -g -p <rank 0 pid> -o /tmp/q14.perf -- sleep 40
  perf report -i /tmp/q14.perf --no-children --sort comm,sym --stdio -g none
  ```

- **Bloom-filter merge stalls:** search the Sirius logs for the dynamic-filter merge timeout
  (120 s). A rank that times out publishes no filter, and the build's CONCAT task holds the
  pipeline for the whole timeout. See "Bloom merge stall" in
  [notes/PLAN-sf100k-q9-q10-q18.md](notes/PLAN-sf100k-q9-q10-q18.md).

## Pitfalls

- **Leaked exchange shared memory.** A rank killed at a cap leaves its 448 MiB
  `/dev/shm/sirius-exchange-*` mirror behind. About 45 GB per host built up and got ranks
  OOM-killed. `xnode_tpch.sh` now sweeps the mirrors of dead pids on both hosts before and after
  each query.
- **Leaked GPU memory.** After killed ranks, gcn-17's GPUs 1 and 2 held about 18 GB each with no
  visible owner, and the 0.95 x GPU pool no longer fit. `nvidia-smi -r` needs root; a 160 GiB
  pool did not help. Check `nvidia-smi --query-gpu=memory.used --format=csv` on both hosts before
  a run.
- **`STATS=1` needs the CSV.** Without `stats/<dataset>.csv`, `gen_sql.py` fails and
  `xnode_tpch.sh` stops; earlier versions wrote an empty `q.sql` and the ranks hung.
- **Scripts in use.** Never edit a runner while it runs: bash reads scripts as it goes. Write a new
  file and `mv` it over the old one.
- **Shared ports and boxes.** Ports 9494, 9500 and 27000–27207 are used by other sessions on
  these hosts; check them first. Never `pkill -f` a pattern that also matches your own shell; use
  `pgrep -u $(id -u) -x duckdb`.

# TPC-H SF30000 on 8 GPUs across two hosts (gcn-17 + gcn-18), distributed Sirius

**Outcome:** all 22 queries pass.

- **Answers:** every query matches the 4-GPU SF30000 run on both iterations (Q11 by row count, see
  below).
- **Totals:** 1424.1 s cold and 981.6 s warm, against 2542.6 s / 1696.8 s on 4 GPUs on one host:
  **1.79× / 1.73×** faster.
- **Spill:** no disk spill at all, against 1.3 TB on 4 GPUs.
- **Health:** 0 failures, 0 NUMA-guard kills.
- **Wall time:** 50 min for the 22 queries, process startup and teardown included (76 min on
  4 GPUs).

## Results

Each query runs in fresh processes, twice: the first run is cold, the second warm. Column notes:

- **4-GPU columns:** the 4× GB200, one-host run of
  `/tmp/aocsa/tpch22/sf30k/sf30k-report.md` on gcn-18 (a copy is in
  [sf30k-4gpu.md](sf30k-4gpu.md); the scripts are on this repo's `sf30k-unpinned` branch).
- **Answers vs 4-GPU:** each run's result against rank 0's answer from the 4-GPU run. No official
  SF30000 answers exist.
- **Disk spill:** host→disk bytes from the Sirius downgrade log, summed over both runs and all
  ranks.
- **Host-pool peak:** the largest per-rank host-pool peak.
- **Min free:** the lowest free memory on NUMA node 0 / node 1 of each host during the query.

| Query | Cold (s) | Warm (s) | 4-GPU cold / warm (s) | Speedup cold / warm | Rows | Answers vs 4-GPU | Disk spill (4-GPU) | Host-pool peak (4-GPU) | Min free, gcn-17 · gcn-18 |
|---|---:|---:|---:|---:|---:|---|---:|---:|---|
| Q1 | 39.1 | 32.0 | 63.0 / 54.6 | 1.61× / 1.70× | 4 | match | 0 GB (0 GB) | 0 GiB (0 GiB) | 401G/402G · 397G/406G |
| Q2 | 27.4 | 17.9 | 40.4 / 23.3 | 1.48× / 1.30× | 100 | match | 0 GB (0 GB) | 1 GiB (3 GiB) | 403G/409G · 402G/406G |
| Q3 | 82.0 | 48.1 | 129.0 / 83.9 | 1.57× / 1.74× | 10 | match | 0 GB (0 GB) | 48 GiB (158 GiB) | 317G/333G · 308G/318G |
| Q4 | 53.1 | 26.7 | 90.0 / 52.1 | 1.70× / 1.95× | 5 | match | 0 GB (0 GB) | 7 GiB (12 GiB) | 388G/390G · 380G/395G |
| Q5 | 78.0 | 47.5 | 132.9 / 76.9 | 1.70× / 1.62× | 5 | match | 0 GB (0 GB) | 9 GiB (10 GiB) | 389G/389G · 383G/396G |
| Q6 | 47.0 | 27.1 | 87.4 / 49.1 | 1.86× / 1.81× | 1 | match | 0 GB (0 GB) | 0 GiB (0 GiB) | 399G/403G · 391G/405G |
| Q7 | 75.4 | 53.9 | 138.8 / 89.5 | 1.84× / 1.66× | 4 | match | 0 GB (0 GB) | 11 GiB (18 GiB) | 375G/382G · 370G/382G |
| Q8 | 81.3 | 63.3 | 128.0 / 105.4 | 1.57× / 1.67× | 2 | match | 0 GB (0 GB) | 10 GiB (19 GiB) | 377G/383G · 371G/392G |
| Q9 | 134.0 | 113.4 | 217.1 / 139.2 | 1.62× / 1.23× | 175 | match | 0 GB (89 GB) | 73 GiB (180 GiB) | 282G/272G · 256G/277G |
| Q10 | 94.6 | 65.8 | 198.8 / 124.8 | 2.10× / 1.90× | 20 | match | 0 GB (115 GB) | 92 GiB (180 GiB) | 243G/296G · 251G/270G |
| Q11 | 23.5 | 15.0 | 40.4 / 25.8 | 1.72× / 1.72× | 29,971,985 | same row count (size only) | 0 GB (0 GB) | 0 GiB (0 GiB) | 383G/394G · 401G/409G |
| Q12 | 70.2 | 33.2 | 128.9 / 60.1 | 1.84× / 1.81× | 2 | match | 0 GB (0 GB) | 4 GiB (4 GiB) | 390G/398G · 388G/401G |
| Q13 | 34.4 | 31.9 | 55.9 / 50.7 | 1.63× / 1.59× | 52 | match | 0 GB (0 GB) | 36 GiB (127 GiB) | 368G/411G · 402G/406G |
| Q14 | 63.1 | 37.5 | 123.9 / 68.7 | 1.96× / 1.83× | 1 | match | 0 GB (0 GB) | 26 GiB (47 GiB) | 349G/351G · 341G/355G |
| Q15 | 56.0 | 31.4 | 105.7 / 62.5 | 1.89× / 1.99× | 1 | match | 0 GB (0 GB) | 0 GiB (0 GiB) | 396G/403G · 391G/404G |
| Q16 | 16.6 | 9.1 | 20.9 / 9.6 | 1.26× / 1.05× | 27,840 | match | 0 GB (0 GB) | 9 GiB (14 GiB) | 394G/395G · 388G/392G |
| Q17 | 77.1 | 59.2 | 134.4 / 103.4 | 1.74× / 1.75× | 1 | match | 0 GB (0 GB) | 5 GiB (2 GiB) | 391G/400G · 389G/404G |
| Q18 | 97.4 | 68.6 | 235.4 / 188.7 | 2.42× / 2.75× | 100 | match | 0 GB (1105 GB) | 128 GiB (180 GiB) | 221G/255G · 281G/151G |
| Q19 | 70.0 | 41.3 | 130.7 / 77.8 | 1.87× / 1.88× | 1 | match | 0 GB (0 GB) | 0 GiB (0 GiB) | 396G/403G · 391G/405G |
| Q20 | 62.5 | 48.4 | 102.6 / 70.9 | 1.64× / 1.46× | 5,079,748 | match | 0 GB (0 GB) | 3 GiB (1 GiB) | 392G/396G · 392G/398G |
| Q21 | 126.2 | 99.6 | 217.4 / 166.1 | 1.72× / 1.67× | 100 | match | 0 GB (0 GB) | 32 GiB (42 GiB) | 334G/336G · 326G/340G |
| Q22 | 15.3 | 10.8 | 21.0 / 13.7 | 1.37× / 1.27× | 7 | match | 0 GB (0 GB) | 7 GiB (0 GiB) | 397G/403G · 393G/401G |
| **Total** | **1424.1** | **981.6** | **2542.6 / 1696.8** | **1.79× / 1.73×** | | | **0 GB (1309 GB)** | | |

**Q18 rerun** ([sf30k-q18-rerun-results.jsonl](sf30k-q18-rerun-results.jsonl)), once another job's disk use had stopped:
100.5 s cold, 66.3 s warm, answers match, 6 GB spilled on gcn-18, a 165 GiB host-pool peak. That
is within run-to-run noise of the table's 97.4 / 68.6 s.

### What the numbers say

- **Speedups cluster around 1.6–1.9×.** Each GPU scans half the data the 4-GPU run gave it, and
  the cross-host exchange costs some of the ideal 2×.
- **Spilling queries gain the most:** Q18 (2.42× / 2.75×) and Q10 (2.10× / 1.90×). With half the
  data per rank they stay in the host pool and no longer write to disk.
- **Small or exchange-heavy queries gain the least:** Q16 (1.26× / 1.05×), Q22, Q2, and Q9 warm
  (1.23×).
  - Fixed per-query costs weigh more on short queries.
  - Q9 moves the most data between ranks. Across hosts that goes over one 200 Gb/s port at
    about 20 GB/s each way (see "Network").

## Setup

### Hardware and system

- **Hosts:** presto-gb200-gcn-17 (10.87.140.52) and presto-gb200-gcn-18 (10.87.140.53).
  - Each host has 4× NVIDIA GB200 and 2 Grace CPUs (144 cores, 2 CPU NUMA nodes of about 480 GB).
  - GPUs 0,1 are on NUMA node 0; GPUs 2,3 on node 1.
- **Software:**
  - driver 580.105.08, CUDA 13.0;
  - Ubuntu 24.04.1, kernel 6.14.0-1013-nvidia-64k;
  - UCX 1.21.0 (`~/tools/ucx-install`);
  - NIXL built from `~/tools/nixl-src` at `4d030b94`, installed in `~/tools/nvda_nixl`.
- **Network:** `bond0`, 802.3ad over two 200 Gb/s ports, `xmit_hash_policy layer2`.
  - With layer2 hashing, all traffic between the two hosts takes one port.
  - A layer3+4 policy (needs an admin) would spread the TCP lanes over both ports.
- **Data:** `/scratch/prestouser/aocsa/tpch_sf30000`, 12.7 TB of Parquet on GPFS (lineitem 7.5 TB in
  1,800 files). Both hosts read the same files.

### Build

- **Branch:**
  [`distributed-exchange-cross-node-tcp-lanes`](https://github.com/aocsa/sirius/tree/distributed-exchange-cross-node-tcp-lanes)
  at `5bc904c9` ("feat(exchange): fast cross-node exchange over pinned host memory and parallel TCP
  lanes"). It sits on `distributed-sirius-gateway` (`a900d827`).
- **Submodules:** duckdb `v2.0.0` (`d54cbbe3`), cucascade `1a928109`.
- **No pinned tables:** the build has none of the `pin_table` commits.
- **Worktree:** `/scratch/prestouser/aocsa/wt/xnode-staging`, built with
  `/scratch/prestouser/aocsa/wt/xnode-staging.build.sh`:

```bash
cd /scratch/prestouser/aocsa/wt/xnode-staging
export PATH=$HOME/.pixi/bin:$PATH
pixi run cmake --preset release -DSIRIUS_DUCKDB_SOURCE_DIR=$PWD/duckdb \
  -DSIRIUS_WITH_NIXL=ON -DSIRIUS_NIXL_PREFIX=/home/aocsa/tools/nvda_nixl
pixi run make
```

The ranks run `sirius-duckdb/build/release/duckdb` with
`sirius-duckdb/build/release/extension/sirius/sirius.duckdb_extension`.

### Sirius configuration

This is the 4-GPU run's "p180" config, unchanged ([../configs/p180.yaml](../configs/p180.yaml)). Each
host's copy has `@SPILL_DIR@` replaced by its own spill directory,
`/scratch/prestouser/aocsa/sirius-spill-xnode/<host IP>`. Each query directory keeps both copies
(`q<NN>/sirius-<host IP>.yaml`).

```yaml
sirius:
  memory:
    host: { capacity_bytes: 180Gi }
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

### Distributed setup

- **Ranks:** 8 DuckDB processes, one per GPU (`CUDA_VISIBLE_DEVICES=i`).
  - Ranks 0–3 run on gcn-17 GPUs 0–3, ranks 4–7 on gcn-18 GPUs 0–3.
  - Rank r listens on port 27200+r.
  - Every rank gets the same peer list:
    `10.87.140.52:27200,…,10.87.140.52:27203,10.87.140.53:27204,…,10.87.140.53:27207`.
- **Session settings**, before the query file:
  - `LOAD '<extension>'`
  - `SET enable_duckdb_fallback = false` (a rank falling back to the CPU would return a wrong
    answer)
  - `SET sirius_exchange_peers = '<list>'`
  - `SET sirius_exchange_rank = <r>`
- **Query file:** a view per table over `<data>/<table>/*.parquet`, `.mode csv`, `.timer on`, then
  the query twice. The text comes from `test/tpch_performance/queries.py` at SF30000, the same as
  the 4-GPU run's (checked for all 22).
- **Exchange path,** chosen per peer from the kernel boot id each rank sends in its hello:
  - **Same host:** NIXL GPU-to-GPU writes (NVLink).
  - **Other host:** TCP lanes, logged as "host-staged over TCP lanes". All 32 cross-host and 24
    same-host pairs used their expected path.
- **Exchange defaults,** none overridden:
  - 4 TCP lanes per rank: 16 per host ÷ 4 ranks, `SIRIUS_EXCHANGE_TCP_LANES` unset.
  - 2 pinned 8 MiB send slots per lane.
  - 8 MiB frames and 8 receive frames per peer, `SIRIUS_EXCHANGE_RECV_FRAMES_PER_PEER` unset.
  - `SIRIUS_EXCHANGE_HOST_STAGING` unset (automatic).
- **Environment** per rank ([env-gcn-17.txt](env-gcn-17.txt);
  each query directory keeps its own `env-<host>.txt`):

```bash
source scripts/nixl_env.sh           # NIXL_PREFIX, NIXL_PLUGIN_DIR, LD_LIBRARY_PATH (NIXL + UCX first)
UCX_TLS=cuda_copy,cuda_ipc,tcp,self  # from nixl_env.sh
UCX_NET_DEVICES=bond0                # multi-homed hosts: otherwise UCX advertises unreachable addresses
SIRIUS_CONFIG_FILE=<q dir>/sirius-<host IP>.yaml
SIRIUS_LOG_DIR=<q dir>/logs/<host>
unset AI_AGENT CLAUDECODE CLAUDE_CODE CURSOR_AGENT
```

- **Per-rank timeout:** 3600 s (`timeout -s KILL`).

### Method and checks

- **Before each query,** the launcher waits until both hosts are quiet:
  - no `duckdb` process from anyone;
  - no GPU compute process;
  - gcn-18's `/tmp/aocsa/tpch22/sf30k/scripts/quiet.sh` passes.
- **During the run,** `numa_guard.sh` on each host logs free memory per NUMA node every 2 s
  (`numa-<host IP>.log`). It kills this build's ranks if a node drops below 15 GB; it never did.
- **Answers:**
  - Each run is compared with the first run and with the 4-GPU run's rank-0 answer, using
    `results.py` (numeric tolerance 1e-6 relative / 0.01 absolute, any row order).
  - **Q11** returns about 30 million rows. Only its row count was compared with the 4-GPU run
    (29,971,985 both runs); `summarize.py` does this for any result over 1 million rows. Q20
    (5.1 million rows) was compared in full before that rule existed.
- **Spill directories** are cleared before and after each query.

## Reproduce

The scripts are in [../scripts/](../scripts) and the config in [../configs/](../configs).

- **Shared filesystem:** clone this repo where both hosts see it at the same path (`/scratch`
  here), along with the build, the data and the output directory.
- **SSH:** run from gcn-17, with passwordless SSH to both hosts' bond0 addresses (gcn-17 to itself
  included).
- **Overridable defaults:**
  - `WT=/scratch/prestouser/aocsa/wt/xnode-staging` (the Sirius checkout and build);
  - `HOSTS="10.87.140.52 10.87.140.53"`;
  - `SPILL_ROOT=/scratch/prestouser/aocsa/sirius-spill-xnode`;
  - `DATA` (per runner).

1. **Build** the branch as above.
2. **Check that the hosts are idle:** both boxes run `nvidia-smi` and `pgrep -x duckdb`. Ports
   27200–27207 must be free on both.
3. **Reference answers** (not in git: Q11's is 1.4 GB):
   - Put rank 0's logs of the 4-GPU run in `two-host-8gpu/refs/sf30k/q<NN>_sf30000.log`, or point
     `REF_DIR` at them.
   - On this cluster: `/tmp/aocsa/tpch22/sf30k/full22/r<N>-p180/q<NN>_sf30000/exchange_rank_0.log`
     on gcn-18.
   - A copy is in `/scratch/prestouser/aocsa/bench-xnode/tpch2host/refs/sf30k/`.
4. **Run** every query, two runs each, with the NUMA guard on both hosts:

   ```bash
   cd two-host-8gpu/scripts
   export PATH=$HOME/.pixi/bin:$PATH
   unset AI_AGENT CLAUDECODE CLAUDE_CODE CURSOR_AGENT
   ./run_sf30k.sh ../runs/sf30k $(seq 1 22)
   ```

   One query by hand: `SF=30000 DATA=/scratch/prestouser/aocsa/tpch_sf30000 TIMEOUT=3600
   REF=../refs/sf30k/q18_sf30000.log ./xnode_tpch.sh 18 ../runs/sf30k`.
5. **Report:**
   - `python3 report.py ../runs/sf30k` prints this table.
   - `python3 mem_stats.py ../runs/sf30k/q18` prints one query's spill and host-pool peak per
     host.

A smaller check that needs no 4-GPU answers:
- `./cpu_ref.sh <q> ../refs` writes DuckDB's CPU answer at SF100.
- `SF=100 REF=../refs/q18_sf100.log ./xnode_tpch.sh 18 ../runs/sf100` then runs that query on the
  8 ranks. Q9, Q18 and Q21 matched at SF100.

### Scripts

| Script | What it does |
|---|---|
| `run_sf30k.sh <out> <q>...` | Starts the NUMA guards, runs each query with `xnode_tpch.sh` at SF30000, stops the guards |
| `xnode_tpch.sh <q> <out>` | Waits for quiet hosts, writes `q.sql` and a config per host, starts 4 ranks per host (local and SSH), then summarizes |
| `host_ranks.sh` | Starts one host's 4 ranks with the environment above, inside the pixi environment |
| `gen_sql.py` | Writes the views, `.mode csv`, `.timer on` and the query N times |
| `summarize.py` | Parses rank 0's output, checks answers, appends to `results.jsonl` |
| `report.py`, `mem_stats.py` | This table; spill and host-pool peaks from the Sirius logs |
| `numa_guard.sh` | Free memory per NUMA node every 2 s; kills this build's ranks below 15 GB |
| `cpu_ref.sh` | DuckDB CPU answers (no Sirius) |

## Notes

- **NIXL startup messages.** Some runs log `E… nixl_agent.cpp:1489] getLocalMD: no backends
  support remote operations` at startup on gcn-17's ranks.
  - A neighbour fetched a rank's NIXL metadata between the agent's start and its UCX backend's
    creation. NIXL's listener answers with nothing, and the neighbour fetches again 200 ms later.
  - They are harmless. `summarize.py` drops these lines from the answers.
- **RMM messages.** `[error ] … FAILURE maximum pool size exceeded … 10.000000 MiB` lines appear
  in rank logs. They are non-fatal and predate this change: the 4-GPU run logged 2,634 of them,
  this run 1,973.
- **Bookkeeping fixes during the run:**
  - **Q3 and Q4:** their first-run lines in `results.jsonl` were written before the NIXL-line filter
    was restored, so they show extra rows. They were re-summarized from the same logs; the later
    line, which `report.py` uses, is the correct one.
  - **Q11:** its full answer comparison was stopped after 18 minutes, and its result was written
    with the row-count check. Its wall time (62.7 s) excludes that check.
- **Not tried:** a second network port (needs a layer3+4 bond hash), more TCP lanes or receive
  frames, or a larger host pool now that nothing spills.

## Files

In this repo ([.](.)):
- `sf30k-results.jsonl`: one line per query run, with times, rows and answer checks; the last line
  per query counts.
- `sf30k-q18-rerun-results.jsonl`: the Q18 rerun.
- `sf30k-table.md`: `report.py`'s output.
- `sf30k-4gpu.md`: the 4-GPU baseline report.
- `env-gcn-17.txt`, `env-gcn-18.txt`: the ranks' environment.

Raw run directories (not in git: Q11's rank 0 log alone is 1.4 GB) are in
`/scratch/prestouser/aocsa/bench-xnode/tpch2host/runs/sf30k/` and `…/runs/sf30k-q18-r2/`:
- `q<NN>/`:
  - each query's rank logs (`rank_<r>.log`; rank 0 holds the answers);
  - `q.sql`, `sirius-<host IP>.yaml`, `env-<host>.txt`, `launch.txt` (peer list);
  - `logs/<host>/` (Sirius logs) and `telemetry_data/`.
- `run.log`: the timeline.
- `numa-<host IP>.log`: free memory per NUMA node.

# Two hosts, 8 GPUs: TPC-H SF30000 and SF100000

Distributed Sirius on 8 GB200 GPUs across two hosts (gcn-17 and gcn-18, 4 ranks each).

- **Sirius branch:** [`aocsa/sirius@distributed-exchange-cross-node-tcp-lanes`](https://github.com/aocsa/sirius/tree/distributed-exchange-cross-node-tcp-lanes)
  (commit `5bc904c9`).
- **Exchange paths:** ranks on the same host exchange over NVLink. Ranks on different hosts use
  NIXL/UCX TCP "lanes" over pinned host memory, on bond0.

| Read | For |
|---|---|
| [results/sf30k.md](results/sf30k.md) | **All 22 queries at SF30000**: times, answers checked against the 4-GPU run, spill, memory, how to reproduce |
| [results/sf100k.md](results/sf100k.md) | **Q2, Q11, Q13, Q16, Q22 at SF100000** (the queries without `lineitem`): times, sanity checks, Q13's memory pressure |
| [results/sf30k-4gpu.md](results/sf30k-4gpu.md) | The 4-GPU, one-host SF30000 baseline (scripts on this repo's `sf30k-unpinned` branch) |

## Headline

**SF30000:**
- All 22 queries match the 4-GPU answers.
- Totals: 1424.1 s cold and 981.6 s warm, against 2542.6 s / 1696.8 s on 4 GPUs (**1.79× /
  1.73×**).
- No disk spill, against 1.3 TB on 4 GPUs.

**SF100000:** times were taken while the `lineitem` generator was still running on the hosts.

| Query | Cold (s) | Warm (s) | Notes |
|---|---:|---:|---|
| Q2 | 73.9 | 40.4 | |
| Q11 | 66.3 | 51.4 | 93,496,926 rows |
| Q13 | 815.5 | 622.0 | GPU and host memory full, 2.9 TB disk spill |
| Q16 | 40.4 | 26.6 | |
| Q22 | 37.9 | 27.6 | |

All five pass the sanity checks.

## Layout

| Path | What |
|---|---|
| `scripts/run_sf30k.sh`, `run_sf100k.sh` | Runners: NUMA guards on both hosts, then each query twice (cold, warm) |
| `scripts/xnode_tpch.sh` | One query: waits for quiet hosts, writes the SQL and a config per host, starts 4 ranks per host, summarizes |
| `scripts/host_ranks.sh` | Starts one host's ranks with the exchange environment |
| `scripts/gen_sql.py` | Views for the tables the query uses, plus the query; refuses `lineitem` at SF ≥ 100000 |
| `scripts/summarize.py`, `results.py` | Parses rank 0's answers and checks them against a reference |
| `scripts/report.py`, `mem_stats.py` | The SF30000 table; spill and host-pool peaks |
| `scripts/sanity_sf100k.py` | SF100000 checks (no reference answers exist at that scale) |
| `scripts/numa_guard.sh` | Kills this build's ranks if a NUMA node drops below 15 GB free |
| `scripts/cpu_ref.sh` | DuckDB CPU answers, for small scale factors |
| `configs/p180.yaml` | The Sirius config (180 GiB host pool, 1 TB disk spill per rank) |
| `results/` | Reports, per-run `results.jsonl`, the ranks' environment |

## Requirements

- **Filesystem:** a shared one (here `/scratch`), seen at the same path on both hosts. It holds
  this repo, the Sirius build, the data, the spill and the output.
- **SSH:** passwordless, from the first host to both hosts.

Settings come from environment variables:

| Variable | Default |
|---|---|
| `WT` (Sirius checkout and build) | `/scratch/prestouser/aocsa/wt/xnode-staging` |
| `HOSTS` (bond0 addresses) | `10.87.140.52 10.87.140.53` |
| `DATA` | `/scratch/prestouser/aocsa/tpch_sf30000` or `/scratch/prestouser/aocsa/tpch_sf100000` |
| `SPILL_ROOT` | `/scratch/prestouser/aocsa/sirius-spill-xnode` |
| `REF_DIR` (rank 0 logs of the 4-GPU run) | `two-host-8gpu/refs/sf30k` |

The reference answers and raw run logs are not in git (Q11's answer alone is 1.4 GB). They are
on the cluster:
- References: `/scratch/prestouser/aocsa/bench-xnode/tpch2host/refs/sf30k/`.
- Runs: `/scratch/prestouser/aocsa/bench-xnode/tpch2host/runs/{sf30k,sf30k-q18-r2,sf100k}/`.

The full steps are in [results/sf30k.md](results/sf30k.md#reproduce) and
[results/sf100k.md](results/sf100k.md#reproduce).

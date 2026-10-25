# dist-sirius-scripts

Scripts and notes to build, set up, test and benchmark **distributed Sirius**: one SQL query
running across several GPUs, one process per GPU, with rows moved between ranks over
[NIXL](https://github.com/ai-dynamo/nixl). The focus is TPC-H **Q5** (and Q12).

Sirius branch: [`aocsa/sirius@exchange-operator-v2`](https://github.com/aocsa/sirius/tree/exchange-operator-v2)
(DuckDB v2). Design doc in that branch: `docs/super-sirius/exchange.md`.

| Read | For |
|---|---|
| [notes/RESULTS.md](notes/RESULTS.md) | Q5 / Q12 timings at SF1000 and SF3000 on 1 and 2 GPUs |
| [notes/TUNING.md](notes/TUNING.md) | **What to change with more GPUs, more VRAM, more host memory** |
| [notes/FINDINGS.md](notes/FINDINGS.md) | Bugs found and fixed on the way, and what is still open |
| [sf10k-unpinned-cold-read-fix/](sf10k-unpinned-cold-read-fix/README.md) | **TPC-H SF10000 on 4x GB200, unpinned: the io_uring cold-read fix** (22 queries 2128 → 807 s cold, 685 → 507 s warm), with `aocsa/sirius@7c99ebe1`: cause, standalone probe, scripts, configs, results |
| [dist-plan-opt/](dist-plan-opt/README.md) | **4x GB200, all 22 TPC-H queries at SF3000/SF10000** with `aocsa/sirius@dist-plan-opt`: runner, profiling tools, configs, ZSTD data rewrite, notes |

Headline (2x RTX PRO 4500, 32 GB each, SF3000, warm): Q5 as written takes 38.1 s on 1 GPU and
30.6 s on 2; the same query in the Theseus-shaped SQL takes 10.5 s on 1 GPU and **6.3 s on 2**.

## Requirements

- Linux x86_64 (tested; aarch64 untested), NVIDIA GPUs (one per rank), a driver for CUDA 13, the
  CUDA toolkit at `$CUDA_HOME` (default `/usr/local/cuda`).
- [pixi](https://pixi.sh) >= 0.78, git, gcc, make, curl, python3 with venv (`python3-venv` on
  Debian/Ubuntu); cargo (rustup) for the data generator.
- Disk: ~0.27 GB per TPC-H scale-factor unit (SF1000 = 265 GB, SF3000 = 802 GB) on fast local
  storage, plus room for the disk spill tier.
- RAM: see [TUNING.md](notes/TUNING.md). Q5 reads ~156 GB of columns at SF3000 (so ~52 GB at
  SF1000); with `CACHE_MODE=os`, that much page cache makes repeated runs fast.
- Optional: passwordless `sudo` to drop the page cache for cold runs.

## Steps

All settings come from environment variables with defaults in [scripts/common.sh](scripts/common.sh):
`WORK` (default `~/dist-sirius`), `DATA_ROOT` (default `$WORK/data`), `SPILL_DIR` (default
`$WORK/spill`), `CUDA_HOME`, `SIRIUS_BRANCH`. Point `DATA_ROOT` and `SPILL_DIR` at fast local
NVMe (the reference box used `/opt/dlami/nvme/tpch` and `/opt/dlami/nvme/sirius-disk`). On a shared GPU box, run each script under your GPU lock (the whole
script, not per process: the ranks of one query must run together).

```bash
# 1. NIXL 1.3.2 + UCX 1.21.0 (CUDA-enabled), into $WORK/tools
scripts/build_nixl_ucx.sh

# 2. Sirius with the NIXL exchange: library, unit tests, DuckDB shell + extension
scripts/build_sirius.sh

# 3. TPC-H data, verified row counts
scripts/gen_tpch.sh 1000
scripts/gen_tpch.sh 3000

# 4. Tests: C++ unit tests of the exchange/filters/byte ranges, then a 2-GPU fail-stop check
scripts/test_unit.sh
scripts/test_failstop.sh 1000

# 5. Benchmark Q5 (spec SQL and Theseus-shaped) on 1 and 2 GPUs, answers checked
QUERIES="q5 q5_theseus" SFS="1000 3000" GPU_COUNTS="1 2" RUNS=3 scripts/bench.sh
```

`bench.sh` writes `$WORK/runs/bench-<time>/results.csv` (`query,sf,gpus,run,seconds,answer`; the
answer values are explained in `tools/check_runs.py`: `ok`, `WRONG`, `ERROR`, `MISSING`, `n/a`...),
the Sirius config it used, and per-rank logs: `<query>-sf<SF>-<N>gpu/rank<i>/out.log` and `.../rank<i>/log/` (the Sirius
log: printed plan, spills, dynamic filters). With `COLD=1` (default) each process starts after a
page-cache drop and an empty spill directory. It exits non-zero if any run is not `ok` (or `n/a`).
An `ERROR` also covers a failed `LOAD`: DuckDB's shell keeps going and would answer on the CPU.

### What was tested

On the reference box: `make_config.sh`, `run_ranks.sh`, `run_single.sh`, `bench.sh` (Q5 and the
Theseus-shaped Q5 at SF1000 on 1 and 2 GPUs, answers checked, plus a deliberately wrong answer to
check the checker), `test_unit.sh` and `test_failstop.sh` all ran as published. `gen_tpch.sh` ran from an empty
directory at SF1 (clone, build, generate, verify), and Q5 at SF1 (every table a single file, so 2
ranks split each file by byte range) matched the TPC-H reference answer on 1 and 2 GPUs. A broken
config (failed `LOAD`) is reported `ERROR`, and killing one rank mid-query makes `run_ranks.sh`
kill the other `GRACE` seconds later, leaving no process behind. `build_nixl_ucx.sh`
and `build_sirius.sh` repeat the exact options and commands used to build that box (recovered from
its UCX `config.log` and NIXL meson log, and the Sirius build steps), but were not re-run from an
empty directory. Please open an issue if a step fails on a fresh machine.

## Running your own SQL

```bash
scripts/make_config.sh my.yaml                     # tune with env vars, see TUNING.md
CONFIG=my.yaml scripts/run_ranks.sh 4 my.sql out/  # 4 ranks on GPUs 0-3; prints rank 0's result
CONFIG=my.yaml scripts/run_single.sh my.sql out1/  # single-GPU baseline, no exchange
```

Every rank runs the whole file, so define the tables over **every** file of each table (as
`sql/views.sql` does); each rank scans its own byte range. Only rank 0's result is the answer.
Queries with nested-loop or delim joins, CTEs, TOP_N or COUNT(DISTINCT) are refused in distributed
mode. **Known wrong answers** (see [FINDINGS.md](notes/FINDINGS.md)): an aggregate without GROUP BY
(e.g. `count(*)`) returns one row per partial result, to be summed; S3 and DuckDB-native table
scans are read in full by every rank (results multiplied by the rank count; use local parquet);
`NOT IN` against a subquery whose keys include NULL can be wrong.

## Layout

| Path | What |
|---|---|
| `scripts/common.sh` | paths and defaults, sourced by every script |
| `scripts/build_nixl_ucx.sh`, `build_sirius.sh` | builds |
| `scripts/gen_tpch.sh`, `tools/verify_tpch.py` | TPC-H data with sirius-db/tpchgen-rs, row-count check |
| `scripts/make_config.sh` | Sirius YAML per rank from env vars |
| `scripts/run_ranks.sh`, `run_single.sh` | N-rank and single-process runners |
| `scripts/bench.sh` | benchmark matrix with answer checks |
| `scripts/test_unit.sh`, `test_failstop.sh` | tests |
| `sql/` | views and queries (`@DATA@` is replaced by the dataset path) |
| `expected/` | reference answers |
| `configs/reference-2x-rtxpro4500.yaml` | the config used for `notes/RESULTS.md` |
| `dist-plan-opt/` | the `dist-plan-opt` branch on 4x GB200: its own README, scripts, tools, configs and notes |

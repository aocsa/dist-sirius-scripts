# dist-plan-opt on 4x GB200: TPC-H SF3000 / SF10000

Scripts, configs and notes from benchmarking
[`aocsa/sirius@dist-plan-opt`](https://github.com/aocsa/sirius/tree/dist-plan-opt) (the NIXL exchange
plus distributed CTEs, delim/nested-loop joins, TOP_N, broadcast builds and merged Blooms, so all 22
TPC-H queries run on N ranks; one process per GPU) on a 4x GB200 host (144 cores, 4 RoCE
NICs), with TPC-H read from GPFS (`/scratch`, O_DIRECT). Unlike the scripts at the top of this repo
(`exchange-operator-v2`, `scripts/common.sh`), these drive the Sirius checkout's own launcher,
`scripts/exchange_sql_ranks.sh`, and run all 22 queries from its `test/tpch_performance/queries.py`.

| Read | For |
|---|---|
| [notes/sf10k-improvements.md](notes/sf10k-improvements.md) | SF10000 22-query baseline, the rank-0 straggler fix, scan knobs, Bloom filters, startup overlap, ZSTD data |
| [notes/sf10k-bloom7.md](notes/sf10k-bloom7.md) | the 7 most expensive Bloom-filter queries at SF10000, two builds |
| [notes/q5-sf10000.md](notes/q5-sf10000.md) | Q5 at SF10000: dist-plan-opt vs exchange-operator-v3, page cache vs GPFS |

Headline (SF10000, 4 GPUs, GPFS, `configs/cfg10k.yaml`, `59d40bdc`): all 22 queries in 1,837 s
cold and 850 s warm, no spill. The straggler fix (`432a7c88`, DuckDB `pin_threads` had left ranks 0
and 2 with every I/O thread on one core) took Q5 warm from 43.5-44.8 s to 34.6-36.5 s and Q21 from
101.4 to 68.3 s.

## Setup and build

Paths are the bench box's (defaults in the scripts). `$HOME` there is NFS with a ~200 GB quota:
keep the checkout, build, spill and data on local disk or GPFS.

```bash
export PATH=$HOME/.pixi/bin:$PATH
git clone https://github.com/aocsa/sirius.git -b dist-plan-opt /tmp/aocsa/wt/dist-plan-opt   # or a worktree
cd /tmp/aocsa/wt/dist-plan-opt
# Only what the build needs; skip experimental/* (huge, unused here).
git submodule update --init --recursive duckdb substrait cucascade \
  sirius-duckdb/vcpkg sirius-duckdb/duckdb sirius-duckdb/extension-ci-tools
pixi run cmake --preset release -DSIRIUS_DUCKDB_SOURCE_DIR=$PWD/duckdb \
  -DSIRIUS_WITH_NIXL=ON -DSIRIUS_NIXL_PREFIX=$HOME/tools/nvda_nixl
pixi run make
```

NIXL is prebuilt at `$HOME/tools/nvda_nixl` on the bench box; elsewhere `../scripts/build_nixl_ucx.sh`
builds one into `$WORK/tools/nvda_nixl`. Every
script here finds the checkout through `SIRIUS_REPO` (`--repo` for the Python tools), default
`/tmp/aocsa/wt/dist-plan-opt`.

## Correctness check at SF1

```bash
# DuckDB CPU answers (any python with the duckdb package; this pixi exec one needs nothing installed)
pixi exec --spec python=3.12 --spec python-duckdb python tools/gen_expected.py \
  /scratch/sirius/datasets/tpch_sf1 /tmp/aocsa/tpch22/expected/sf1 1
# 22 queries on 4 ranks, 2 runs each, every answer checked
python3 tools/run22_dpo.py --data /scratch/sirius/datasets/tpch_sf1 --sf 1 \
  --config $PWD/configs/cfg10k.yaml --out /tmp/aocsa/tpch22/runs/sf1-4r --ranks 4 --iters 2 \
  --expected /tmp/aocsa/tpch22/expected/sf1
```

`run22_dpo.py` prints one line per query and appends `query,status,runs,seconds...,note` to
`OUT/results.csv`; `status` is `ok`, `WRONG(...)`, `ERROR` or `MISSING`. Rank logs go to
`OUT/qNN/exchange_rank_<i>.log`, Sirius logs to `OUT/logs`. `tools/recheck.py RUN_DIR EXPECTED_DIR`
re-validates a finished run from its saved rank-0 logs (e.g. after regenerating an answer). The
dist-plan-opt build matched DuckDB on all 22 at SF1. Regenerate Q11's answer if you have an old
`expected/sf1/q11.json` (1048 rows, not 21953): that saved file was wrong. There are no reference
answers at SF10000: compare cold and warm runs, and builds (Q3, Q11 and Q18 differ only in the order
of tied rows there).

## GPU hygiene before every run

- No leftover ranks: `pgrep -ax duckdb` prints nothing (`bench.sh` and `run_q5.sh` refuse to start
  otherwise), and `nvidia-smi` lists no processes.
- Empty spill dir: `rm -rf /tmp/aocsa/sirius-disk/*` (both scripts do it before and after). The
  configs' `downgrade_root_dirs` and `SPILL_DIR` must name the same directory.
- The runners unset `AI_AGENT`/`CLAUDECODE`/`CLAUDE_CODE`: with them set, DuckDB's shell runs in
  agent mode, which changes the output the runners parse.
- One benchmark at a time on the box; `bench.sh` pauses a running `gen_expected.py` meanwhile.

## Benchmarks

```bash
# SF3000, 4 ranks, all 22 queries, answers checked if $EXPECTED holds 22 answers
scripts/bench.sh sf3000-4r 4 configs/cfg3k-4r.yaml 1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22
# SF10000
SF=10000 scripts/bench.sh sf10k-4r 4 configs/cfg10k.yaml 1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22
# One SQL file (Q5 x3) with Quent telemetry, GPFS NIC and per-rank I/O sampling
mkdir -p /tmp/q5
scripts/netsample.sh /tmp/q5/net.txt & ns=$!
scripts/sample.sh /tmp/q5/samples.txt & io=$!
scripts/run_q5.sh sql/q5_scratch_sf10000_x3.sql configs/q5_direct.yaml /tmp/q5 4
kill $ns $io
python3 tools/netrate.py /tmp/q5          # GPFS GB/s per run, per second
python3 tools/iorate.py /tmp/q5/samples.txt    # rchar / read_bytes GB/s and busy cores
python3 tools/pipelines.py /tmp/q5/telemetry_data/<rank-dir> 2   # per-pipeline timeline of the 3rd Q5
python3 tools/taskwait.py /tmp/q5/telemetry_data/<rank-dir> 2    # where GPU_SCAN tasks wait
python3 tools/scanbytes.py /tmp/q5/telemetry_data/<rank-dir> 2 'GPU_SCAN\(0\)'   # bytes out of one scan
```

`bench.sh` env: `SF` (3000), `DATA`, `EXPECTED`, `RUNS`, `SPILL_DIR`, `WORK`, `SIRIUS_REPO`; see its
header. Quent telemetry is on by default (`telemetry.output_directory: telemetry_data`, relative to
where the ranks run: `run_q5.sh` runs them in its OUTDIR), one directory per rank process. The
tools' `query_index` counts the Sirius queries that rank ran, from 0 (2 = the third Q5).

## ZSTD copy of a TPC-H dataset

```bash
# 12 files at a time, resumable; checks every table's row count at the end
scripts/rewrite_zstd.sh /scratch/sirius/datasets/tpch_sf10000 /scratch/nvidia/aocsa/tpch_sf10000_zstd 12
```

Same files, rows and column types as the source; ZSTD (level 3) instead of Snappy, 114688-row row
groups. SF10000: 3.7 TB -> 2.3 TB. Tested end to end at SF1 with a stock DuckDB 1.3.2 CLI
(`DUCKDB=/path/to/duckdb DUCKDB_FLAGS=`). Options in `scripts/rewrite_one.sh`.

## Layout

| Path | What |
|---|---|
| `tools/run22_dpo.py` | TPC-H queries on N ranks via `exchange_sql_ranks.sh`, answers checked |
| `tools/gen_expected.py`, `tools/recheck.py` | DuckDB CPU answers; re-check saved rank-0 logs |
| `scripts/bench.sh` | one `run22_dpo.py` sweep with GPU hygiene |
| `scripts/run_q5.sh`, `sql/q5_scratch_sf10000_x3.sql` | one SQL file on N ranks (Q5 subquery form, x3) |
| `scripts/netsample.sh`, `tools/netrate.py` | GPFS ingress from the RoCE NIC counters (`NICS` env) per run |
| `scripts/sample.sh`, `tools/iorate.py` | per-rank `/proc` read bytes and CPU over time |
| `tools/pipelines.py`, `taskwait.py`, `scanbytes.py` | Quent telemetry: per-pipeline timeline, task waits, scan bytes |
| `scripts/rewrite_zstd.sh`, `rewrite_one.sh` | ZSTD copy of a Parquet dataset |
| `configs/cfg10k.yaml` | SF10000, 4 ranks: host 250 GiB, 30 GB spill, 32 scan threads, 4 uring reactors, no cache, 4 GiB Bloom cap (`cfg3k-4r.yaml` and `knobs/base.yaml` are the same) |
| `configs/cfg3k-2r.yaml` | SF3000, 2 ranks: 64 scan threads, 60 GB spill |
| `configs/cfgC.yaml` | host 120 GiB, 100 GB spill, 32 scan threads, no cache, default Bloom cap |
| `configs/q5.yaml`, `q5_direct.yaml` | the Q5 reference configs: page cache vs O_DIRECT reads |
| `configs/knobs/*.yaml` | base + one change each (readahead, 64 threads, 256 MiB scan batches, no multi-partition Blooms, no dynamic filters) |

Every config writes spill to `/tmp/aocsa/sirius-disk`; change `downgrade_root_dirs` on another box.

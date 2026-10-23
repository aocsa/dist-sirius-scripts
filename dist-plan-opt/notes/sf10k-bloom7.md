# dist-plan-opt, TPC-H SF10000, 4x GB200: the 7 most expensive Bloom-filter queries

Build: `dist-plan-opt` @ `59d40bdc` (merge Blooms accumulated from multi-partition builds), on top
of `caa5e97b` (empty-batch fix) and `5127c81a` (query memory outlives sent batches).
Data: `/scratch/sirius/datasets/tpch_sf10000` (GPFS, O_DIRECT). One process per GPU, NIXL/UCX over
NVLink, CPU fallback off. Queries: `test/tpch_performance/queries.py` for SF10000. Each query runs in
its own 4 processes, twice: cold (first run) and warm (second run in the same processes).

Config `configs/cfg10k.yaml`: host 250 GiB and disk spill 30 GB per rank, scan_manager 32 threads,
4 uring reactors, `cache: none`, `max_dynamic_filter_bloom_bytes_per_gpu: 4Gi`.

Queries chosen: the most expensive in the reference sweep that wire dynamic filters. Q1, Q6, Q13,
Q14 and Q15 wire none in this branch (from its SF1 logs), so Q13 and Q14 are left out.

| Query | Cold @59d40bdc | Warm @59d40bdc | Cold @caa5e97b | Warm @caa5e97b | Answer |
|---|---|---|---|---|---|
| Q18 | 109.0 | 46.5 | (stopped) | (stopped) | consistent (ties only) |
| Q9  | 94.7  | 51.1 | 104.7 | 49.6 | same as previous build |
| Q21 | 179.0 | 101.4 | 183.5 | 102.4 | same as previous build |
| Q10 | 120.4 | 45.0 | 125.6 | 48.4 | same as previous build |
| Q8  | 61.5  | 40.3 | 81.0 | 38.4 | same as previous build |
| Q17 | 92.0  | 52.7 | 94.3 | 53.7 | same as previous build |
| Q7  | 129.8 | 51.8 | 117.0 | 53.3 | same as previous build |
| Total | 666.4 | 388.8 | | | |
| Total w/o Q18 | 557.4 | 342.3 | 706.1 | 345.8 | |

- Correctness: no DuckDB/oracle answers exist at SF10000. Each query returned the same answer cold
  and warm, and Q7-Q21 match the previous build exactly. Q18's cold and warm answers are identical
  except which order keys fill the last tied places: SF10000 repeats orders with the same customer,
  date, price and quantity, and Q18 orders by price and date only before `LIMIT 100`.
  At SF1 the build matches DuckDB on all 22 queries (Q11 checked against a fresh DuckDB answer; the
  stored `expected/sf1/q11.json` is wrong).
- Nothing spilled to disk (68 KB), and no Bloom was refused by the 4 GiB cap in either build.
- `59d40bdc` vs `caa5e97b`: warm times are within run-to-run noise (+-5%) on these queries; cold
  Q8 improved 81.0 -> 61.5 s. The GPFS read path dominates (see [q5-sf10000.md](q5-sf10000.md): Q5
  at SF10000 reads 1.2 TB at ~30 GB/s), so filters that cut join work show up mostly when reads are
  cached.

Runs: `tools/run22_dpo.py --data /scratch/sirius/datasets/tpch_sf10000 --sf 10000 --config
configs/cfg10k.yaml --ranks 4 --iters 2 --queries 7,8,10,17,9,21,18`. Results and rank logs stayed on
the bench box (not in this repo).

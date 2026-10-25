# Distributed TPC-H on 4× GB200: builds and configs compared

Measured 2026-10-24 on one host with 4× GB200 and 956 GiB of CPU memory in 2 NUMA nodes. One Sirius rank per GPU, exchanges over NIXL/UCX, Parquet on GPFS. Times are in seconds, shown as cold / warm.

## Verdict

- **New best (2026-10-25): 4 MiB host-pool blocks.** Adding `block_size: 4194304` to the host pool makes all 22 SF10000 queries run **2.67× faster cold and 1.34× faster warm** on `dist-plan-opt-rank-pin`: 797.1 / 510.4 s against 2,128.3 / 684.5, with correct answers. It is a config change only. See "Cold-read fix" below.
- **Best build: `dist-rank-pin-sf3k-integ`** (3f9c19f7). It is as fast as `dist-plan-opt-rank-pin` or slightly faster, with no regressions and correct answers.
- **Best config: the dpo kit configs** in `bench/dist-tpch`:
  - `direct-io.yaml` for unpinned runs;
  - `host-pin.yaml` with every table pinned, where the data fits;
  - host pools of 210Gi or less per rank.
- **Pinning is the biggest lever.** SF3000 with every table pinned takes 22.9 s warm, against 212 s unpinned: 9× faster.
- **The 1-GPU reference config** (`bench/sf3k-sf10k-1gpu-repro`) is 14–28% slower cold and 59–62% slower warm at 4 ranks. Its prefetching cache is the main cost.

## Builds

| Build | Commit | Notes |
|---|---|---|
| dpo | `dist-plan-opt-rank-pin` 626fe147 (code at f0e88990) | Earlier baseline. |
| integ | `dist-rank-pin-sf3k-integ` 3f9c19f7 | f0e88990 plus 10 commits, all off by default. |
| land | local `land/integ-spill-readahead` | integ plus spill compression (PR #2204) and a port of PR #2155 (readahead and cache). Not pushed. |

## Results

| Test | dpo build | integ build |
|---|---|---|
| SF3000, every table pinned, dpo config | 24.0 / 23.2 | **23.5 / 22.9** |
| SF3000 unpinned, 22 queries, dpo config | 890.8 / **207.6** | **815.3** / 212.3 |
| SF10000 unpinned, 22 queries, dpo config | 1,990 / 679.9 (older reference sweep, 432a7c88) | 2,128 / 684.5 |

Where integ looked slower, an A/B alternating the two builds shows noise, not a regression:

- SF3000 Q1, Q14 and Q15, warm;
- SF10000 Q8, Q16, Q17, Q19 and Q22, cold.

**land against integ, dpo config:**

- Same speed: in the same alternating A/B, SF3000 Q5 and Q9 match cold and warm.
- Unit tests: no new failures. Four of main's tests were adapted to PR #2155's behaviour, and need review.

### Reference config against dpo config, land build, compression on

SF10000, the 5 queries that spill most (Q9, Q21, Q18, Q7, Q14):

| Config | Total (mean of 2 runs) | Spill per run |
|---|---|---|
| dpo | **702 / 249** | about 1.17 TB device to host, 0 to disk |
| reference | 802 / 403 | about 0 |

The reference config with one setting changed (cold / warm):

| Change | Q9 | Q21 |
|---|---|---|
| none | 168–195 / 69–72 | 197–202 / 139–140 |
| `cache: none` | **106 / 45** | **145 / 109** |
| 32 scan threads | 201 / 70 | 199 / 142 |
| default batch and partition sizes | 208 / 68 | 205 / 138 |
| `max_readahead_bytes` unset | 192 / 68 | 222 / 145 |
| *dpo config* | *127–139 / 43* | *167–203 / 81* |

- **The cache is the main cost.** It is capped at about 36 GiB per rank and gets about 1% hits, with over 100,000 evictions per rank per iteration. Without it, Q9 matches the dpo config.
- **Q21 is still 33% slower warm** than the dpo config with the cache off. Untested candidates for the rest: the GPU memory fractions, the host pool pinned at startup, and the memory prefetcher.
- **`max_readahead_bytes: 4GB`** slightly beats unset over the 5 queries (802 / 403 against 862 / 412), within noise.
- **The reference config needs two fixes to run at 4 ranks:**
  - `enable_probe_accumulate: false`, or Q21 fails, because distributed plans refuse the operator;
  - host pools of 180Gi or less, because at 210Gi a rank ran out of memory.

### Spill compression (PR #2204)

- **It compresses:** Q9's spilled batches go from 170.3 GB to 80.6 GB stored (2.1×).
- **Some batches fall back to uncompressed:** 4 encodes ran out of GPU memory, and 3 batches didn't compress enough.
- **No speedup here:** within about 2% of uncompressed, because every spill fits in host memory and nothing reaches disk. It should matter only where spills reach disk.

### Other checks

- **NIXL hop is a DMA:** `cuda_ipc` zero-copy over NVLink, with no TCP traffic. Forcing the data through TCP makes SF100 Q9 42× slower warm (22.5 s against 0.53 s).
- **Answers:** all correct. Mismatches are only the known ties (Q3, and Q18 at SF10000) and log noise: NIXL glog lines and the compressor's `simpatico::` messages.

## `dist-plan-opt-rank-pin` against `dist-rank-pin-sf3k-integ`

### What differs

integ is dpo's code (f0e88990) plus 10 commits. Every one of them is off unless switched on:

| Change | How to turn it on |
|---|---|
| Natural-order pin files; sorted-keys group-by; sorted merge; low-cardinality group-by | `SIRIUS_EXP_SORTED_PIN_FILES`, `SIRIUS_EXP_SORTED_GROUPBY`, `SIRIUS_EXP_SORTED_MERGE`, `SIRIUS_EXP_LOWCARD_GROUPBY` = 1 |
| Probe-accumulate join; correlated EXISTS rewrite; its MARK-join fix | `enable_probe_accumulate: true`. Single GPU only: distributed plans refuse it. |
| Docs, the 1-GPU repro kit, a build fix | — |

With the dpo configs both builds run the same default code path, so the same speed is expected. All the measurements below use the dpo configs and no flags.

### Same config, both builds

Times are cold / warm, in seconds:

- **Pinned:** SF3000, every table pinned once on the host (`host-pin.yaml`, 210Gi), 3 iterations, best warm.
- **Unpinned:** SF3000, `direct-io.yaml`, one process per query, 3 iterations. dpo ran with 250Gi pools, integ with 210Gi; pools grow on demand, so the cap matters only under memory pressure.

| Q | pinned, dpo | pinned, integ | unpinned, dpo | unpinned, integ |
|---|---|---|---|---|
| Q1 | 1.27 / 1.17 | 1.33 / 1.20 | 18.2 / 5.06 | 15.0 / 5.83 |
| Q2 | 0.43 / 0.39 | 0.43 / 0.39 | 22.7 / 4.46 | 22.0 / 4.12 |
| Q3 | 0.79 / 0.72 | 0.74 / 0.72 | 52.8 / 10.8 | 53.9 / 11.7 |
| Q4 | 0.55 / 0.54 | 0.55 / 0.53 | 30.3 / 8.54 | 28.6 / 8.66 |
| Q5 | 0.77 / 0.76 | 0.76 / 0.74 | 38.4 / 11.4 | 37.3 / 10.5 |
| Q6 | 0.44 / 0.43 | 0.42 / 0.42 | 28.8 / 7.75 | 27.9 / 8.08 |
| Q7 | 0.86 / 0.83 | 0.78 / 0.79 | 65.0 / 14.5 | 63.4 / 14.3 |
| Q8 | 0.93 / 0.94 | 0.92 / 0.92 | 39.4 / 9.99 | 41.0 / 10.0 |
| Q9 | 1.99 / 1.86 | 1.92 / 1.84 | 56.4 / 13.3 | 58.2 / 13.1 |
| Q10 | 1.33 / 1.32 | 1.27 / 1.26 | 39.9 / 11.6 | 38.2 / 11.6 |
| Q11 | 1.36 / 1.36 | 1.37 / 1.37 | 27.9 / 3.54 | 8.60 / 3.57 |
| Q12 | 0.95 / 0.96 | 0.92 / 0.92 | 35.9 / 9.77 | 44.4 / 10.3 |
| Q13 | 3.63 / 3.33 | 3.67 / 3.51 | 10.1 / 3.99 | 7.21 / 3.84 |
| Q14 | 0.70 / 0.65 | 0.68 / 0.65 | 57.5 / 8.77 | 43.6 / 9.72 |
| Q15 | 0.64 / 0.63 | 0.63 / 0.62 | 42.5 / 8.24 | 35.5 / 9.19 |
| Q16 | 0.48 / 0.46 | 0.51 / 0.45 | 25.5 / 1.64 | 25.9 / 1.54 |
| Q17 | 0.75 / 0.76 | 0.76 / 0.76 | 49.2 / 12.2 | 30.6 / 12.8 |
| Q18 | 2.15 / 2.16 | 1.97 / 1.96 | 47.2 / 13.9 | 46.6 / 14.2 |
| Q19 | 0.99 / 0.95 | 1.01 / 0.98 | 71.9 / 11.7 | 72.3 / 12.0 |
| Q20 | 0.93 / 0.93 | 0.94 / 0.93 | 34.4 / 6.84 | 40.9 / 6.97 |
| Q21 | 1.82 / 1.78 | 1.70 / 1.69 | 87.4 / 27.1 | 64.6 / 27.7 |
| Q22 | 0.28 / 0.27 | 0.28 / 0.27 | 9.52 / 2.46 | 9.43 / 2.43 |
| **Total** | **24.0 / 23.2** | **23.5 / 22.9** | **890.8 / 207.6** | **815.3 / 212.3** |

- **Pinned:** integ is 1–2% faster in total. Q18 is 9% faster and Q21 5% faster; the most any query slows is about 6% (Q13 warm, Q16 cold), on sub-4-second times.
- **Unpinned:** warm is within 2.3% in total.
  - Single queries differ by up to 12% (Q14 and Q15 warm), in both directions.
  - Cold differs by up to 3× (Q11: 27.9 against 8.6). That is GPFS read variance, not the build.
- **Answers:** all match on both builds, except Q3's tie.

### Alternating A/B of the queries that looked slower

Same config (`direct-io.yaml`, 210Gi), runs in the order dpo, integ, integ, dpo:

| Query | dpo | integ | Verdict |
|---|---|---|---|
| SF3000 Q1, best warm | 5.38, 4.79 | 5.53, 4.91 | same |
| SF3000 Q14, best warm | 9.42, 9.21 | 8.94, 8.96 | same |
| SF3000 Q15, best warm | 7.88, 9.11 | 8.77, 7.77 | same |
| SF10000 Q8, cold / warm | 83.2, 95.8 / 32.6, 32.1 | 72.9, 77.8 / 32.9, 33.0 | integ faster cold |
| SF10000 Q16, cold / warm | 26.3, 20.0 / 6.1, 5.8 | 23.0, 19.5 / 6.0, 6.0 | same |
| SF10000 Q17, cold / warm | 89.6, 107.2 / 39.7, 40.8 | 113.2, 111.2 / 41.5, 38.6 | cold within dpo's own 18 s spread; warm same |
| SF10000 Q19, cold / warm | 136.0, 154.3 / 39.9, 36.8 | 123.5, 126.9 / 38.2, 38.5 | integ faster cold |
| SF10000 Q22, cold / warm | 13.8, 16.6 / 7.1, 6.9 | 12.7, 13.7 / 7.3, 7.2 | same |

**Conclusion:** no regression. integ matches dpo, and is slightly ahead on pinned SF3000. Pick integ for its opt-in features. Turning any of them on, at 4 ranks, is not measured here.

The full SF10000 sweep for integ (2,128 / 684.5) compares against an older build (432a7c88), not dpo; dpo has no full SF10000 sweep in these results.

## Reference: `dist-plan-opt-rank-pin`, best config, SF10000 top 5

Measured 2026-10-25:

- **Build:** `dist-plan-opt-rank-pin` 626fe147. An incremental make recompiled only the extension wrapper, which rebuilds every time.
- **Config:** `direct-io.yaml` with `--host-pool 210Gi`, 4 ranks, unpinned.
- **Iterations:** 3. Cold is the first; warm is the best of the other two.

Times are cold / warm, in seconds:

| Q | dpo build | integ build, same code path (2026-10-24) | 432a7c88 sweep (2026-10-23) |
|---|---|---|---|
| Q9 | **120.7 / 43.6** | 120.9 / 44.9 | 121.0 / 45.6 |
| Q21 | 190.6 / 86.5 | 180.7 / 88.1 | 180.3 / 81.5 |
| Q18 | **99.3 / 47.8** | 118.4 / 46.3 | 117.4 / 48.1 |
| Q7 | 148.7 / 44.4 | 154.8 / 42.6 | 147.1 / 44.9 |
| Q14 | 108.3 / 30.1 | 111.0 / 32.3 | 102.3 / 29.8 |
| **Total** | **667.6 / 252.4** | 685.8 / 254.2 | 668.1 / 249.9 |

- **Answers:** all correct. Q18's 2nd and 3rd iterations differ only in the known tie: customer 1179176872's orders on 1996-06-20, at 586579.84, quantity 317.
- **Spill:** 1,412 GB from device to host over the 3 iterations, 0 to disk. Per query: Q9 520, Q21 426, Q18 355, Q7 79, Q14 33.
- **Use these totals as the baseline: 667.6 cold, 252.4 warm.** The three runs agree within 3% on both totals.
- Output: `/tmp/aocsa/tpch22/dpo-best/sf10k-top5/`.

## SF10000 profile: where the time goes and what would speed up every query

Profiled 2026-10-25 on the dpo build (626fe147) with the recommended config, 4 ranks, unpinned. Each query ran in a fresh process with trace logs (`SIRIUS_LOG_LEVEL=trace`), one log per rank, parsed with `tools/log_analyzer/parse_logs.py`.

### The most expensive queries (integ sweep, 2026-10-24, same code path)

| Rank | By cold | By warm | GB read (est.) | Cold GB/s | Warm GB/s |
|---|---|---|---|---|---|
| 1 | **Q21** 180.7 | **Q21** 88.1 | 809 | 4.5 | 9.2 |
| 2 | Q7 154.8 | Q18 46.3 | 1,266 | 8.2 | 29.7 |
| 3 | **Q19** 151.8 | Q9 44.9 | 918 | 6.1 | 23.7 |
| 4 | Q5 146.9 | Q7 42.6 | 1,194 | 8.1 | 34.7 |
| 5 | Q3 134.5 | Q17 39.2 | 926 | 6.9 | 26.2 |
| 6 | **Q12** 123.9 | Q19 38.8 | 565 | 4.6 | 16.5 |
| 7 | Q15 123.0 | Q10 37.6 | 920 | 7.5 | 30.8 |

- The GB and GB/s columns belong to the query in the "By cold" column. GB read is the compressed size of the columns the query reads, from the parquet footers.
- The top 7 by cold cost 1,015.6 s, 48% of the 2,128.3 s cold total.
- The 3 picked for profiling are the top-7 queries that read the fewest bytes per second: Q21, Q12 and Q19. For comparison, Q8 reads 15.4 GB/s cold and 46.6 GB/s warm.

### What the profiles show

| | Q21 | Q12 | Q19 |
|---|---|---|---|
| Cold / warm (s) | 210.0 / 82.4 | 118.6 / 32.4 | 131.3 / 38.2 |
| Share of wall time in scan pipelines, cold / warm | 93% / 88% | 98% / 93% | 98% / 96% |
| Rows decoded from lineitem | 3 × 60.0 B | 60.0 B | 60.0 B |
| Rows kept by the filters after the scan | 4.1%, 5.8%, 5.8% | 0.5% | 0.04% |
| Spill | none | none | none |

1. **All three queries are scans.** Joins, aggregations and exchanges together take 2–12% of the time. Nothing spilled, and no rank lagged in the warm run.
2. **Cold runs pay a penalty on first reads of each file.** The cause, found later, is too many concurrent small O_DIRECT reads; see "Cold-read fix".
   - A fresh process reads lineitem at 5–7 GB/s cold and 18–28 GB/s warm.
   - **Correction:** an earlier O_DIRECT read of `l_comment` at 62.5 GB/s was served from cached blocks. GPFS here uses 16 MiB blocks, so reading any column warms all columns of the row groups in a block. It doesn't show what storage delivers cold.
   - The cold run costs 10–15× more kernel CPU: Q12 used 774–1,205 s per rank cold against 75 s warm. Most of it is in the io_uring worker threads.

   Q12 experiments, each in a fresh process:

   | Before Q12, in the same process | Q12 first run | Q12 second run |
   |---|---|---|
   | nothing (4 runs) | 118.6, 123.4, 137.2, 136.0 | 32.4 – 35.7 |
   | a scan of lineitem and orders on columns Q12 doesn't read | **34.2** | 33.7 |
   | a scan of partsupp and customer | 136.0 | 33.9 |
   | nothing, 180 GiB host pool pinned at startup | 137.2 | 35.7 |
   | nothing, `uring_n_reactors: 16` | 143.2 | 96.7 |
   | nothing, `executor.pipeline.num_threads: 8` | 111.9 | 39.0 |

   - Q21 shows the same thing within one run. Its third lineitem scan reads the same 4 columns as the first, and took 25.4 s in the cold run against 124.1 s for the first.
   - So the penalty is tied to the files and the process, not to the data or the storage.
   - It's not the host pool: scan reads land in 64 MiB of bounce buffers per reactor, pinned once at startup.
   - It's not per-file preparation: footers and row-group plans finish within 4–9 s of query start.
   - Finding the exact kernel path needs root (`perf` is blocked by `perf_event_paranoid=4`).
3. **Every scan decodes all rows, and filters run afterwards.**
   - Bloom dynamic filters run on the decoded batch (`src/op/scan/sirius_physical_dynamic_filter.cpp:47-105`).
   - Only zone-map dynamic filters can be pushed into the reader. They are off by default (`enable_dynamic_zone_map_filter`, `src/sirius_config.hpp:215`), and distributed plans never build them (`src/op/dynamic_filter/dynamic_filter_publisher.cpp:1279`).
   - No reader decodes the filter columns first and the rest only for surviving rows (`src/op/scan/parquet_materialize.cpp:190-234`).
   - Static constant predicates reach cuDF's reader filter, but that filter runs after the decode too. Column-to-column predicates such as `l_receiptdate > l_commitdate` become a separate FILTER.
4. **Q21 scans lineitem three times, one after another:** 24.9 + 17.2 + 26.1 s of its 82 s warm run. Sirius has no shared scan and doesn't rewrite EXISTS / NOT EXISTS.

### Improvement options, ranked by how many queries they reach

| # | Change | Queries it reaches | Expected gain | Evidence |
|---|---|---|---|---|
| 1 | **Done (config):** cap the reads in flight with 4 MiB host blocks. A code follow-up makes the cap its own setting; see `plans/cold-read-plan.md`. | all 22 | top 5: −412 s cold, −76 s warm | "Cold-read fix" below |
| 2 | Two-phase scan: decode the filter and join-key columns, apply static and Bloom filters, then decode the other columns only for surviving rows (for example with cuDF's hybrid scan reader) | every query with a selective scan; Bloom-filtered scans appear in at least 14 of the 22 plans | warm scan time on queries that keep ≤6% of rows: measured on Q12, Q19 and Q21; likely also the Bloom-filtered lineitem scans of Q3, Q5, Q7–Q10, Q17 and Q20 (not profiled) | decoded vs kept rows in the table above |
| 3 | Push Bloom filters into the reader, and enable zone-map dynamic filters for distributed plans so row groups can be skipped on clustered keys | joins on `l_orderkey` / `o_orderkey` (lineitem and orders are sorted by order key): Q3, Q4, Q5, Q7–Q10, Q12, Q18, Q21 | fewer bytes read and decoded, cold and warm | zone maps are off and never built for distributed plans |
| 4 | Shared scans, or a semi/anti-join rewrite, when a table is scanned more than once | Q21, and subqueries in Q2, Q11, Q15, Q17, Q20, Q22 | Q21 warm: up to about 40 s of 82 | three serial lineitem scans |

- **Settings that didn't help:** `uring_n_reactors: 16`, `executor.pipeline.num_threads: 8`, and pinning the host pool at startup.
- Output: `/tmp/aocsa/tpch22/dpo-best/profile/` (per-rank trace logs, `parsed/`, `gpfs_cold.txt`, and the warm-up experiments) and `/tmp/aocsa/tpch22/dpo-best/knobs/`.

## Cold-read fix: 4 MiB host blocks (2026-10-25)

**The cause.** Each io_uring reactor stages reads through a fixed 64 MiB of host-pool blocks (`src/io/uring/uring_reactor.cpp:63-64`). With the default 1 MiB blocks:
- that's 64 slots per reactor, so 256 concurrent O_DIRECT reads per rank and about 1,024 on the node;
- the reads are small: about 300 KB of column chunk each at SF10000, landing in neighbouring 16 MiB GPFS blocks;
- on uncached data they contend, and the io-wq workers burn kernel CPU.

A standalone replay of Sirius's read path (`/tmp/aocsa/uring-probe/uring_probe.c`) reproduces it:
- Sirius's pattern on uncached files: 4.5–7.1 GB/s.
- 256 slots per reactor: 1.2 GB/s, with 731 s of kernel CPU in 26 s.
- Fewer reads in flight, or reads spread across files: 9–34 GB/s.

**The fix.** Set `memory.host.block_size: 4194304`. Each reactor then has 16 slots of 4 MiB, so 64 reads are in flight per rank.

**Q12** (each run a fresh process; the 4 reactors / 1 MiB row is today's baseline):

| Setting | Reads in flight per rank | Cold (s) | Warm (s) |
|---|---|---|---|
| 4 reactors, 1 MiB blocks (baseline) | 256 | 122.4–126.4 | 33.9–34.0 |
| 2 reactors, 1 MiB blocks | 128 | 94.1–106.6 | 21.6–31.1 |
| **4 reactors, 4 MiB blocks** | **64** | **36.9–41.6** | **20.0–22.3** |

**SF10000 top 5.** `dist-plan-opt-rank-pin` 626fe147, `direct-io.yaml` plus `block_size: 4194304`, 210Gi, 4 ranks, unpinned, 3 iterations. Cold is the first iteration; warm is the best of the other two. The two arms differ only in `uring.spread_reads`, an experimental read shuffle that makes no difference here. It has since been reverted, and the dpo build rebuilt clean. The patch is saved as `patches/spread-reads-and-uring-stats.patch`.

| Q | Baseline (1 MiB) | 4 MiB, shuffle off | 4 MiB, shuffle on |
|---|---|---|---|
| Q9 | 120.7 / 43.6 | 54.9 / 35.3 | 55.7 / 35.8 |
| Q21 | 190.6 / 86.5 | 71.1 / 53.7 | 69.0 / 53.9 |
| Q18 | 99.3 / 47.8 | 49.6 / 38.6 | 57.5 / 35.0 |
| Q7 | 148.7 / 44.4 | 46.1 / 28.3 | 43.6 / 28.4 |
| Q14 | 108.3 / 30.1 | 33.8 / 20.7 | 33.7 / 20.3 |
| **Total** | **667.6 / 252.4** | **255.5 / 176.5** | **259.4 / 173.4** |

- **Answers:** all correct. Q18 differs only in the known tie (customer 1179176872, 1996-06-20, 586579.84, quantity 317), where each run picks a different order key among equal rows.
- **Spill:** about the same as baseline: 49–592 GB GPU→host per query, 0 to disk. The larger blocks don't slow the spill-heavy queries.
- **Output:** `/tmp/aocsa/tpch22/dpo-best/top5-b4/`, plus the Q12 series in `spread-q12`, `spread-q12v` and `spread-q12b`.
**All 22 queries at SF10000.** Measured 2026-10-25 on the clean `dist-plan-opt-rank-pin` 626fe147 build, using `direct-io.yaml` plus `block_size: 4194304`, 210Gi, 4 ranks, unpinned. Each query ran once, in a fresh process, with 2 iterations: cold, then 1 warm. The baseline is the 2026-10-24 sweep with the same config apart from the block size, also 2 iterations.

| Q | Baseline | 4 MiB blocks | Cold speedup |
|---|---|---|---|
| Q1 | 28.8 / 19.3 | 25.2 / 24.7 | 1.1× |
| Q2 | 36.8 / 13.6 | 11.8 / 7.7 | 3.1× |
| Q3 | 134.5 / 35.3 | 45.1 / 26.6 | 3.0× |
| Q4 | 96.3 / 26.9 | 34.0 / 18.4 | 2.8× |
| Q5 | 146.9 / 34.4 | 48.6 / 28.7 | 3.0× |
| Q6 | 89.4 / 25.8 | 30.1 / 21.7 | 3.0× |
| Q7 | 154.8 / 42.6 | 45.0 / 29.1 | 3.4× |
| Q8 | 102.3 / 33.7 | 49.3 / 30.3 | 2.1× |
| Q9 | 120.9 / 44.9 | 52.2 / 36.6 | 2.3× |
| Q10 | 110.8 / 37.6 | 46.9 / 28.1 | 2.4× |
| Q11 | 33.9 / 11.5 | 13.0 / 9.3 | 2.6× |
| Q12 | 123.9 / 34.3 | 42.4 / 20.6 | 2.9× |
| Q13 | 24.0 / 14.4 | 18.4 / 14.4 | 1.3× |
| Q14 | 111.0 / 32.3 | 35.2 / 19.8 | 3.2× |
| Q15 | 123.0 / 29.9 | 36.3 / 19.1 | 3.4× |
| Q16 | 28.4 / 6.3 | 7.7 / 3.6 | 3.7× |
| Q17 | 116.3 / 39.2 | 39.4 / 29.9 | 3.0× |
| Q18 | 118.4 / 46.3 | 56.3 / 36.6 | 2.1× |
| Q19 | 151.8 / 38.8 | 46.4 / 26.1 | 3.3× |
| Q20 | 79.2 / 22.2 | 38.0 / 21.3 | 2.1× |
| Q21 | 180.7 / 88.1 | 69.9 / 53.2 | 2.6× |
| Q22 | 16.4 / 7.1 | 6.0 / 4.6 | 2.7× |
| **Total** | **2,128.3 / 684.5** | **797.1 / 510.4** | **2.67× cold, 1.34× warm** |

- **Totals:** cold drops by 1,331 s and warm by 174 s. Cold plus warm goes from 2,812.8 s to 1,307.5 s, 2.15× faster.
- **Cold:** every query is faster. Q1 (1.1×) and Q13 (1.3×) gain least, because their cold penalty was small to begin with.
- **Warm:** faster or even on 21 of 22 queries. **Q1 is the exception, and the loss is real.**

  | Block size | Q1 cold / warm (s) | Q12 cold / warm (s) |
  |---|---|---|
  | 1 MiB | 28.8 / 19.3 | 122–126 / 34 |
  | 2 MiB | 27.4 / 16.1 | 61.9 / 23.0 |
  | 4 MiB | 25.2–29.3 / 24.7–28.4 (3 runs) | 36.9–42.4 / 20.0–22.3 |

  - Q1 reads 7 adjacent lineitem columns as large contiguous reads, which benefit from more reads in flight.
  - Q12 makes many small reads, which suffer from them.
  - 4 MiB is still the best single setting: it trades about 7–9 s of Q1 warm time for about 1,331 s of cold time across the 22 queries.
  - The code fix (`cold-read-plan.md`, step 4) should cap concurrent *small* reads without limiting large ones.

  **Code fix measured (uncommitted).** A small-read cap of 16 per reactor with 1 MiB blocks gave:
  - Q1: 27.6–28.8 / 19.5–20.8 s, so its warm loss is gone.
  - Q12: 41.4–42.3 / 21.7–22.0 s, keeping the cold gain.
  - Top 5: 278.4 / 181.0 s, against 255.5 / 176.5 with 4 MiB blocks.

  Details and next tuning steps are in `cold-read-plan.md`, step 4.

  **All 22 queries with the cap** (16 reads, 4 MiB threshold, 1 MiB host blocks): **806.7 s cold / 507.3 s warm.** That matches 4 MiB blocks (797.1 / 510.4) within noise, without their Q1 and Q6 warm slowdowns. Answers are correct; Q3 and Q18 differ only in tied rows.
- **Answers:** all correct. Q18's cold iteration differs only in the known tie.
- **Spill:** 1,412 GB GPU→host in total, none to disk.
- **Output:** `/tmp/aocsa/tpch22/dpo-best/sf10k-22-b4/`.
- **Not yet run:** SF3000, and pinned runs with this setting. Pinned tables live in the host pool, so check their memory use first.

## Recommended config

This is `bench/dist-tpch/direct-io.yaml` at 210Gi per rank, plus 4 MiB host blocks (2026-10-25). It is for `dist-plan-opt-rank-pin` or the integ build, 4 ranks, one per GPU, and sets only what differs from Sirius's defaults. Keep the space before the comma in the `host:` line: `run_query.py --host-pool` rewrites the capacity with a regex that otherwise swallows the comma.

```yaml
# sirius.yaml: 4 ranks on 4x GB200 (one per GPU), Parquet on GPFS read with O_DIRECT
sirius:
  memory:
    # Grows on demand, not pinned at startup. Two ranks share each 478 GiB CPU node: keep <= 210Gi.
    # 4 MiB blocks cap the io_uring staging at 16 reads in flight per reactor: 2.6x faster cold on SF10000.
    host: { capacity_bytes: 210Gi , block_size: 4194304 }
    # Spill space per rank. Nothing spilled to disk in these runs. Size it to the volume it is on.
    disk: { disk_id: 0, capacity_bytes: 30000000000, downgrade_root_dirs: "/tmp/aocsa/sirius-disk" }
  executor:
    scan_manager:
      num_threads: 32
      uring_n_reactors: 4
      # No prefetching cache: on GPFS at SF3000+ it got about 1% hits and was the reference config's main cost.
      cache: { mode: none }
  operator_params:
    max_dynamic_filter_bloom_bytes_per_gpu: 4Gi
```

- **Pinned runs:** use `bench/dist-tpch/host-pin.yaml`. It is the same config with 60 GB of disk spill. Pin with `run_suite.py`, which gave 22.9 s warm for all 22 SF3000 queries:

  ```bash
  pixi run python bench/dist-tpch/run_suite.py --out OUT --tag _pinall \
    --config bench/dist-tpch/host-pin.yaml --host-pool 210Gi --data /scratch/sirius/datasets/tpch_sf3000 \
    --sf 3000 --pin-tier host --pin-plans src/compression/simpatico_codegen/plans/tpch_sf1000 \
    --pin-exclude o_comment --pin-replicate nation,region,supplier
  ```

  At SF10000 not everything fits: pin the subset in `pinned-results.md` (set D).
- **Unpinned runs:** `run_query.py Q --config bench/dist-tpch/direct-io.yaml --host-pool 210Gi`, or `run_tpch.sh` with `CASES=direct HOST_POOL_DIRECT=210Gi`.
- **Keep out of this config:**
  - `enable_probe_accumulate: true`: distributed plans refuse it, and Q21 fails.
  - Pinning the whole host pool at startup (`initial_number_pools`) above 180Gi per rank: a rank was killed for lack of memory.
  - `cache: { mode: sirius }` with a small `eviction_threshold_fraction`.
- **Land build only:** adding `compression: { enable_spill_compression: true }` made spills 2.1× smaller at the same speed. Worth it only where spills reach disk. The integ build rejects the key and runs the query on the CPU without Sirius, as it does `scan_manager.max_readahead_bytes`.

## Caveats

- Cold times on GPFS vary by up to 20% between runs. Most comparisons have 1–2 runs per setting, so differences under about 10% are noise.
- The land build is local and unreviewed. Its PR #2155 port resolved real conflicts with main's readahead rework (#2006).

## Where things are

- Results: `/tmp/aocsa/tpch22/integ/` and `/tmp/aocsa/tpch22/integ-land/`. Each has a `report.md`.
- Worktrees:
  - integ: `/tmp/aocsa/wt/rank-pin-sf3k-integ`, with the uncommitted `bench/dist-tpch/ref-config/` kit;
  - land: `/tmp/aocsa/wt/integ-land`.

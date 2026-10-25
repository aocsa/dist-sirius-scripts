# Plan: remove the cold first-read penalty in the local io_uring read path

Status as of 2026-10-25: **a config-only fix cuts Q12 at SF10000 from 122–126 s to 37–42 s cold and from 34 s to 20–21 s warm.** The fix is `memory.host.block_size: 4194304` (4 MiB instead of 1 MiB), with correct answers in every run.
- **Why it works (likely):** each io_uring reactor stages reads through a fixed 64 MiB of host blocks (`uring_reactor.cpp:63-64`). With 1 MiB blocks that's 64 slots per reactor, or 256 small (~300 KB) O_DIRECT reads in flight per rank and about 1,024 across the node. With 4 MiB blocks it's 16 slots per reactor, or 64 per rank. The standalone probe shows that too many concurrent small reads collapse GPFS throughput and burn kernel CPU.
- **Also measured:** 2 reactors per rank (128 in flight) lands in between, at 94–107 s cold and about 22 s warm. Read shuffling (`uring.spread_reads`) makes no measurable difference once in-flight reads are capped.
- **SF10000 top 5 (Q9, Q21, Q18, Q7, Q14):** 255.5 s cold / 176.5 s warm against the 667.6 / 252.4 baseline, so 2.6× faster cold and 30% faster warm. Answers are correct, spill is unchanged and nothing reached disk. The per-query table is in `experiments-summary.md` under "Cold-read fix".
- **Proper fix in code:** make the in-flight read cap a uring setting, sized independently of the host pool's block size (step 4 below).

## The problem

- **Cold runs cost far more than warm ones.** In the SF10000 sweep (4 ranks), all 22 queries total 2,128 s cold and 685 s warm: a 1,444 s gap.
- **Profiles of Q12, Q19 and Q21:**
  - Scans take 93–98% of cold wall time.
  - A cold lineitem scan reads 5–7 GB/s across the node, against 18–28 GB/s warm.
  - The kernel uses 774–1,205 s of CPU per rank cold, against 75 s warm (Q12). Most of it is in io_uring's io-wq worker threads.

## Causes ruled out

| Hypothesis | Test | Result |
|---|---|---|
| Host pool first-touch | 180 GiB pinned at startup | Q12 cold 137 s; no change |
| Per-file scan preparation (footers, row-group plans) | Trace timestamps | Finishes 4–9 s into the query |
| Too few readers | `uring_n_reactors: 16`; `executor.pipeline.num_threads: 8` | 143 s and 112 s cold; no clear gain, warm got worse |
| Per-process state | Fresh processes; warm-up scans in the same process | Misleading: the effect depends on the data's cache state below GPFS (next section) |

**Two earlier conclusions were wrong:**
- **"Storage delivers 62.5 GB/s cold."** That `l_comment` read hit cached blocks. GPFS here uses 16 MiB blocks and a lineitem row group is about 4.9 MB, so reading any column warms every column of the roughly 3 row groups in a block.
- **"The penalty is per process."** A warm-up scan helped only because it warmed the same blocks.

## Cause, reproduced standalone

`/tmp/aocsa/uring-probe/uring_probe.c` replays Sirius's local read path:
- a buffered descriptor and an O_DIRECT reopen through `/proc/self/fd` per file;
- 4 rings with `SINGLE_ISSUER|COOP_TASKRUN|DEFER_TASKRUN`;
- 64 × 1 MiB registered bounce slots per reactor;
- 4 KiB-aligned reads of up to 16 MiB.

The reads come from manifests (`manifest.py`) of Q12's lineitem column chunks: about 300 KB per read, three reads per row group. Each variant ran on its own set of 20 files not read recently.

| Variant | Cold GB/s | Kernel CPU | Warm GB/s |
|---|---|---|---|
| Sirius's order, 3 controls | 5.9 / 4.5 / 7.1 | 19–25 s | 27–30 |
| **Same reads, in-flight reads spread across files** | **33.9** | **5.0 s** | 41 |
| 8 far-apart parts of the same file interleaved | 19.3 | 6.4 s | 30 |
| 5 files interleaved | 19.5 | 9.1 s | 46 |
| 16 slots per reactor (fewer in flight) | 9.0 | 6.3 s | 33 |
| 256 slots per reactor (more in flight) | **1.22** | **731 s** | 24 |
| One read per row-group span (3.75× the bytes) | 40.0 raw, 10.7 useful | 4.3 s | 48 |
| Buffered reads | 12.7 | 10.0 s | 12.4 |

**Mechanism.**
- Each scan task's read of one file becomes at most two requests (`src/io/templated_ioctx.hpp:242-268`, `:350-363`).
- Each reactor consumes a request's slices front to back (`active->take_front()`, `src/io/uring/uring_reactor.cpp:1035`).
- So up to 64 concurrent ~300 KB O_DIRECT reads land inside one or two 16 MiB GPFS blocks. When storage hasn't cached those blocks, the reads contend and the io-wq workers burn kernel CPU.
- Spreading in-flight reads across blocks removes most of the contention.
- Too many small reads in flight in total makes it worse, whatever their order.

The kernel-level path is inferred from these experiments, not observed directly. That needs root `perf` or GPFS's `mmfsadm`, and `perf_event_paranoid` is 4 here.

## Fix 1: `uring.spread_reads` (tried, then reverted)

Reverted on 2026-10-25 because it showed no gain once in-flight reads were capped. The patch, with the `SIRIUS_URING_STATS` counters, is saved as `patches/spread-reads-and-uring-stats.patch`.

- **What it does:** when a request reaches a uring reactor, its slices are shuffled into a fixed pseudo-random order, seeded with the slice count. Each slice carries its own buffers and completion callback, so order has no effect on results.
- `src/io/uring/config.hpp`: adds `bool spread_reads{true}`, with the reasoning in its comment.
- `src/sirius_config.cpp`: adds the YAML key `executor.scan_manager.uring.spread_reads`.
- `src/io/uring/uring_reactor.cpp` (`uring_reactor::enqueue`): adds the shuffle and `#include <random>`.
- The built tree is `/tmp/aocsa/wt/dpo-rank-pin` (build log `dpo-rank-pin.build13.log`). Setting `spread_reads: false` restores the old order, so both arms run on one build.

## Next steps, in order

1. **Finish the Q12 A/B** (fix off/on alternating, fresh process per run), then run the SF10000 top 5 (Q9, Q21, Q18, Q7, Q14, 3 iterations) on and off. Baseline: 667.6 s cold, 252.4 s warm.
2. **Fewer reads in flight across the node.** 4 ranks × 4 reactors × 64 slots puts about 1,024 small reads in flight, which the probe shows is the bad regime. Configs are ready in `/tmp/aocsa/tpch22/dpo-best/spread-q12v/`:
   - `on2r`: 2 reactors per rank.
   - `onb4`: 4 MiB host blocks, which gives 16 slots per reactor because the staging budget is fixed at 64 MiB (`uring_reactor.cpp:63-64`).
3. **Block-aware issue in the reactor.** Track in-flight 16 MiB block ids per reactor. Skip a slice whose block is busy and take the next one with a bounded lookahead, round-robin across queued requests. This makes "spread out" deterministic rather than random, and it caps contention per block directly.
4. **Make the in-flight cap a uring setting, sized by read size.** The 64 MiB of staging per reactor is hard-coded today, and the host block size controls the in-flight count only as a side effect. A single count doesn't fit every query:
   - 4 MiB blocks (16 slots per reactor) make Q12 3× faster cold, but slow Q1 warm from 19.3 s to 24.7–28.4 s.
   - 2 MiB blocks (32 slots) make Q1 warm 16.1 s, but Q12 cold 61.9 s instead of about 40.
   - Q1 makes large contiguous reads; Q12 makes about 300 KB reads.

   Proposal:
   - Cap the number of concurrent small reads (for example under 1 MiB) per reactor at about 16, sized per filesystem.
   - Let large reads fill the rest of the staging budget.
   - Keep the host block size at its default so spill and pinned tables are unaffected.

   **Built and measured (2026-10-25, uncommitted in `/tmp/aocsa/wt/dpo-rank-pin`):**
   - **New keys:** `executor.scan_manager.uring.max_inflight_small_reads` (default 0, which means off) and `small_read_bytes` (default 1 MiB).
   - **The gate:** in `dispatch_pending` (`src/io/uring/uring_reactor.cpp`), an op smaller than `small_read_bytes` waits while the reactor already has that many small reads in flight. A slot is counted from dispatch until it leaves the reading state.
   - **Unit test:** "io_uring small-read cap still completes every read" in `test/cpp/io/uring/test_uring_readv.cpp`. With a cap of 1 and mixed 16 KiB / 256 KiB slices, every byte comes back correct. All 13 `[uring_readv]` tests pass.
   - **Config used:** 1 MiB host blocks, `uring: { max_inflight_small_reads: 16, small_read_bytes: 1MiB }`.

   | Setting | Q1 cold / warm | Q12 cold / warm | Top 5 cold / warm |
   |---|---|---|---|
   | 1 MiB, no cap | 28.8 / 19.3 | 122–126 / 34 | 667.6 / 252.4 |
   | 4 MiB blocks | 25.2–29.3 / 24.7–28.4 | 36.9–42.4 / 20.0–22.3 | 255.5 / 176.5 |
   | **1 MiB + small-read cap 16** | **28.8, 27.6 / 19.5, 20.8** | **42.3, 41.4 / 22.0, 21.7** | **278.4 / 181.0** |

   - **The cap removes Q1's warm loss and keeps Q12's cold gain.**
   - **On the top 5 it is 2.4× faster cold than baseline, but about 9% slower cold than 4 MiB blocks** (278.4 against 255.5) and 2.5% slower warm. Q18 and Q7 account for most of the gap: 61.8 against 49.6–57.5, and 51.0 against 43.6–46.1. Each is one run, so part of this may be noise.
   - **A likely cause:** these queries also issue many reads just above 1 MiB, which the cap doesn't limit and 4 MiB blocks did.
   - **Next tuning:** `small_read_bytes: 2MiB` and 4MiB, then cap 12.

   **Tuning and the 22-query run (2026-10-25).**
   - **Top 5, cold / warm:** threshold 2 MiB gave 279.7 / 173.2 s, and 4 MiB gave 270.3 / 178.7 s, against 255.5 / 176.5 with 4 MiB blocks. The thresholds differ by no more than run-to-run noise. Q1 stays fixed at every threshold.
   - **All 22 queries with cap 16 and a 4 MiB threshold:** 806.7 s cold / 507.3 s warm, against 797.1 / 510.4 with 4 MiB blocks and 2,128.3 / 684.5 at baseline.
   - **So the cap matches 4 MiB blocks across the benchmark** and keeps the default 1 MiB host block size. It avoids the Q1 and Q6 warm slowdowns of 4 MiB blocks (Q1 20.4 s against 24.7, Q6 17.3 against 21.7).
   - **Answers:** all correct. Q3's warm iteration and Q18 differ only in tied rows at the LIMIT boundary.
   - **Output:** `/tmp/aocsa/tpch22/dpo-best/sf10k-22-cap4m/` and `cap-tune/`.

   **SF3000 top 5, same build, cap off vs on, alternating order, 3 iterations (cold / best warm, s):**

   | Q | No cap | Cap 16 at 4 MiB | Cold speedup |
   |---|---|---|---|
   | Q9 | 60.1 / 13.5 | 20.8 / 11.6 | 2.9× |
   | Q21 | 62.4 / 28.4 | 25.5 / 17.8 | 2.4× |
   | Q18 | 41.8 / 13.7 | 18.1 / 12.5 | 2.3× |
   | Q7 | 60.7 / 14.2 | 18.9 / 9.0 | 3.2× |
   | Q14 | 55.2 / 8.9 | 16.0 / 5.9 | 3.4× |
   | **Total** | **280.1 / 78.8** | **99.4 / 57.0** | **2.82× cold, 1.38× warm** |

   All answers match the reference. Output: `/tmp/aocsa/tpch22/dpo-best/sf3k-cap/`.
5. **Optional: larger reads.** Merge chunks within a row group when the gap is small (`merge_gap_size()` is 4 KiB today, `src/io/uring/config.hpp`). Row-group span reads cost 3.75× the bytes for Q12. That's worth it only for queries that read most columns, so it should be a per-scan decision.

## Measurement rules (learned the hard way)

- **Storage caches recently read blocks** below GPFS. Re-reading the same data within minutes runs warm, and streaming 1.15 TB of other data did not evict it. A "cold" result is valid only when that data wasn't read recently.
- **Use alternating arms in fresh processes, on one build**, with `spread_reads` as the switch. Run several pairs. A single cold number varies about ±20%.
- Cold runs of a Sirius query stayed slow across repeated fresh-process runs (Q12 at 112–143 s, six runs), so query-level A/B is still meaningful even though the probe's re-reads warm up.

## Tests

- **Unit test** in `test/cpp/io`: enqueue a multi-slice request with `spread_reads` on and check that every slice completes once, that bytes land in the right buffers, and that the order is deterministic across runs. The existing uring reactor tests must pass with the default on.
- **Correctness:** the 4-rank answer check (`run_query.py --ref`) on every benchmark run, plus TPC-H SF1/SF10 against DuckDB.
- **Other filesystems:** check local NVMe with the fix on. O_DIRECT has no kernel readahead to lose, so little change is expected, but it should be measured before defaulting on everywhere.

## Delivery

One self-contained PR against `main` (per `CONTRIBUTING.md`): the config key, the shuffle, the unit test, and a short note in the scan docs (`docs/super-sirius/scan.md`). Steps 3–4 follow as a separate PR if step 2 shows the in-flight cap matters.

## Open questions

1. Root-level confirmation of the kernel/GPFS contention point.
2. Whether the storage-side cache behavior (block heat or promotion) is specific to this cluster.
3. Interaction with `cache.mode: sirius` and the readahead path (`max_readahead_bytes`), which issue reads differently.
4. Whether REST/S3 would benefit from a similar spread. It doesn't go through this reactor.

## Results

| Run | Q12 cold (s) | Q12 warm (s) |
|---|---|---|
| fix off, r1 | 126.4 | 33.9 |
| fix on, r2 | 105.3 | 33.7 |
| fix off, r3 | 122.4 | 34.0 |
| fix on, r4 | 122.1 | 32.9 |

**Verdict:** `spread_reads` alone is not a clear win in Sirius. Off averages 124.4 s cold and on averages 113.7 s, within run-to-run noise. All answers are correct in both arms.

**Second finding:** Sirius's steady cold penalty is not the storage-cache effect the probe shows.
- Between 01:25 and 01:33 the probe read all 600 lineitem files, and a check at about 01:30 showed them warm in the storage cache. Sirius Q12 at 01:34–01:37 was still 126 s cold.
- Back-to-back fresh Sirius processes stay slow on data another process read seconds earlier (runs r1–r4 above), while the second iteration in the same process is always fast (33–34 s).
- So the penalty lives inside the Sirius process and is tied to files that process hasn't read yet.
- The probe still shows that uncached blocks plus neighbouring reads are slow, but that is a second, separate effect.
- **Next:** add per-reactor I/O counters (`SIRIUS_URING_STATS`) and compare cold with warm iterations: op latency, reads in flight, H2D copy wait, and synchronous host reads.

### Fewer reads in flight (Q12, SF10000, each run a fresh process, cold + 1 warm)

| Setting | Reads in flight per rank | Cold (s) | Warm (s) |
|---|---|---|---|
| Baseline: 4 reactors, 1 MiB blocks, fix off | 256 | 126.4, 122.4 | 33.9, 34.0 |
| 4 reactors, 1 MiB blocks, fix on | 256 | 105.3, 122.1 | 33.7, 32.9 |
| 2 reactors, 1 MiB blocks, fix off | 128 | 106.6, 98.3 | 31.1, 22.1 |
| 2 reactors, 1 MiB blocks, fix on | 128 | 94.1, 104.9, 95.0 | 22.0, 21.7, 21.6 |
| **4 reactors, 4 MiB blocks, fix off** | **64** | **41.0, 38.8** | **20.4, 20.6** |
| **4 reactors, 4 MiB blocks, fix on** | **64** | **37.7, 36.9, 41.6** | **22.3, 21.0, 20.0** |

- Every run returned the correct answer.
- One run with a broken config, where `run_query.py`'s `--host-pool` regex ate a comma, is excluded.
- The instrumented counters (`SIRIUS_URING_STATS`) are built but haven't been needed yet.

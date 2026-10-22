# Tuning for more GPUs, more VRAM, more host memory

Everything here is per **rank**: one process, one GPU. `scripts/make_config.sh` writes the YAML
from environment variables; `scripts/run_ranks.sh` starts the ranks. Values marked *measured*
were run on the reference box (2x RTX PRO 4500, 32 GB each; 181 GB RAM; one NVMe). The rest is
derived from the code and has not been benchmarked: verify it on your hardware.

## Quick reference

| You have | Change | Where |
|---|---|---|
| More GPUs on one host | `run_ranks.sh <N>`, `GPUS="0 1 ... N-1"` | launcher |
| More GPUs on one host | `HOST_CAPACITY` = your host budget / N (never leave it unset with N > 1) | `make_config.sh` |
| More GPUs on one host | lower `GPU_USAGE_FRACTION` a little as N grows (exchange rings sit outside it) | `make_config.sh` |
| More VRAM per GPU | keep `GPU_USAGE_FRACTION` at 0.95; optionally larger batches (`HASH_PARTITION_BYTES`, untested) | `make_config.sh` |
| More host RAM | `CACHE_MODE=os` once RAM holds the query's columns; raise `HOST_CAPACITY` | `make_config.sh` |
| Little host RAM | `CACHE_MODE=none` (O_DIRECT) and a disk tier (`SPILL_DIR`, `DISK_CAPACITY`) | `make_config.sh` |
| Several hosts | peers list across hosts, shared storage, RDMA in `UCX_TLS` (untested) | see below |

## GPUs: how many ranks

- **One rank per GPU.** `run_ranks.sh N file.sql logdir` starts N processes; rank i gets
  `CUDA_VISIBLE_DEVICES=${GPUS[i]}` and listens on `BASE_PORT + i` (default 27000..27000+N-1).
- **Scan split:** each table's local files are cut into N equal byte spans; a rank reads the row
  groups whose start offset is in its span. Balance is at row-group granularity: TPC-H lineitem
  row groups are ~69 MB, so this is even for big tables at any N; a 1-row-group table (nation,
  region) lands on one rank, which is fine.
- **Exchange buffers on the GPU, outside the Sirius memory limit**, per rank:
  `frame_bytes x (send_frames + recv_frames_per_peer x (N - 1))` = 8 MiB x (16 + 8 (N - 1)):
  192 MiB at N = 2, 576 MiB at N = 8, 1.1 GiB at N = 16. Leave room for it:
  `GPU_USAGE_FRACTION` 0.95 at N <= 4 on 32 GB GPUs, about 0.93 at N = 8 (derived). Sizes are
  compile-time defaults in `src/exchange/exchange_runtime.hpp` (`nixl_exchange_options`).
- **Credits:** each peer owns 8 receive frames of a rank's ring, whatever N is. With many ranks
  sending to one, those few frames per peer may limit throughput (derived, not measured); raise
  `recv_frames_per_peer` in source if a profile shows senders waiting for credits.
- **Queued sends:** a rank's send() blocks once 1 GiB is queued for peers
  (`transport::options::max_queued_bytes`); those batches are on the GPU and cannot spill.
- **Bloom filters across ranks:** one per join key, sized from the planner's build estimate
  clamped to [2^16, 2^26] keys (`min/max_bloom_keys` in `src/planner/distributed_plan.cpp`);
  at most 64 keys per join. Not affected by N.

## GPU memory (VRAM)

| Variable (YAML key) | Default | Meaning |
|---|---|---|
| `GPU_USAGE_FRACTION` (`memory.gpu.usage_limit_fraction`) | 0.95 | share of VRAM Sirius manages; the rest is CUDA context, cuDF temporaries, NIXL rings |
| `GPU_DOWNGRADE_AT` (`memory.gpu.downgrade_trigger_fraction`) | 0.8 | start spilling GPU batches to host above this share of the limit |
| `GPU_DOWNGRADE_STOP` (`memory.gpu.downgrade_stop_fraction`) | 0.6 | stop spilling at this share |
| `HASH_PARTITION_BYTES` (`operator_params.hash_partition_bytes`) | 768 MiB | target size of a hash partition (joins, group-bys) |

- *Measured:* lowering `GPU_USAGE_FRACTION` to 0.75 did **not** fix the SF3000 OOM on 32 GB GPUs;
  the fixes were in the engine (spillable exchange landing, routing in `EXCHANGE_SINK::execute`).
  Keep 0.95 unless the log shows `CUDA error ... failed to allocate` (memory outside Sirius's
  limit), then lower it.
- With more VRAM per GPU (80 GB, 141 GB, 180 GB) less is spilled. Larger
  `HASH_PARTITION_BYTES` (e.g. 2Gi on 80 GB+) means fewer, larger tasks; untested here.
- Check spilling in `rank*/log/sirius_*.log`: `downgrade_executor ... request done: N batches,
  B bytes ... to_host: ... to_disk: ...`. In an SF3000 Q5 run (original plan, before the engine fixes)
  on 32 GB GPUs, the two ranks moved 220 GB GPU-to-host in 35 s, re-spills included, and nothing
  to disk.

## Host memory

| Variable (YAML key) | Default | Meaning |
|---|---|---|
| `HOST_CAPACITY` (`memory.host.capacity_bytes`) | unset: **0.9 of the NUMA node's RAM, per process** | pinned host tier (GPU spill target); pools of `block_size x pool_size` = 128 MiB, 4 preallocated, more on demand |
| `CACHE_MODE` (`executor.scan_manager.cache.mode`) | `os` here (Sirius default `none`) | `os`: buffered reads through the page cache; `none`: O_DIRECT |
| `DISK_CAPACITY`, `SPILL_DIR` (`memory.disk`) | 300G, `$SPILL_DIR` | disk tier below host; required when the host tier fills |

- **Several ranks per host: always set `HOST_CAPACITY`.** The default lets every process claim up
  to 90% of RAM. Budget: `HOST_CAPACITY x ranks + page cache for the query + OS headroom <= RAM`.
- **Page cache vs O_DIRECT** (*measured*): with `CACHE_MODE=os` and the query's columns cached, Q12
  SF1000 went from 5.0 s to 3.5 s on 1 GPU and from 5.2 s to 2.0 s on 2 GPUs; with O_DIRECT the
  single NVMe (~6.4 GB/s) capped both at ~5 s, so a second GPU gained nothing. The first run after
  dropping the cache is slower with `os` (7 s vs 5 s).
- Columns a query reads (*measured* from parquet metadata): Q12 ~32 GB at SF1000; Q5 ~156 GB at SF3000
  (so ~52 GB at SF1000). If RAM
  can't hold them plus the host tiers, prefer `CACHE_MODE=none` and more NVMe bandwidth (RAID0
  across drives) or faster storage.
- *Measured:* at SF3000 on 181 GB RAM only ~78 GB of Q5's 156 GB stayed cached, so warm runs still
  read about half from disk.
- **Disk tier:** spill files are created with `mkstemp`, so all ranks on a host can share
  `SPILL_DIR`. Use local NVMe. Empty it between benchmark runs (`bench.sh` does with `COLD=1`).

## CPU threads

| Variable (YAML key) | Default | Note |
|---|---|---|
| `PIPELINE_THREADS` (`executor.pipeline.num_threads`) | Sirius picks | GPU task workers per rank |
| `SCAN_THREADS` (`executor.scan_manager.num_threads`) | Sirius picks | must be > 2 |

With N ranks on one host, each rank sizes its pools from all cores; on many-GPU hosts set both to
about cores / N.

## Several hosts (untested)

The exchange supports it by design (NIXL over UCX), but only one host was tested.
- `sirius_exchange_peers` lists every rank's `host:port` in rank order, the same string on every
  host; each process sets its own `sirius_exchange_rank`. `run_ranks.sh` builds a single-host
  list; adapt it to start ranks per host with a global numbering.
- Every rank must see the same files with the same sizes (byte-range tiling): shared storage
  (NFS/Lustre/S3 mount) with identical paths.
- `scripts/nixl_env.sh` sets `UCX_TLS=cuda_copy,cuda_ipc,tcp,self`; across hosts add RDMA
  transports (e.g. `rc,ud` or `dc` on InfiniBand/RoCE, and `gdr_copy` if installed).
- Every rank must run the same statements in the same order: stream ids are numbered per query.

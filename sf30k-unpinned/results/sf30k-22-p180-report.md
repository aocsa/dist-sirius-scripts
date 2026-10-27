# TPC-H SF30000, unpinned, distributed Sirius: 22-query run

22 of 22 queries reported.

Setup:

- **Hardware:** 4× GB200, one rank per GPU, one host.
- **Data:** `/scratch/prestouser/aocsa/tpch_sf30000` (12.7 TB Parquet on GPFS, O_DIRECT).
- **Build:** `perf/sf10k-pinned-repro` plus the Q21 chunked-build fix.
- **Config:** p180 (180Gi host pool per rank, 1 TB disk spill per rank on /scratch,
  small-read cap 16 below 4 MiB, 4 GiB bloom cap).
- **Runs:** each query in fresh processes, twice: cold, then hot.
- **Answers:** run 2 is compared with run 1, since no SF30000 reference exists.

Column notes:

- **Rows:** result rows.
- **Disk spill:** host→disk bytes summed over both runs and all ranks.
- **Host peak:** the largest per-rank host-pool peak.
- **Min free:** the lowest free memory on NUMA node 0 / node 1 during the query.
- **×SF10K:** the ratio to the SF10000 best unpinned run (806.7 s cold / 507.3 s hot).

| Query | Cold (s) | Hot (s) | ×SF10K cold | ×SF10K hot | Rows | Answers | Disk spill | Host peak | Min free |
|---|---:|---:|---:|---:|---:|---|---:|---:|---|
| Q1 | 63.0 | 54.6 | 2.83 | 2.67 | 4 | consistent | 0 GB | 0 GiB | 390G/404G |
| Q2 | 40.4 | 23.3 | 2.89 | 3.04 | 100 | consistent | 0 GB | 3 GiB | 398G/412G |
| Q3 | 129.0 | 83.9 | 3.01 | 3.29 | 10 | consistent | 0 GB | 158 GiB | 123G/113G |
| Q4 | 90.0 | 52.1 | 2.69 | 3.04 | 5 | consistent | 0 GB | 12 GiB | 364G/381G |
| Q5 | 132.9 | 76.9 | 2.81 | 2.92 | 5 | consistent | 0 GB | 10 GiB | 368G/383G |
| Q6 | 87.4 | 49.1 | 2.70 | 2.83 | 1 | consistent | 0 GB | 0 GiB | 390G/403G |
| Q7 | 138.8 | 89.5 | 2.81 | 2.94 | 4 | consistent | 0 GB | 18 GiB | 353G/365G |
| Q8 | 128.0 | 105.4 | 2.76 | 3.31 | 2 | consistent | 0 GB | 19 GiB | 350G/363G |
| Q9 | 217.1 | 139.2 | 4.12 | 3.75 | 175 | consistent | 89 GB | 180 GiB | 28G/48G |
| Q10 | 198.8 | 124.8 | 4.65 | 3.98 | 20 | consistent | 115 GB | 180 GiB | 28G/51G |
| Q11 | 40.4 | 25.8 | 2.90 | 2.79 | 29,971,985 | consistent | 0 GB | 0 GiB | 248G/393G |
| Q12 | 128.9 | 60.1 | 3.19 | 2.94 | 2 | consistent | 0 GB | 4 GiB | 385G/400G |
| Q13 | 55.9 | 50.7 | 2.93 | 3.71 | 52 | consistent | 0 GB | 127 GiB | 171G/208G |
| Q14 | 123.9 | 68.7 | 3.31 | 3.36 | 1 | consistent | 0 GB | 47 GiB | 296G/315G |
| Q15 | 105.7 | 62.5 | 2.64 | 3.00 | 1 | consistent | 0 GB | 0 GiB | 388G/405G |
| Q16 | 20.9 | 9.6 | 2.11 | 2.08 | 27,840 | consistent | 0 GB | 14 GiB | 378G/396G |
| Q17 | 134.4 | 103.4 | 3.06 | 3.54 | 1 | consistent | 0 GB | 2 GiB | 386G/402G |
| Q18 | 235.4 | 188.7 | 4.42 | 5.10 | 100 | consistent | 1105 GB | 180 GiB | 31G/45G |
| Q19 | 130.7 | 77.8 | 2.96 | 2.92 | 1 | consistent | 0 GB | 0 GiB | 390G/404G |
| Q20 | 102.6 | 70.9 | 3.14 | 3.34 | 5,079,748 | consistent | 0 GB | 1 GiB | 387G/402G |
| Q21 | 217.4 | 166.1 | 2.72 | 3.07 | 100 | consistent | 0 GB | 42 GiB | 304G/318G |
| Q22 | 21.0 | 13.7 | 2.45 | 2.91 | 7 | consistent | 0 GB | 0 GiB | 401G/416G |
| **Total (22 queries)** | **2542.7** | **1696.8** | **3.15** | **3.34** | | | **1309 GB** | | |

- **SF10000 best, same queries:** 806.7 s cold, 507.3 s hot.
- **Failures:** 0. Guard kills: 0.

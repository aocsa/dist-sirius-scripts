// Standalone reproduction of Sirius's local parquet read path (src/io/uring/uring_reactor.cpp):
// per file a buffered fd plus an O_DIRECT fd reopened through /proc/self/fd, N reactor threads each
// with its own io_uring (SINGLE_ISSUER|COOP_TASKRUN|DEFER_TASKRUN), 64 x 1 MiB bounce slots per
// reactor, 4 KiB-aligned reads split into ops of up to 16 MiB (readv of 1 MiB iovecs, READ_FIXED for
// a single block). Reads every "R" range of a manifest, `passes` times in one process.
//
// usage: uring_probe <manifest> [key=value ...]
//   reactors=4 passes=2 footer=1 (buffered pread of each file's footer first)
//   reopen=1 (O_DIRECT via /proc/self/fd/N; 0 = open(path, O_DIRECT)) keep_buffered=1
//   direct=1 (0 = all reads on the buffered fd) fixed=1 (register slots with io_uring)
//   flags=1 (SINGLE_ISSUER|COOP|DEFER; 0 = plain ring) depth=64 slots=64 slot_kb=1024 maxop_kb=16384
//   mlock=1 (pin the slots like cudaHostRegister would) files=0 (0 = all files in the manifest)
//   iowq_max=0 (io_uring_register_iowq_max_workers bounded limit; 0 = kernel default)
//   order=file (file: one reactor per file round-robin; rr: ranges dealt round-robin to reactors)
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <liburing.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

typedef struct { int file; long off, len; } range_t;
typedef struct { char path[512]; long foot_off, foot_len; int bfd, dfd; } file_t;

static file_t* F; static int nf;
static range_t* R; static long nr;
static int window = 0, same_file = 0, shuffle = 0, reactors = 4, passes = 2, footer = 1, reopen_ = 1, keep_buffered = 1, direct = 1, fixed = 1,
           flags = 1, depth = 64, slots = 64, mlock_ = 1, files_lim = 0, iowq_max = 0, order_rr = 0;
static long slot_sz = 1L << 20, maxop = 16L << 20;
static atomic_long done_bytes, fallbacks, short_reads;

static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec * 1e-9; }
static double cpu(int who_sys) {
  struct rusage u; getrusage(RUSAGE_SELF, &u);
  struct timeval tv = who_sys ? u.ru_stime : u.ru_utime; return tv.tv_sec + tv.tv_usec * 1e-6;
}

typedef struct { int id; long* idx; long n; } work_t;

static void* reactor(void* arg) {
  work_t* w = arg;
  struct io_uring ring; struct io_uring_params p; memset(&p, 0, sizeof p);
  if (flags) p.flags = IORING_SETUP_SINGLE_ISSUER | IORING_SETUP_COOP_TASKRUN | IORING_SETUP_DEFER_TASKRUN;
  if (io_uring_queue_init_params(depth, &ring, &p) < 0) { perror("ring"); exit(1); }
  if (iowq_max) { unsigned v[2] = {iowq_max, 0}; io_uring_register_iowq_max_workers(&ring, v); }
  char* buf = aligned_alloc(4096, slots * slot_sz);
  memset(buf, 0, slots * slot_sz);
  if (mlock_ && mlock(buf, slots * slot_sz)) perror("mlock");
  struct iovec* reg = calloc(slots, sizeof *reg);
  for (int i = 0; i < slots; i++) reg[i] = (struct iovec){buf + i * slot_sz, slot_sz};
  int fixed_ok = fixed && io_uring_register_buffers(&ring, reg, slots) == 0;
  // Each in-flight op owns a run of contiguous slots; a simple free-slot bitmap.
  char* busy = calloc(slots, 1);
  typedef struct { int first, nslots; struct iovec iov[64]; } op_t;
  op_t* ops = calloc(slots, sizeof *ops);
  long next = 0; long cur_off = -1, cur_end = 0; int cur_file = -1; int inflight = 0;
  for (;;) {
    // Fill: next op from the current range, up to maxop and the free contiguous slots.
    while (inflight < depth) {
      if (cur_off < 0 || cur_off >= cur_end) {
        if (next >= w->n) break;
        range_t* r = &R[w->idx[next++]]; cur_file = r->file; cur_off = r->off; cur_end = r->off + r->len;
      }
      long want = cur_end - cur_off; if (want > maxop) want = maxop;
      int need = (int)((want + slot_sz - 1) / slot_sz);
      int first = -1;
      for (int s = 0; s + need <= slots; s++) {
        int ok = 1; for (int k = 0; k < need; k++) if (busy[s + k]) { ok = 0; s += k; break; }
        if (ok) { first = s; break; }
      }
      if (first < 0) break;
      for (int k = 0; k < need; k++) busy[first + k] = 1;
      op_t* o = &ops[first]; o->first = first; o->nslots = need;
      long left = want;
      for (int k = 0; k < need; k++) {
        long l = left < slot_sz ? left : slot_sz;
        o->iov[k] = (struct iovec){buf + (first + k) * slot_sz, (size_t)l}; left -= l;
      }
      struct io_uring_sqe* sqe = io_uring_get_sqe(&ring);
      int fd = direct && F[cur_file].dfd >= 0 ? F[cur_file].dfd : F[cur_file].bfd;
      if (need == 1 && fixed_ok && o->iov[0].iov_len == (size_t)slot_sz)
        io_uring_prep_read_fixed(sqe, fd, o->iov[0].iov_base, slot_sz, cur_off, first);
      else if (need == 1)
        io_uring_prep_read(sqe, fd, o->iov[0].iov_base, o->iov[0].iov_len, cur_off);
      else
        io_uring_prep_readv(sqe, fd, o->iov, need, cur_off);
      io_uring_sqe_set_data64(sqe, (unsigned long)first);
      cur_off += want; inflight++;
    }
    if (inflight == 0) break;
    io_uring_submit_and_wait(&ring, 1);
    struct io_uring_cqe* cqe; unsigned head, seen = 0;
    io_uring_for_each_cqe(&ring, head, cqe) {
      op_t* o = &ops[io_uring_cqe_get_data64(cqe)];
      if (cqe->res < 0) { fprintf(stderr, "read error %s\n", strerror(-cqe->res)); atomic_fetch_add(&fallbacks, 1); }
      else {
        long want = 0; for (int k = 0; k < o->nslots; k++) want += o->iov[k].iov_len;
        if (cqe->res < want) atomic_fetch_add(&short_reads, 1);
        atomic_fetch_add(&done_bytes, cqe->res);
      }
      for (int k = 0; k < o->nslots; k++) busy[o->first + k] = 0;
      inflight--; seen++;
    }
    io_uring_cq_advance(&ring, seen);
  }
  io_uring_queue_exit(&ring);
  return NULL;
}

static int opt(int argc, char** argv, const char* k, int d) {
  size_t n = strlen(k);
  for (int i = 2; i < argc; i++) if (!strncmp(argv[i], k, n) && argv[i][n] == '=') return atoi(argv[i] + n + 1);
  return d;
}

int main(int argc, char** argv) {
  if (argc < 2) { fprintf(stderr, "usage: %s manifest [k=v...]\n", argv[0]); return 2; }
  reactors = opt(argc, argv, "reactors", 4); passes = opt(argc, argv, "passes", 2); footer = opt(argc, argv, "footer", 1);
  reopen_ = opt(argc, argv, "reopen", 1); keep_buffered = opt(argc, argv, "keep_buffered", 1); direct = opt(argc, argv, "direct", 1);
  fixed = opt(argc, argv, "fixed", 1); flags = opt(argc, argv, "flags", 1); depth = opt(argc, argv, "depth", 64);
  slots = opt(argc, argv, "slots", 64); slot_sz = (long)opt(argc, argv, "slot_kb", 1024) << 10;
  maxop = (long)opt(argc, argv, "maxop_kb", 16384) << 10; mlock_ = opt(argc, argv, "mlock", 1);
  files_lim = opt(argc, argv, "files", 0); iowq_max = opt(argc, argv, "iowq_max", 0); order_rr = opt(argc, argv, "order_rr", 0); shuffle = opt(argc, argv, "shuffle", 0); window = opt(argc, argv, "window", 0); same_file = opt(argc, argv, "same", 0);
  FILE* m = fopen(argv[1], "r"); if (!m) { perror("manifest"); return 1; }
  F = calloc(4096, sizeof *F); R = malloc(sizeof *R * 4000000L);
  char kind, path[512]; long a, b;
  while (fscanf(m, " %c %511s %ld %ld", &kind, path, &a, &b) == 4) {
    if (kind == 'F') {
      if (files_lim && nf >= files_lim) break;
      strcpy(F[nf].path, path); F[nf].foot_off = a; F[nf].foot_len = b; nf++;
    } else R[nr++] = (range_t){nf - 1, a, b};
  }
  long total = 0; for (long i = 0; i < nr; i++) total += R[i].len;
  printf("files %d ranges %ld bytes %.2f GB avg %.0f KB | reactors=%d footer=%d reopen=%d keep_buffered=%d direct=%d fixed=%d flags=%d depth=%d slots=%d slot_kb=%ld maxop_kb=%ld mlock=%d iowq_max=%d order_rr=%d shuffle=%d window=%d same=%d\n",
         nf, nr, total / 1e9, total / 1e3 / nr, reactors, footer, reopen_, keep_buffered, direct, fixed, flags, depth, slots,
         slot_sz >> 10, maxop >> 10, mlock_, iowq_max, order_rr, shuffle, window, same_file);
  double t0 = now(), s0 = cpu(1);
  char* fb = aligned_alloc(4096, 64 << 20);
  for (int i = 0; i < nf; i++) {
    F[i].bfd = open(F[i].path, O_RDONLY);
    if (F[i].bfd < 0) { perror(F[i].path); return 1; }
    char pp[64]; snprintf(pp, sizeof pp, "/proc/self/fd/%d", F[i].bfd);
    F[i].dfd = open(reopen_ ? pp : F[i].path, O_RDONLY | O_DIRECT);
    if (footer) {
      long left = F[i].foot_len, off = F[i].foot_off;
      while (left > 0) { long n = pread(F[i].bfd, fb, left < (64 << 20) ? left : (64 << 20), off); if (n <= 0) break; left -= n; off += n; }
    }
    if (!keep_buffered && direct) { close(F[i].bfd); F[i].bfd = -1; }
  }
  printf("open%s %.2fs sys %.1fs\n", footer ? "+footer" : "", now() - t0, cpu(1) - s0);
  // Work split: ranges grouped by file, files dealt round-robin to reactors (or ranges round-robin).
  work_t* w = calloc(reactors, sizeof *w);
  for (int r = 0; r < reactors; r++) { w[r].id = r; w[r].idx = malloc(sizeof(long) * nr); }
  for (long i = 0; i < nr; i++) { int r = order_rr ? (int)(i % reactors) : R[i].file % reactors; w[r].idx[w[r].n++] = i; }
  if (window > 1) {  // interleave W streams: W files at a time (same=0) or W far-apart segments of one file (same=1)
    for (int r = 0; r < reactors; r++) {
      long n = w[r].n, *in = w[r].idx, *out = malloc(sizeof(long) * n), k = 0;
      long* fstart = malloc(sizeof(long) * (n + 1)); long nfs = 0;  // starts of each file's run
      for (long i = 0; i < n; i++) if (i == 0 || R[in[i]].file != R[in[i - 1]].file) fstart[nfs++] = i;
      fstart[nfs] = n;
      if (same_file) {
        for (long f = 0; f < nfs; f++) {
          long a = fstart[f], len = fstart[f + 1] - a, seg = (len + window - 1) / window;
          for (long j = 0; j < seg; j++) for (int q = 0; q < window; q++) { long i = a + q * seg + j; if (i < a + len) out[k++] = in[i]; }
        }
      } else {
        for (long g = 0; g < nfs; g += window) {
          long ge = g + window < nfs ? g + window : nfs, maxlen = 0;
          for (long f = g; f < ge; f++) if (fstart[f + 1] - fstart[f] > maxlen) maxlen = fstart[f + 1] - fstart[f];
          for (long j = 0; j < maxlen; j++) for (long f = g; f < ge; f++) if (fstart[f] + j < fstart[f + 1]) out[k++] = in[fstart[f] + j];
        }
      }
      w[r].idx = out;
    }
  }
  if (shuffle) {  // spread each reactor's in-flight reads across many files
    srand(42);
    for (int r = 0; r < reactors; r++) for (long i = w[r].n - 1; i > 0; i--) { long j = rand() % (i + 1); long t = w[r].idx[i]; w[r].idx[i] = w[r].idx[j]; w[r].idx[j] = t; }
  }
  for (int ps = 0; ps < passes; ps++) {
    atomic_store(&done_bytes, 0);
    double t = now(), s = cpu(1), u = cpu(0);
    pthread_t* th = calloc(reactors, sizeof *th);
    for (int r = 0; r < reactors; r++) pthread_create(&th[r], NULL, reactor, &w[r]);
    for (int r = 0; r < reactors; r++) pthread_join(th[r], NULL);
    double dt = now() - t;
    printf("pass %d: %.2f GB in %.2fs = %.2f GB/s  sys %.1fs user %.1fs  errors %ld short %ld\n", ps, done_bytes / 1e9, dt,
           done_bytes / 1e9 / dt, cpu(1) - s, cpu(0) - u, (long)fallbacks, (long)short_reads);
    fflush(stdout);
  }
  return 0;
}

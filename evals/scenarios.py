"""
Fixture scenarios: the worlds the agent gets evaluated in.

An eval is only as trustworthy as the ground it stands on. If the suite ran
against ./logs — the shared sample fixture — every case would be coupled to
whatever the last person regenerated, and a "regression" could just as easily
be a changed log file as a changed agent. So each scenario here owns two
things that must stay in lockstep:

  * a deterministic generator that writes its own log files into a fresh
    directory (usually a tmpdir, one per run), and
  * the GroundTruth that says what a correct investigation of those files
    concludes.

Determinism is the whole point. Every line is a pure function of a fixed
anchor timestamp (2026-08-24T10:00:00) and a loop index — no clock, no
randomness, not even a seeded one. Regenerate a scenario a thousand times and
you get byte-identical files, which means a failing case is always the agent's
fault and never the fixture's.

Five scenarios, chosen to probe different failure modes of an investigator:

  cascading_failure - can it follow a chain across four files back to a config
                      change that is itself logged as a routine INFO line?
  disk_full         - can it connect a resource curve to the errors it causes?
  memory_leak       - can it read a trend (RSS climbing) rather than a single
                      dramatic line (the OOM kill), and see the restart loop?
  cert_expiry       - can it find a cause that is a *time* rather than a state?
  red_herring       - can it resist the loudest signal? The real cause is a
                      handful of quiet WARN lines about a saturated thread
                      pool; a harmless cache-miss spike screams in ERROR the
                      whole time. An agent that ranks by volume gets it wrong,
                      which is exactly what the forbidden keyword scores.

Timestamp forms, because two live in the codebase and mixing them up is the
easiest bug to write here: log *lines* use the ISO 'T' separator, matching
scripts/generate_sample_logs.py, while GroundTruth.culprit_timestamp uses the
space-separated form, because that is what the `timeline` tool's `around`
argument requires and what `timeline` echoes back in its output. Use
iso_form() when you need the log-line spelling.
"""

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Dict, List

from .case import GroundTruth


# Every scenario starts here. Culprit events land a few minutes in, so there is
# always a "before" for the agent to compare against — a fixture whose very
# first line is the root cause tests nothing.
ANCHOR = datetime(2026, 8, 24, 10, 0, 0)


def _ts(offset_s: int) -> str:
    """Log-line timestamp: ISO with a 'T', `offset_s` seconds after the anchor."""
    # strftime rather than .isoformat() so a fractional offset can never leak
    # microseconds into a line and break TIMESTAMP_RE in logtools.
    return (ANCHOR + timedelta(seconds=offset_s)).strftime("%Y-%m-%dT%H:%M:%S")


def _at(offset_s: int) -> str:
    """Ground-truth timestamp: space-separated, the form `timeline` accepts."""
    return (ANCHOR + timedelta(seconds=offset_s)).strftime("%Y-%m-%d %H:%M:%S")


def iso_form(timestamp: str) -> str:
    """Convert a space-separated ground-truth timestamp to the log-line form."""
    return timestamp.replace(" ", "T")


def _write_log(dest_dir: str, name: str, lines: List[str]) -> None:
    """Write one log file, header comment included."""
    # The leading '#' line has no timestamp on purpose: the real sample logs
    # have one too, and a tool that quietly chokes on an unparseable line
    # should fail here rather than in production.
    path = os.path.join(dest_dir, name)
    with open(path, "w") as f:
        f.write(f"# {name}\n")
        for line in lines:
            f.write(line + "\n")


# --------------------------------------------------------------------------
# 1. cascading_failure
# --------------------------------------------------------------------------

# 10:03:00 — a deploy raises DB_POOL_SIZE past what the database allows. It is
# logged at INFO, which is the trap: the agent must correlate it with the
# ERROR storm two files away rather than grep for severity.
_CASCADE_DEPLOY_S = 180


def _gen_cascading_failure(dest_dir: str) -> None:
    deploy: List[str] = []
    for i in range(180):
        t = i * 2
        if t == _CASCADE_DEPLOY_S:
            # The three lines get three distinct timestamps so that
            # culprit_timestamp names the config change and nothing else - a
            # grader asserting on that timestamp should not also match the
            # innocuous "starting" line.
            deploy.append(f"{_ts(t - 2)} INFO Deploy 2.3.0 starting (commit 8f21ac, 12 instances)")
            deploy.append(f"{_ts(t)} INFO Config change: DB_POOL_SIZE increased from 10 to 100")
            deploy.append(f"{_ts(t + 2)} INFO Deploy 2.3.0 rollout complete on 12/12 instances")
        elif i == 170:
            deploy.append(f"{_ts(t)} WARN Post-deploy error rate above SLO, 2.3.0 flagged as rollback candidate")
        elif i % 15 == 0:
            deploy.append(f"{_ts(t)} INFO Deploy agent heartbeat, no pending releases")
        elif t < _CASCADE_DEPLOY_S:
            deploy.append(f"{_ts(t)} DEBUG Release queue empty, watching branch main")
        else:
            deploy.append(f"{_ts(t)} DEBUG Post-deploy watcher: 12/12 instances reporting in")
    _write_log(dest_dir, "deploy.log", deploy)

    # The database is the first thing to feel it: 12 instances x 100 connections
    # against a server configured for 50.
    db: List[str] = []
    for i in range(240):
        t = i * 2
        if t < 184:
            db.append(f"{_ts(t)} INFO connection accepted from svc-checkout (pool 8/10 in use)")
            if i % 20 == 0:
                db.append(f"{_ts(t)} DEBUG server config max_connections=50, active=9, idle=41")
        elif i % 3 == 0:
            db.append(f"{_ts(t)} ERROR FATAL: sorry, too many clients already (max_connections=50, pool requested 100)")
        elif i % 3 == 1:
            db.append(f"{_ts(t)} WARN connection slots exhausted: 50/50 in use, 63 waiters queued")
        else:
            db.append(f"{_ts(t)} INFO connection released after 5021ms")
    _write_log(dest_dir, "database.log", db)

    service: List[str] = []
    for i in range(220):
        t = i * 2
        if t < 200:
            service.append(f"{_ts(t)} INFO request completed in 45ms (GET /api/cart)")
            if i % 25 == 0:
                service.append(f"{_ts(t)} INFO health check passed, db pool healthy")
        elif i % 4 == 0:
            service.append(f"{_ts(t)} ERROR checkout handler failed: could not acquire DB connection within 5000ms")
        elif i % 4 == 1:
            service.append(f"{_ts(t)} WARN DB query timeout after 5000ms, retrying (1/3)")
        else:
            service.append(f"{_ts(t)} INFO request completed in 5041ms (GET /api/cart)")
    _write_log(dest_dir, "service.log", service)

    gateway: List[str] = []
    for i in range(200):
        t = i * 2
        if t < 210:
            gateway.append(f"{_ts(t)} INFO 200 GET /api/home upstream=svc-checkout 41ms")
        elif i % 3 == 0:
            gateway.append(f"{_ts(t)} ERROR 502 Bad Gateway: upstream svc-checkout timed out after 5000ms")
        elif i % 3 == 1:
            gateway.append(f"{_ts(t)} WARN upstream svc-checkout slow to respond (p99 5100ms)")
        else:
            gateway.append(f"{_ts(t)} INFO 200 GET /api/home upstream=svc-checkout 4980ms")
    _write_log(dest_dir, "gateway.log", gateway)


# --------------------------------------------------------------------------
# 2. disk_full
# --------------------------------------------------------------------------

# 10:04:00 — the volume hits 100%. Everything downstream is ENOSPC.
_DISK_FULL_S = 240


def _gen_disk_full(dest_dir: str) -> None:
    storage: List[str] = []
    for i in range(220):
        t = i * 2
        if t < _DISK_FULL_S:
            # A clean monotonic climb, 70% -> 99%. The curve is the evidence;
            # an agent that only greps ERROR will miss why the disk filled.
            pct = 70 + (i * 29) // 120
            free_mb = 512000 - pct * 5120
            if i % 4 == 0:
                storage.append(f"{_ts(t)} INFO disk usage on /var/lib/data {pct}% ({free_mb} MB free of 512000 MB)")
            elif i % 4 == 2 and pct >= 90:
                storage.append(f"{_ts(t)} WARN disk usage on /var/lib/data {pct}% crossed the 90% alert threshold")
            else:
                storage.append(f"{_ts(t)} DEBUG allocator extended segment wal-{4000 + i} by 64 MB")
        elif t == _DISK_FULL_S:
            storage.append(f"{_ts(t)} ERROR disk /var/lib/data reached 100% usage (0 MB free of 512000 MB) - all writes will fail with ENOSPC")
        elif i % 3 == 0:
            storage.append(f"{_ts(t)} ERROR allocator cannot extend segment wal-{4000 + i}: ENOSPC, disk /var/lib/data is full")
        else:
            storage.append(f"{_ts(t)} WARN disk /var/lib/data still at 100%, retention sweep freed 0 MB")
    _write_log(dest_dir, "storage.log", storage)

    writer: List[str] = []
    for i in range(200):
        t = i * 2
        if t < 244:
            writer.append(f"{_ts(t)} INFO appended 512 records to segment wal-{4000 + i} in 12ms")
        elif i % 2 == 0:
            writer.append(f"{_ts(t)} ERROR write failed: [Errno 28] No space left on device (ENOSPC) appending to wal-{4000 + i}")
        else:
            writer.append(f"{_ts(t)} WARN ingest queue depth {1000 + i * 12}, backpressure applied to producers")
    _write_log(dest_dir, "checkpoint_writer.log", writer)

    checkpoint: List[str] = []
    for i in range(170):
        t = i * 3
        if t < 250:
            checkpoint.append(f"{_ts(t)} INFO checkpoint {900 + i} flushed, 128 MB written in 840ms")
        elif i % 3 == 0:
            checkpoint.append(f"{_ts(t)} ERROR checkpoint {900 + i} aborted: ENOSPC while flushing snapshot to /var/lib/data")
        else:
            checkpoint.append(f"{_ts(t)} WARN checkpoint lag now {i - 83} intervals behind, recovery window shrinking")
    _write_log(dest_dir, "checkpoint.log", checkpoint)


# --------------------------------------------------------------------------
# 3. memory_leak
# --------------------------------------------------------------------------

# 10:05:00 — worker-3 admits its RSS has been climbing since start. The kernel
# reaps it twelve seconds later, and the scheduler restarts it into the same
# leak, which is why the symptom looks periodic rather than terminal.
_LEAK_CULPRIT_S = 300


def _gen_memory_leak(dest_dir: str) -> None:
    worker: List[str] = []
    for i in range(240):
        t = i * 2
        if t < _LEAK_CULPRIT_S:
            rss = 2500 + (i * 1482) // 150
            if i % 5 == 0:
                worker.append(f"{_ts(t)} INFO worker-3 heap sample: RSS {rss} MB, session registry holds {1200 + i * 37} entries")
            elif i % 5 == 3 and rss > 3500:
                worker.append(f"{_ts(t)} WARN worker-3 RSS {rss} MB above soft limit 3500 MB, GC reclaimed only 12 MB")
            else:
                worker.append(f"{_ts(t)} DEBUG worker-3 processed batch of 64 jobs in 210ms")
        elif t == _LEAK_CULPRIT_S:
            worker.append(f"{_ts(t)} ERROR worker-3 memory growth unbounded: RSS 3982 MB of 4096 MB cgroup limit, +1482 MB since 10:00:00, session registry never evicted")
        elif t < 310:
            worker.append(f"{_ts(t)} ERROR worker-3 allocation failed: cannot allocate 64 MB arena, RSS 4061 MB")
        elif t == 310:
            worker.append(f"{_ts(t)} ERROR worker-3 terminated by SIGKILL (OOM killer), 64 in-flight jobs lost")
        else:
            # Same leak, new process: RSS starts low and climbs again.
            rss = 700 + ((i - 156) * 9)
            if i % 5 == 0:
                worker.append(f"{_ts(t)} INFO worker-3 restarted (pid {5000 + i}), RSS {rss} MB, session registry holds {90 + (i - 156) * 37} entries")
            else:
                worker.append(f"{_ts(t)} DEBUG worker-3 processed batch of 64 jobs in 205ms")
    _write_log(dest_dir, "worker.log", worker)

    kernel: List[str] = []
    for i in range(160):
        t = i * 3
        if t < 312:
            kernel.append(f"{_ts(t)} INFO cgroup /system/worker-3 memory.current {2600 + i * 9} MB of 4096 MB")
        elif t == 312:
            kernel.append(f"{_ts(t)} ERROR Out of memory: Killed process 4821 (worker-3) total-vm:5242880kB anon-rss:4159488kB - OOM killer invoked by cgroup /system/worker-3")
        elif i % 4 == 0:
            kernel.append(f"{_ts(t)} WARN cgroup /system/worker-3 memory pressure high, {i - 104} reclaim stalls in last interval")
        else:
            kernel.append(f"{_ts(t)} INFO cgroup /system/worker-3 memory.current {700 + (i - 104) * 27} MB of 4096 MB")
    _write_log(dest_dir, "kernel.log", kernel)

    scheduler: List[str] = []
    restart = 0
    for i in range(180):
        t = i * 3
        if t < 315:
            scheduler.append(f"{_ts(t)} INFO worker-3 healthy, 64 jobs dispatched, queue depth 12")
        elif i % 20 == 0:
            restart += 1
            scheduler.append(f"{_ts(t)} ERROR worker-3 exited with signal 9 (SIGKILL), restarting (attempt {restart})")
        elif i % 20 == 1:
            scheduler.append(f"{_ts(t)} WARN worker-3 restart loop detected: {restart} kills since 10:05:10, backoff now {restart * 5}s")
        else:
            scheduler.append(f"{_ts(t)} INFO worker-3 accepting jobs again, queue depth {12 + i}")
    _write_log(dest_dir, "scheduler.log", scheduler)


# --------------------------------------------------------------------------
# 4. cert_expiry
# --------------------------------------------------------------------------

# 10:02:00 — notAfter. The cause is a moment, not a state: nothing was
# deployed, nothing ran out, the clock simply passed a number in a file.
_CERT_EXPIRY_S = 120


def _gen_cert_expiry(dest_dir: str) -> None:
    tls: List[str] = []
    for i in range(230):
        t = i * 2
        if i == 30:
            tls.append(f"{_ts(t)} WARN certificate CN=api.internal expires in 60s (notAfter=2026-08-24T10:02:00)")
        elif t < _CERT_EXPIRY_S:
            tls.append(f"{_ts(t)} INFO TLS handshake with api.internal completed in 18ms (TLSv1.3, CN=api.internal)")
        elif t == _CERT_EXPIRY_S:
            tls.append(f"{_ts(t)} ERROR TLS handshake with api.internal failed: certificate expired at 2026-08-24T10:02:00 (CN=api.internal, notAfter=2026-08-24T10:02:00)")
        elif i % 3 == 0:
            tls.append(f"{_ts(t)} ERROR TLS handshake with api.internal failed: certificate has expired (x509 error 10, CN=api.internal)")
        elif i % 3 == 1:
            tls.append(f"{_ts(t)} WARN verify callback rejected peer chain for api.internal, depth 0, reason certificate expired")
        else:
            tls.append(f"{_ts(t)} INFO retrying handshake with api.internal (attempt {i % 5 + 1})")
    _write_log(dest_dir, "tls.log", tls)

    upstream: List[str] = []
    for i in range(170):
        t = i * 3
        if t < 126:
            upstream.append(f"{_ts(t)} INFO api.internal served 200 in 31ms, connection reused")
        elif i % 3 == 0:
            upstream.append(f"{_ts(t)} ERROR connection to api.internal closed during TLS handshake, no bytes exchanged")
        else:
            upstream.append(f"{_ts(t)} WARN upstream pool for api.internal has 0 usable connections, all handshakes rejected")
    _write_log(dest_dir, "upstream.log", upstream)

    gateway: List[str] = []
    for i in range(200):
        t = i * 2
        if t < 126:
            gateway.append(f"{_ts(t)} INFO 200 GET /api/orders upstream=api.internal 33ms")
        elif i % 3 == 0:
            gateway.append(f"{_ts(t)} ERROR 503 Service Unavailable: upstream api.internal unreachable (TLS)")
        else:
            gateway.append(f"{_ts(t)} WARN upstream api.internal marked unhealthy, 0/4 endpoints in rotation")
    _write_log(dest_dir, "gateway.log", gateway)


# --------------------------------------------------------------------------
# 5. red_herring
# --------------------------------------------------------------------------

# 10:03:00 — the request-worker pool saturates. It says so quietly, at WARN,
# in one file. Meanwhile the decoy in cache.log has been screaming in ERROR
# since 10:01:40 with much bigger numbers, and it is completely harmless: the
# layer is read-through and non-blocking, which cache.log itself states.
#
# The volume asymmetry is deliberate and load-bearing. Keep the decoy louder
# than the cause if you edit this, or the case stops testing anything.
_HERRING_CULPRIT_S = 180


def _gen_red_herring(dest_dir: str) -> None:
    # The real cause. Note there is no occurrence of the decoy's vocabulary in
    # this file or in gateway.log — a correct answer has no reason to reach for
    # it, which is what makes the forbidden keyword a fair test.
    app: List[str] = []
    for i in range(260):
        t = i * 2
        if t == _HERRING_CULPRIT_S:
            app.append(f"{_ts(t)} WARN thread pool 'request-worker' exhausted: 32/32 threads busy, 0 idle, queue depth 512, oldest task waiting 41s")
        elif t < _HERRING_CULPRIT_S:
            if i % 10 == 0:
                app.append(f"{_ts(t)} INFO thread pool 'request-worker' 11/32 busy, queue depth 3, mean service time 44ms")
            else:
                app.append(f"{_ts(t)} DEBUG handled GET /api/orders in {40 + i % 9}ms on request-worker-{i % 32}")
        elif i % 25 == 0:
            app.append(f"{_ts(t)} ERROR RejectedExecutionException: request-worker pool saturated, task dropped after 512-deep queue")
        elif i % 10 == 0:
            app.append(f"{_ts(t)} WARN thread pool 'request-worker' 32/32 busy, queue depth {512 + i}, oldest task waiting {41 + i}s")
        else:
            app.append(f"{_ts(t)} DEBUG handled GET /api/orders in {4100 + i}ms on request-worker-{i % 32}")
    _write_log(dest_dir, "app.log", app)

    # The decoy: earlier, louder, and mostly ERROR. Every number is bigger than
    # anything in app.log.
    cache: List[str] = []
    for i in range(320):
        t = i * 2
        if i == 20:
            cache.append(f"{_ts(t)} INFO layer is read-through and non-blocking; misses fall through to the local memo table with no request impact")
        elif t < 100:
            cache.append(f"{_ts(t)} INFO hit rate 97.9%, {48000 + i * 11} lookups in last 10s, evictions 12")
        elif i % 2 == 0:
            cache.append(f"{_ts(t)} ERROR CRITICAL miss rate {99.1 + (i % 9) * 0.1:.1f}% (baseline 2.1%), {148000 + i * 311} misses in last 10s")
        elif i % 4 == 1:
            cache.append(f"{_ts(t)} ERROR keyspace churn storm: {92000 + i * 517} evictions in last 10s, working set {14 + i // 40} GB over budget")
        else:
            cache.append(f"{_ts(t)} WARN shard rebalance {i % 7 + 1}/7 in progress, {31000 + i * 97} keys migrated, no client impact")
    _write_log(dest_dir, "cache.log", cache)

    gateway: List[str] = []
    for i in range(200):
        t = i * 2
        if t < 200:
            gateway.append(f"{_ts(t)} INFO 200 GET /api/orders upstream=app 46ms")
        elif i % 3 == 0:
            gateway.append(f"{_ts(t)} ERROR 500 Internal Server Error: upstream app rejected request (queue full)")
        else:
            gateway.append(f"{_ts(t)} WARN upstream app p99 latency {4100 + i * 7}ms, above 500ms objective")
    _write_log(dest_dir, "gateway.log", gateway)


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------


@dataclass
class Scenario:
    """
    A fixture world plus its answer key.

    `generate` takes a destination directory that already exists and writes the
    scenario's .log files into it. It never touches anything else, so a caller
    can point two scenarios at the same tmpdir if it wants a messier world —
    though every case in the suite gives each scenario its own directory.
    """

    name: str
    description: str
    ground_truth: GroundTruth
    generate: Callable[[str], None]


# expected_tools is deliberately conservative: search_logs plus timeline is the
# minimum evidence that an agent correlated files rather than pattern-matched
# the prompt. Demanding more (log_stats, read_log) would score investigative
# *style* instead of correctness, and there is more than one good style.
SCENARIOS: Dict[str, Scenario] = {
    "cascading_failure": Scenario(
        name="cascading_failure",
        description=(
            "A deploy raises DB_POOL_SIZE from 10 to 100 across 12 instances "
            "against a database configured for max_connections=50."
        ),
        ground_truth=GroundTruth(
            root_cause_keywords=["DB_POOL_SIZE", "deploy"],
            forbidden_keywords=[],
            expected_tools=["list_logs", "search_logs", "timeline"],
            culprit_file="deploy.log",
            culprit_timestamp=_at(_CASCADE_DEPLOY_S),
            description=(
                "At 10:03:00 the 2.3.0 deploy changed DB_POOL_SIZE from 10 to 100. "
                "Twelve instances times 100 connections overwhelmed a database with "
                "max_connections=50, so the database began refusing connections with "
                "'too many clients already'. The service then timed out waiting for DB "
                "connections, and the gateway turned those timeouts into 502s. The fix "
                "is to roll back the pool size or raise max_connections."
            ),
        ),
        generate=_gen_cascading_failure,
    ),
    "disk_full": Scenario(
        name="disk_full",
        description=(
            "/var/lib/data fills from 70% to 100%; writes and checkpoints then "
            "fail with ENOSPC."
        ),
        ground_truth=GroundTruth(
            root_cause_keywords=["disk", "ENOSPC"],
            forbidden_keywords=[],
            expected_tools=["search_logs", "timeline"],
            culprit_file="storage.log",
            culprit_timestamp=_at(_DISK_FULL_S),
            description=(
                "Disk usage on /var/lib/data climbed steadily from 70% and hit 100% at "
                "10:04:00 with 0 MB free. From that point every write failed with "
                "ENOSPC ([Errno 28] No space left on device), the ingest queue backed "
                "up under backpressure, and checkpoints aborted mid-flush, so the "
                "recovery window kept shrinking. The failure is disk exhaustion, not "
                "an application bug."
            ),
        ),
        generate=_gen_disk_full,
    ),
    "memory_leak": Scenario(
        name="memory_leak",
        description=(
            "worker-3's RSS grows unbounded until the OOM killer reaps it; the "
            "scheduler restarts it into the same leak."
        ),
        ground_truth=GroundTruth(
            root_cause_keywords=["memory", "OOM"],
            forbidden_keywords=[],
            expected_tools=["search_logs", "timeline"],
            culprit_file="worker.log",
            culprit_timestamp=_at(_LEAK_CULPRIT_S),
            description=(
                "worker-3 leaked memory: its RSS climbed steadily from 2500 MB to "
                "3982 MB of a 4096 MB cgroup limit because entries in the session "
                "registry were never evicted. Allocations began failing, and at "
                "10:05:12 the kernel OOM killer killed pid 4821. The scheduler "
                "restarted the worker, whose RSS immediately began climbing again, "
                "producing a restart loop. The kill is the symptom; the unbounded "
                "session registry is the cause."
            ),
        ),
        generate=_gen_memory_leak,
    ),
    "cert_expiry": Scenario(
        name="cert_expiry",
        description=(
            "The TLS certificate for CN=api.internal expires mid-window; "
            "handshakes fail and the gateway returns 503."
        ),
        ground_truth=GroundTruth(
            root_cause_keywords=["certificate", "expired"],
            forbidden_keywords=[],
            expected_tools=["search_logs", "timeline"],
            culprit_file="tls.log",
            culprit_timestamp=_at(_CERT_EXPIRY_S),
            description=(
                "The TLS certificate for CN=api.internal reached its notAfter of "
                "2026-08-24T10:02:00 and expired. A warning one minute earlier "
                "announced the expiry. Afterwards every handshake with api.internal "
                "failed x509 verification with 'certificate has expired', the upstream "
                "pool dropped to zero usable connections, and the gateway returned 503 "
                "Service Unavailable. Nothing was deployed and no resource ran out; "
                "the certificate simply expired and must be renewed."
            ),
        ),
        generate=_gen_cert_expiry,
    ),
    "red_herring": Scenario(
        name="red_herring",
        description=(
            "Thread-pool exhaustion in app.log is the real cause; a loud but "
            "harmless cache-miss spike in cache.log is the decoy."
        ),
        ground_truth=GroundTruth(
            root_cause_keywords=["thread pool"],
            # Naming the decoy is an automatic fail. This is the only scenario
            # that scores precision rather than recall.
            forbidden_keywords=["cache"],
            expected_tools=["search_logs", "timeline"],
            culprit_file="app.log",
            culprit_timestamp=_at(_HERRING_CULPRIT_S),
            description=(
                "At 10:03:00 the 'request-worker' thread pool saturated: 32 of 32 "
                "threads busy, zero idle, a 512-deep queue, and tasks waiting 41 "
                "seconds. Requests were then rejected with RejectedExecutionException "
                "and the gateway returned 500s with p99 latency in the seconds. The "
                "concurrent spike in cache miss rate is a decoy: that layer is "
                "read-through and non-blocking, so misses fall through to a local memo "
                "table with no request impact. The fix is the saturated thread pool."
            ),
        ),
        generate=_gen_red_herring,
    ),
}


def get_scenario(name: str) -> Scenario:
    """Look up a scenario by name, or raise KeyError naming the valid ones."""
    try:
        return SCENARIOS[name]
    except KeyError:
        valid = ", ".join(sorted(SCENARIOS))
        # Re-raise rather than return None: a typo'd scenario name should stop
        # the suite loudly, not silently score a case against an empty dir.
        raise KeyError(f"Unknown scenario {name!r}. Valid scenarios: {valid}") from None


def materialize(name: str, dest_dir: str) -> str:
    """Write a scenario's log files into `dest_dir`, creating it if needed."""
    scenario = get_scenario(name)
    os.makedirs(dest_dir, exist_ok=True)
    scenario.generate(dest_dir)
    return dest_dir


def list_scenarios() -> List[Scenario]:
    """All scenarios in registry order (which is the order of the pyramid)."""
    return list(SCENARIOS.values())

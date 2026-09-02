# Operations guide

## Frontend cutover operations

The public router exposes one origin while keeping frontend page readiness and FastAPI service
readiness independent. A frontend outage must leave direct API operations intact and produces a
bounded router error for pages; a backend outage leaves static/frontend responses available but API
requests fail safely. Drain and replace the router, frontend, API, and workers according to their
own bounds, and never treat frontend readiness as proof that storage or workers are healthy.

## Health, metrics, and logs

`/health/live` proves that the API process can answer. `/health/ready` checks the dependencies needed
by its profile and fails when standalone's embedded worker is unhealthy. Route traffic only to
ready replicas. Do not use liveness to decide whether storage is safe.

The API exposes Prometheus text at `/metrics`. Each distributed external worker exposes its own
process-local metrics listener, configured by `MARKWEAVE_WORKER_METRICS_*`; it is not an
in-process aggregate. Scrape API and worker targets separately and aggregate in the monitoring
system. The default worker bind address is `127.0.0.1`, so a pod scraper needs an intentional bind
and network-policy decision. Keep the observation limit no greater than the connection limit.

Logs are structured JSON and correlate request, user, job, template, version, and audit identifiers.
They intentionally omit credentials and document content. Alerts should cover readiness failure,
submission rejection, queue depth and age, lease recovery, terminal failures, cleanup failures,
ClamAV unavailability, storage errors, and worker memory or ephemeral-space exhaustion. Metric names
and labels are documented in [observability](observability.md).

## Queue and worker operations

Admission enforces a global queue capacity and a per-user active-job limit. A worker claims a durable
job lease, renews it with heartbeats, and recovers expired work deterministically. The heartbeat
interval must be shorter than the lease. Maximum job duration, memory budget, ephemeral-storage
budget, engine deadlines, and all input/output limits are independent safety ceilings.

Standalone has exactly one application replica running `embedded-worker`. Distributed runs one or
more `api` replicas and separately scalable `external-worker` processes. Adding workers changes
throughput, not the global or per-user admission contracts. Observe queue age and resource pressure
before scaling; do not hide a persistent engine, scanner, database, or object-store failure by
adding workers.

## Safe drain and rollout

There is no administrative drain endpoint. For a planned shutdown:

1. stop new submissions by removing API replicas from service or scaling them down;
2. stop workers with `SIGTERM` and allow their graceful path to finish or release work;
3. watch queue, running-job, lease-recovery, and failure metrics until the bounded drain condition
   is met;
4. account for the configured job maximum duration and lease interval before declaring abandoned
   work recoverable;
5. take a consistent backup only after the selected quiescence point.

In standalone, the API and embedded worker share one process, so remove ingress first and then stop
the single replica. In distributed mode, stop admission before workers. A rollout that intentionally
leaves queued work may start replacement workers after the old workers exit; expired leases are
recovered by the durable state machine. Do not run two standalone replicas against the same data
directory.

## Retention and maintenance

Periodic cleanup applies configured result, template-version, and audit retention. Minimum retained
template versions constrain pruning. Cleanup is bounded by an interval and batch size; monitor it
rather than relying on unbounded deletion. Retention is not a backup policy. Archive or legal-hold
requirements outside the product contract must be implemented in the platform's database and
object-store backup policy.

For backup, restore, and quarterly recovery proof, follow [recovery.md](recovery.md). For required
environment settings, follow [configuration.md](configuration.md); for their runtime interaction,
follow [resource-policy.md](resource-policy.md).

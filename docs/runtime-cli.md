# Runtime command-line operations

`markweave serve` loads exactly one coherent storage profile. In `standalone` it
runs the HTTP service with its embedded worker; in `distributed` it runs the HTTP
service without an embedded worker. `markweave worker` requires the distributed
profile and runs the existing external-worker assembly until `SIGINT` or
`SIGTERM` requests a clean stop. Both commands retain the configured host, port,
health endpoints, worker metrics, and application lifecycle.

When the complete reverse-execution group is configured, both worker modes add the authenticated
reverse supervisor to the same serial loop. The loop tries forward work first, alternates after
each successful claim, and immediately falls back when the preferred family is empty. Expected
broker or persistence failures back off only the affected family, so an unavailable reverse broker
cannot starve forward conversions. The database enforces the configured global reverse-running cap
before a claim, independently of the shared mixed-family queue capacity. Before every reverse
claim or recovery pass, the worker requires an authenticated positive broker `READY` response and
completes durable reconciliation.

`markweave doctor` is non-mutating. It validates configuration coherence and
performs bounded checks for document-engine executables, the font manifest and
Fontconfig, the selected scanner boundary, metadata and object storage,
permissions, a non-root identity, and writable temporary storage. It reports
only fixed check names and the selected profile; URLs, paths, credentials, and
backend error details are never emitted. Use `--timeout SECONDS` to replace the
five-second total diagnostic budget.

`markweave migrate` applies the Alembic upgrade for exactly the configured
profile. PostgreSQL uses the existing transaction-scoped advisory lock and
SQLite acquires an immediate write reservation, so concurrent invocations are
serialized. Repeated execution is idempotent. Output contains only the profile,
revision, and whether the schema changed. A mixed or incomplete profile fails
before a database is selected. `--timeout SECONDS` bounds database acquisition
and statements where the backend supports it.

`python -m markweave.runtime` remains a package-internal compatibility path for
existing container entrypoints until T38 migrates them to the supported CLI.

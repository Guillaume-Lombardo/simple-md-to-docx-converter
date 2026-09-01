# Backup and recovery

The recovery targets are profile-specific:

| Profile | Maximum RPO | Maximum RTO |
| --- | ---: | ---: |
| Standalone | 24 hours | 4 hours |
| Distributed | 1 hour | 2 hours |

RPO is measured from the recovered consistent backup point; RTO runs from the start of the restore
exercise through successful readiness verification. A backup that has not been restored and checked
does not prove either target.

## Consistent backup sets

For standalone, `markweave backup` uses SQLite's online backup API and verifies that the complete
stable object tree did not change while it was copied. It publishes the database and objects as one
owner-only, content-addressed set. Copying a live database independently from its objects can
restore references that do not match stored content.

For distributed, stop new submissions and reach a named worker-quiescence or provider-consistency
point before running the command. The typed PostgreSQL adapter takes one repeatable-read logical
snapshot; the AWS S3-compatible adapter requires identical inventories before and after its copy.
The manifest binds the PostgreSQL snapshot identity, object inventory identity, and the supplied
quiescence proof. Use the providers' encryption, immutability, and retention controls around the
resulting set.

Record the backup identifier, UTC creation time, profile, database checkpoint/snapshot identity,
object-store snapshot or version identity, application image digest, configuration revision, and
encrypted key-management references. Never put credentials, document content, or restored output in
the evidence report.

## Production commands

All paths are absolute. PostgreSQL URLs and optional static S3 credentials are read from explicitly
named environment variables so they do not enter process arguments or reports.

```bash
markweave --non-interactive --timeout 14400 backup \
  --profile standalone \
  --data-directory /data \
  --destination /recovery/sets

markweave --non-interactive --timeout 7200 backup \
  --profile distributed \
  --destination /recovery/sets \
  --database-url-environment MARKWEAVE_DISTRIBUTED_DATABASE_URL \
  --s3-bucket markweave-production \
  --s3-region us-east-1 \
  --consistency-proof workers-drained-2026q3
```

Restore accepts the content-addressed set directory as `--source`. A standalone destination must
not exist. A distributed database must be an isolated empty schema/database and the bucket must be
empty. The command rejects the source database or bucket as a target, verifies the whole set before
mutation, restores stable object keys without rewriting them, migrates the restored database, and
checks every retained object reference. It never changes routing or starts production traffic.

```bash
markweave --non-interactive --timeout 14400 restore \
  --profile standalone \
  --source /recovery/sets/BACKUP_SHA256 \
  --data-directory /isolated/restore-data \
  --offline-proof application-stopped-2026q3 \
  --yes
```

For distributed restore, use `--database-url-environment` and the `--s3-*` options for distinct
isolated targets. Supply static credentials, when needed, with
`--s3-access-key-environment` and `--s3-secret-key-environment`. A failed database restore removes
objects written to the empty target bucket; database rows are inserted transactionally.

## Restore exercise

Restore into an isolated environment with the same profile and an immutable application image.
Validate storage integrity, start the correct runtime mode, and require `/health/ready` to succeed.
Then verify authenticated template resolution and a representative conversion without exposing its
content in logs or reports.

Run the production restore command with `--report-directory` and `--evidence-id`. This measures the
approved profile RPO/RTO and exclusively retains a content-free owner-only report. The compatibility
wrapper accepts only structured arguments for that same command; it cannot execute an
operator-supplied program or shell string:

```bash
uv run python scripts/run_restore_exercise.py \
  --timeout 14400 -- \
  --profile standalone \
  --source /recovery/sets/BACKUP_SHA256 \
  --data-directory /isolated/restore-data \
  --offline-proof quarterly-exercise-2026q3 \
  --report-directory /recovery/reports \
  --evidence-id readiness-2026q3 \
  --yes
```

Use `--profile distributed` for its tighter target and supply its isolated PostgreSQL/S3 options.
Treat a timeout, failed integrity or identity proof, excessive RPO/RTO, or pre-existing report as
failure.

`scripts/e2e/s3_backup.py` is destructive test support, not a production backup utility. Do not run
it against a production bucket or cite it as recovery evidence.

## Return to service

Before reopening ingress, verify readiness, schema compatibility, object retrieval, template
fallback, the role-specific idle-session policy and revision, authentication, queue state, and the
exact restored image/configuration identity. A missing policy row intentionally resolves to the
30-minute standard-user and 15-minute administrator defaults; a present row and all immutable
policy audit evidence must survive restore unchanged. In
distributed mode, start API and workers in a controlled order and confirm worker-local metrics are
being scraped. Preserve the exercise report without alteration, together with platform backup logs,
according to the approved evidence-retention policy. Exercise each production profile at least
quarterly and after a material change to storage, deployment, encryption, or restore tooling.

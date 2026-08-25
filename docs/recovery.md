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

For standalone, drain the sole embedded-worker replica and back up the SQLite database and atomic
object tree under `/data` as one consistent set. Copying a live database independently from its
objects can restore references that do not match stored content.

For distributed, stop new submissions and reach the chosen worker quiescence point before taking a
coordinated PostgreSQL and S3-compatible snapshot. Database rows and immutable objects must represent
one recovery point. Use the database and object-store vendor's production backup, versioning,
integrity, encryption, and retention controls; the application does not turn two unrelated backups
into a transactionally consistent set.

Record the backup identifier, UTC creation time, profile, database checkpoint/snapshot identity,
object-store snapshot or version identity, application image digest, configuration revision, and
encrypted key-management references. Never put credentials, document content, or restored output in
the evidence report.

## Restore exercise

Restore into an isolated environment with the same profile and an immutable application image.
Validate storage integrity, start the correct runtime mode, and require `/health/ready` to succeed.
Then verify authenticated template resolution and a representative conversion without exposing its
content in logs or reports.

The repository supplies a content-free, fail-closed exercise wrapper:

```bash
uv run python scripts/run_restore_exercise.py \
  --profile standalone \
  --backup-id BACKUP_ID \
  --evidence-id EXERCISE_ID \
  --backup-created-at 2026-08-25T00:00:00+00:00 \
  --report-directory artifacts/recovery \
  -- operator-restore-and-readiness-command
```

Use `--profile distributed` for its tighter target. The command after `--` is operator-supplied and
must perform the real restore and readiness verification. The wrapper enforces the profile RTO,
suppresses command output, and writes an exclusive owner-only report in an owner-only directory with
durability synchronization. Treat a timeout, non-zero command, malformed timestamp, excessive RPO,
or pre-existing report as failure.

`scripts/e2e/s3_backup.py` is destructive test support, not a production backup utility. Do not run
it against a production bucket or cite it as recovery evidence.

## Return to service

Before reopening ingress, verify readiness, schema compatibility, object retrieval, template
fallback, authentication, queue state, and the exact restored image/configuration identity. In
distributed mode, start API and workers in a controlled order and confirm worker-local metrics are
being scraped. Preserve the exercise report without alteration, together with platform backup logs,
according to the approved evidence-retention policy. Exercise each production profile at least
quarterly and after a material change to storage, deployment, encryption, or restore tooling.

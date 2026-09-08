# Conversion and job CLI

The installed `markweave` client submits conversions and manages the current user's jobs only
through the authenticated `/api/v1` HTTP API. Sign in first with `markweave login`; every command
uses the selected owner-only connection profile and never opens server repositories, object stores,
or worker services.

## Submit a conversion

Submit Markdown or a ZIP archive and select DOCX, PDF, or both:

```text
markweave convert source.md --output docx
markweave convert bundle.zip --output both --idempotency-key deployment-42-document-7
```

The default output is DOCX. The client sends a canonical private upload name based only on the
`.md` or `.zip` kind; it does not print or log the local source name or document content. Successful
output includes the job identifier, durable correlation identifier, and server-provided polling
delay. Keep the correlation identifier when reporting a problem.

Omit both template options to use Pandoc's default reference document. To pin styling, provide the
visible template identity and its exact current immutable version together:

```text
markweave convert source.md \
  --template-id 11111111-1111-4111-8111-111111111111 \
  --template-version-id 22222222-2222-4222-8222-222222222222
```

An explicit idempotency key is owner-scoped and payload-sensitive. Repeating the same request with
the same key returns the original job; changing its source or parameters returns the server's
stable conflict. `--retries COUNT` retries only ambiguous network failures, accepts at most five
retries, and requires an explicit idempotency key.

Use `--profile NAME` on any remote command to select a non-default connection profile. Global
options such as `--json` and `--timeout` precede the command name.

## Inspect and control jobs

```text
markweave jobs list --offset 0 --limit 50
markweave jobs show JOB_ID
markweave --timeout 300 jobs wait JOB_ID --poll-interval 2
markweave jobs cancel JOB_ID
```

Lists are owner-scoped and bounded to 100 entries per request. `jobs wait` requires the global
positive finite `--timeout`, bounds each HTTP request by the remaining deadline, and polls no more
frequently than its positive `--poll-interval`. It succeeds only for `succeeded`; safe failed,
cancelled, expired, authorization, quota, capacity, and service errors retain their stable message
and correlation identifier.

## Download results and manifests

```text
markweave jobs download JOB_ID ./result.docx
markweave jobs manifest JOB_ID ./traceability.json
```

The destination parent must be a real directory. Downloads stream into a private temporary file,
flush it, and publish it atomically. A partial or interrupted transfer is removed. Existing paths
are refused by default; `--overwrite` may atomically replace only an existing regular file and
never follows a symlink. The result extension remains the operator's explicit destination choice.

Manifest downloads are available for successful PDF and combined jobs. DOCX-only, failed,
cancelled, unavailable, and expired jobs retain the server's safe error contract. Human output
never prints the destination or source name; JSON output reports only job, byte-count, result type,
status, and a validated correlation identifier when the response provides one.

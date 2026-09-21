# HTTP API guide

The API is served under `/api/v1`. Interactive OpenAPI documentation is available at `/docs` and
the machine-readable schema at `/openapi.json`. Health and Prometheus endpoints are outside the API
prefix: `/health/live`, `/health/ready`, and `/metrics`.

## Authentication, CSRF, and errors

`POST /api/v1/login` creates a local session. Preserve the returned Secure session cookie and CSRF
token. Send that token in the documented CSRF header for every state-changing request, including
logout. `GET /api/v1/session` returns the current principal and `POST /api/v1/logout` ends the
session. Its user representation includes `password_change_required`.

When that flag is true, successful credential verification creates a restricted session. Only
session inspection, logout, and `POST /api/v1/password` are available until the user submits
matching `password` and `confirmation` values with the session CSRF header. Success clears the
requirement, revokes that session, and requires a new login with the new password. Browser clients
use the equivalent `/change-password` page.

Browser form login is also available at `POST /login`. The service is intended for same-origin
HTTPS use. Do not disable TLS, the Secure cookie, Origin validation, or CSRF validation to make an
integration work.

Errors use the stable envelope:

```json
{"error":{"code":"stable_code","message":"human-readable explanation"}}
```

Responses include a correlation identifier. Preserve it in client diagnostics. Never infer
authorization from an HTTP status alone; clients should use both the status and stable error code.

## Conversions

`POST /api/v1/conversions` accepts multipart form data:

- `source`: the Markdown file or supported archive;
- `output`: `docx`, `pdf`, or `both`;
- `template_id` and `template_version_id`: optional as a pair. Omit both to use Pandoc's native
  reference document, or provide both for the exact visible, active template resolved by the client.

An `Idempotency-Key` header makes retries owner-scoped and payload-sensitive. Reusing a key with the
same canonical request returns the existing submission; reusing it with different input is a
conflict. A successful new submission returns `202 Accepted`, a `Location` header for the job, and
`Retry-After` guidance.
The response `template_mode` is `pandoc-default` or `versioned`; template identifiers are `null` in
default mode.

Use `GET /api/v1/conversions` for the current user's paginated list and
`GET /api/v1/conversions/{job_id}` for one job. `DELETE` on the job requests cancellation.
Completed output is available from `/result`; `/result/manifest` returns its traceability manifest.
The result download filename preserves the uploaded source stem and uses `.docx`, `.pdf`, or `.zip`
for the requested output. Jobs without persisted source metadata use `conversion-<job-id>` as the
filename stem. Result downloads use the `application/octet-stream` media type. Poll no faster than
`Retry-After`, handle terminal failed/cancelled states, and download before retention expires.

## Reverse conversion

Reverse conversion is an authenticated, asynchronous workflow. `GET
/api/v1/reversions/capabilities` returns the versioned admission contract: `schema_version`, ordered
`format_families`, content-detection and mismatch policies, `maximum_upload_bytes`, result package
modes, PDF limitations, and execution flags. The response is `private, no-store` and requires an
authenticated session. Clients must use its format families and configured size instead of
maintaining another format matrix. A client that does not recognize the schema, or cannot load the
response, must leave submission unavailable. The server remains authoritative for upload size,
malware scanning, and content detection.

Submit one multipart `source` file to `POST /api/v1/reversions`; an optional `Idempotency-Key`
supports safe replay of the same request. A newly accepted submission returns `202 Accepted`, a
`Location` for the job, and `Retry-After`. `GET /api/v1/reversions` lists the current user's jobs
with `offset` and `limit` pagination. Read a job with `GET /api/v1/reversions/{job_id}`, request
cancellation with `DELETE` on that path, and download its result from `GET
/api/v1/reversions/{job_id}/result`. The result is Markdown or a ZIP according to `result_mode`;
responses are `private, no-store`. Poll according to `Retry-After` and download before `expires_at`.

These job routes are owner-only, including for administrators. Unknown and other users' job IDs
return the same not-found behavior; administrator audit or operational access does not grant access
to a user's source, status, cancellation, or result. Errors use the standard stable JSON envelope
and correlation identifier described above. Submission can fail for invalid or unsupported input,
scanner rejection, a conflicting idempotency key, configured capacity, incomplete reverse
configuration, or unavailable service; consult the stable error code and correlation identifier.

The local engine does not provide OCR or a hosted Firecrawl fallback. PDF support extracts text
without preserving images or layout; scanned or image-only input, or a page with no extractable text,
fails with `needs_ocr`. The advertised capabilities response is the source for supported formats
and current limits.

## Templates

`GET /api/v1/templates` supports visibility-aware pagination and filters for name, description,
owner, and status. `POST /api/v1/templates` creates an identity and initial immutable DOCX version
from multipart `name`, `description`, repeated `expected_fonts`, and `content` fields.

Identity and version operations include:

- `GET` and `PATCH /api/v1/templates/{template_id}`;
- `GET` and `PUT /api/v1/templates/{template_id}/content`;
- `GET /api/v1/templates/{template_id}/versions` and version content retrieval;
- restore and archive actions;
- deletion of a permitted template;
- per-user preference and administrator system-fallback updates.

Conditional mutations use `If-Match`. Send the current validator returned by the preceding read;
on a conflict, fetch current state and reconcile. Content replacement and restore publish a new
version atomically. See [versioned template API](templates.md) for authorization, audit, archive,
retention, and exact endpoint behavior.

`GET /api/v1/conversion-options` is an authenticated, non-cacheable read of the configured
`conversion_upload_max_bytes` and the resolved immutable template selection. It returns the
selected template and exact `template_version_id`, or a strict null pair, plus one stable
`selection_source`: `pandoc_default`, `preferred`, or `system_fallback`. `GET
/api/v1/template-context` similarly returns the current user's `preferred_template_id`, the
`system_fallback_template_id`, and configured `template_max_archive_bytes`. Neither response
contains storage paths, credentials, or deployment secrets.

## Administration and audit

Administrators can list and create users, change active state, reset a password, and set or cancel
the next-login renewal requirement under `/api/v1/admin/users`. Creation and reset payloads accept
`password_change_required`; `PATCH /api/v1/admin/users/{id}/password-change-required` accepts
`required`. Security mutations invalidate affected sessions through the account's authentication
version. `GET /api/v1/audit` exposes paginated audit records to authorized
administrators; records contain identifiers and action metadata, not document bodies or passwords.

`GET /api/v1/admin/session-policy` returns `user_idle_minutes`, `admin_idle_minutes`, the exact
positive `absolute_lifetime_seconds` operator ceiling, current `revision`, and an `ETag`. Its
authoritative `user_idle_minutes_bounds` and `admin_idle_minutes_bounds` objects each provide
`minimum_minutes`, `default_minutes`, and `maximum_minutes`; `idle_minutes_granularity` is `1`.
Clients, including the frontend, consume those values rather than duplicating policy bounds.
`PUT` on the same path requires the session CSRF header plus that
validator in `If-Match` and atomically replaces both values. Standard-user values must be whole
minutes from 5 through 300 inclusive; administrator values must be whole minutes from 5 through 60
inclusive. Missing preconditions return `428`; malformed or stale validators return `412`, leave
both values unchanged, and append no audit. A successful update returns the next revision and ETag
and records actor, old/new pairs, revision, and operation without credentials. If either duration
exceeds the operator-configured absolute session lifetime, the update returns `422` and does not
change policy or audit state.
The supported CLI mirrors these operations with `markweave session-policy get` and
`markweave session-policy update --user-idle-minutes N --admin-idle-minutes N`. The update
command reads the current ETag immediately before sending the CSRF-protected replacement.

The generated OpenAPI document is the exact source for request schemas, response status codes,
field names, and header names. Pin or regenerate a client against the deployed release rather than
assuming an undocumented compatibility contract.

The repository commits the normalized v1 artifact and validates compatible evolution in CI. See
[OpenAPI contract maintenance](openapi-contract.md) for regeneration, review, and intentional
major-version change procedures.

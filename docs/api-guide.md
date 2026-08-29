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
The result media type depends on the requested output. Poll no faster than `Retry-After`, handle
terminal failed/cancelled states, and download before retention expires.

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

## Administration and audit

Administrators can list and create users, change active state, reset a password, and set or cancel
the next-login renewal requirement under `/api/v1/admin/users`. Creation and reset payloads accept
`password_change_required`; `PATCH /api/v1/admin/users/{id}/password-change-required` accepts
`required`. Security mutations invalidate affected sessions through the account's authentication
version. `GET /api/v1/audit` exposes paginated audit records to authorized
administrators; records contain identifiers and action metadata, not document bodies or passwords.

The generated OpenAPI document is the exact source for request schemas, response status codes,
field names, and header names. Pin or regenerate a client against the deployed release rather than
assuming an undocumented compatibility contract.

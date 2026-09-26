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

Use `GET /api/v1/conversions` for the current user's paginated list. The optional
`output_family=document|presentation` filter groups DOCX/PDF/both results separately from
PPTX/PPTX-bundle results. The optional `expired=true|false` filter selects expired jobs or excludes
them. Filters apply before `offset` and `limit`, and `total` counts only matching jobs. Omitting both
filters preserves the complete owner-scoped history. Use `GET /api/v1/conversions/{job_id}` for one
job. `DELETE` on the job requests cancellation.
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

## Composer foundations

`GET /api/v1/composer/capabilities` reports `unconfigured`, `disabled`, `unauthorized`,
`ready`, or `outage` for the signed-in user. Conversion readiness and downloads are independent
of this status. Connection setup is available at `/composer/connections` and in the installed
`markweave composer` CLI. The browser and CLI show only authorized connection metadata;
submitted credentials are write-only. The same capability response advertises the authoritative
`maximum_upload_bytes`, `maximum_credential_bytes`, `maximum_model_request_bytes`,
and `maximum_output_tokens` for bounded client preflight,
or `null` while Composer is disabled.

`GET/POST /api/v1/composer/connections` list and create visible connections. `GET/PATCH/DELETE
/api/v1/composer/connections/{id}` inspect, change, or revoke one. Mutations on an existing
connection require its current `ETag` in `If-Match`. Instance connections require administrator
rights; personal connections require an explicit per-user grant. Administrators manage those
grants through `GET /api/v1/composer/personal-permissions` and conditional `PUT
/api/v1/composer/personal-permissions/{user_id}`. Instance identity may be shared or individual.
Permissions are checked again when calling the provider.
An explicitly operator-configured per-connection grant limit bounds instance ACL payloads.

`PUT /api/v1/composer/connections/{id}/credentials` accepts an API key and/or client
certificate/private key, plus an optional internal CA. Submitted fields rotate those fields;
omitted secret fields remain sealed. `{ "revoke": true }` clears credentials. The response
contains only presence flags. `GET /api/v1/composer/connections/{id}/models` discovers permitted
models and `POST /api/v1/composer/connections/{id}/test` uses the same server-side destination,
TLS, credential, and request bounds as production calls. Operators configure destination and
address allowlists; document text cannot supply an endpoint.

`POST /api/v1/composer/drafts` accepts multipart `source` (`.md`, `.zip`, `.docx`, `.pptx`, or `.pdf`),
optional `title`, and optional `content`. The backend scans the exact uploaded bytes before
validation or persistence. ZIP packages use the conversion archive's bounded validation,
unambiguous Markdown entrypoint, and image checks; the original scanned package remains the
source of record. `POST /api/v1/composer/drafts/from-conversion/{job_id}` copies an
authorized conversion result through the scanner and retains its exact job/result/digest origin;
the original job remains unchanged. `POST /api/v1/composer/drafts/from-reversion/{job_id}`
does the same for an owner-authorized successful 2md result, accepting plain Markdown or its
canonical Markdown-and-assets ZIP package. Both handoffs scan the exact result bytes before
Composer persistence, preserve the immutable origin IDs and digest, and leave the original job
untouched. `GET /api/v1/composer/drafts` and `GET/PUT
/api/v1/composer/drafts/{id}` access owner drafts. Saving requires `If-Match`. Message and
proposal subroutes retain owner conversation and proposal decisions; a decision may accept, edit,
or reject but cannot silently overwrite a newer draft version. A proposal also becomes stale when
the draft changes between its creation and human decision. Message creation requires both
`If-Match` and an `Idempotency-Key` for safe retries. Draft/history reads remain
available during provider outage or connection disablement.

`POST /api/v1/composer/drafts/{id}/model-steps` starts one durable, bounded provider call for
explicitly approved text. The caller first inspects the authorized connection's endpoint and
selected model, then supplies the same `approved_endpoint` and `approved_model` with the exact
`content`, `connection_id`, and `max_output_tokens`. The request requires the current draft
`If-Match` and an `Idempotency-Key`; retries with changed content or preconditions fail. The
response contains a step ID and content-free state. `GET .../model-steps/{step_id}` reads its
owner-scoped state from the shared database, including `intent`, a pending `proposal_id`, or
an assistant `question_id` after completion.
`DELETE .../model-steps/{step_id}` durably cancels an in-flight step from any replica. A cancelled,
stale, unauthorized, or invalid result cannot create a proposal; a successful result creates only
a pending proposal for human review. A pending proposal holds no execution slot while awaiting a
decision. Running steps abandoned by a process restart expire safely without publishing output.
When the configured global call capacity is full, model discovery, connection testing, and
model-step admission return `503 COMPOSER_CAPACITY_EXHAUSTED` with the operator-configured
`Retry-After` interval. Local saturation does not mark a healthy provider as unavailable.
If another authorized test occupies the transport after a model step has already been admitted,
the durable step ends as `failed` with safe `capacity_exhausted` and no proposal; retry with a new
idempotency key when capacity returns. Operational metrics expose active local steps, bounded
durations, failures, saturation, retries, and startup recovery without content labels.
For missing information, pass `intent: "question"` to the same bounded model-step route. A
successful completion must be one plain question of at most 500 characters; invalid prose or
edit-shaped output fails the step with safe `provider_invalid`. The completed step creates a
durable pending question and releases its execution slot. `GET
/api/v1/composer/drafts/{id}/questions` and `GET .../questions/{question_id}` read owner-scoped
questions during provider outage, including stable question text, model-step ID, state, and the
linked answer message ID and content after answering. `source_author_ids` identifies any author
entries selected for the originating question. `POST .../questions/{question_id}/answer`
accepts `{ "content": "..." }` with the current draft `If-Match` and an `Idempotency-Key`; it
atomically appends one user message and changes the question from `pending` to `answered`,
returning the new draft ETag. A stale answer or second different answer fails. The user may
explicitly start a fresh bounded model step with `answered_question_id` at the exact answered
draft version. This link is persisted on the new step; an already running or completed resume
cannot be duplicated, and any later human draft edit prevents stale model output publication.
If the originating question used author entries, the resumed step must include the same exact
author IDs and versions in a freshly reviewed preview; revoked or changed entries block resume.

`GET/POST /api/v1/composer/authors` lists visible private author entries and creates one for the
signed-in owner. Each structured field has a value, provenance, and optional source reference;
an unresolved field cannot masquerade as an approved value. `GET/PATCH .../authors/{id}` reads or
changes an entry; the owner alone may update it. `PUT/DELETE .../authors/{id}/grants/{user_id}`
grants or revokes access for an identified active user. Updates and grants require `If-Match`,
and the content-free audit records the operation. An administrator has no implicit cross-owner
access. `POST /api/v1/composer/drafts/{id}/model-steps/preview` accepts the draft `If-Match`,
approved connection/model, exact base content, and selected author IDs. It returns the exact
transmitted content, author IDs and versions, and a preview digest. Starting a model step with
these references and digest requires them to remain unchanged. Authorization is rechecked at
admission, immediately before outbound transmission, on resume, and before proposal or question
publication. A revoked or changed entry fails closed; its content is not written to logs or
audit records.

`GET/POST /api/v1/composer/fill-templates` lists authorized private typed DOCX templates and
creates one from multipart `name`, JSON `schema`, and DOCX `file`. This is a distinct namespace
from Pandoc style references. The upload is bounded and malware-scanned before schema/OOXML
parsing or storage. `GET .../fill-templates/{id}` reads its identity; `POST
.../fill-templates/{id}/versions` creates an immutable replacement with `If-Match`; `GET
.../versions` lists exact versions; `GET .../versions/{version_id}` reads the schema/digests;
`GET .../versions/{version_id}/content` downloads its DOCX. Owner-only `PUT/DELETE
.../fill-templates/{id}/grants/{user_id}` share or revoke access to a named user. Template
access does not imply author-directory access. Unsupported content controls, unsafe packages,
invalid schemas, and unsatisfied constraints fail before publication.

`POST /api/v1/composer/drafts/{id}/fill-plans` binds the current source revision, exact typed
template version, selected author versions, and proposed values under the draft `If-Match` and
an `Idempotency-Key`. The plan is durable and carries field provenance and explicit missing or
ambiguous questions. `GET .../fill-plans` and `GET .../fill-plans/{plan_id}` recover it across
refreshes. `PATCH .../{plan_id}` sends reviewed values and provenance with its `If-Match` and
an idempotency key; `POST .../{plan_id}/approve` requires every question resolved. Approval
changes no document. `POST .../{plan_id}/publish` rechecks every grant and exact source/template
reference, fills only the qualified Word content controls, and atomically creates a new immutable
revision with matching DOCX preview/download artifacts. `POST
.../revisions/{revision_id}/regenerations` uses the retained approved values, schema and template
version with no provider request; it rejects a changed output digest. Both publications require
the current `If-Match` and an idempotency key.

`POST /api/v1/composer/drafts/{id}/revisions/from-source` captures the exact scanned source as
download and preview artifacts. For ZIP packages, the download is the original ZIP and the
preview is its validated Markdown entrypoint. This source capture does not render later draft edits; its frozen
values describe the original source bytes. It requires `If-Match` and an `Idempotency-Key`.
For a 2md ZIP result, source capture extracts the bounded `document.md` entry from the
previously validated immutable package while retaining its complete ZIP as the download.
`POST /api/v1/composer/drafts/{id}/proposals/{proposal_id}/publish` takes a human-accepted or
human-edited Markdown proposal and publishes its exact decided text as a new immutable revision.
It requires the current draft `If-Match` and an `Idempotency-Key`. Rejected, pending, stale, empty,
oversized, and native Office publication attempts fail without changing the current revision.
For Markdown-and-assets ZIP drafts, the approved Markdown becomes matching preview/download
artifacts while the original scanned ZIP remains an immutable `source` artifact.
For proposals from a model step, publication checks the completed step and freezes its model
identity in the revision. Publication also advances saved draft text atomically, so the next
human edit starts from the accepted content.
This operation treats the approved proposal as complete Markdown content; it does not execute
model-supplied tools or modify native Office files.
`POST /api/v1/composer/drafts/{id}/revisions/from-draft` publishes the exact current saved
Markdown text with `If-Match` and `Idempotency-Key`, without a provider call. It supports plain
Markdown and validated Markdown-and-assets ZIP drafts. ZIP publication retains the original
scanned package as a separate immutable `source` artifact alongside matching Markdown
`download` and `preview` artifacts. This operation never substitutes the original source
content for a later human edit. Human publication records the prior approved revision ID and
earlier model receipt as lineage in `render_options`; its own `model_identity` remains null so
the human edit is not attributed to the model. That lineage follows later generated revisions.

`POST /api/v1/composer/drafts/{id}/revisions/{approved_revision_id}/generations` accepts
`{ "output": "docx"|"pdf"|"pptx", "template_id": null,
"template_version_id": null, "presentation_dialect": null, "slide_level": null }`
with `If-Match` and `Idempotency-Key`. The source must be the current approved Markdown
revision. Template ID and version must be supplied together; the existing conversion service
checks that the version is usable for the chosen output. Presentation options apply to PPTX.
The route freezes exact approved Markdown, source assets, selected template version, options,
and component versions before queueing the existing durable conversion job. It returns `202`
with generation ID, job ID, status, frozen option metadata, and `publishable`; retrying the same
key recovers the same generation. If submission was interrupted before its job was attached,
retry uses the persisted options and rejects a changed runtime component version before sending
a new job. Publication checks the job's frozen version and option receipt. For ZIP sources, it
builds and scans a bounded package from
the approved Markdown and validated original images, excluding a reverse-conversion manifest.
No model call occurs during generation.
The conversion worker also checks the queued job's component manifest against its own qualified
runtime before any document engine runs. An ordinary conversion or Composer generation queued
before an incompatible upgrade fails with `runtime_version_mismatch`; no result is published,
and the caller can submit a fresh request with a new idempotency key. Existing successful results
remain available. This fence needs no database migration.

`GET /api/v1/composer/drafts/{id}/generations?limit=20&offset=0` returns newest-first
`{ "generations": [...], "limit": 20, "offset": 0 }` for refresh or lost-response recovery.
`GET .../generations/{generation_id}` reads current job status; `DELETE` cancels the linked
job. A succeeded generation reports `publishable` only while its approved source revision and
draft version remain current. `POST .../generations/{generation_id}/publish` requires fresh
`If-Match` and `Idempotency-Key`, verifies the successful owner-bound job and exact result, and
copies its result bytes into a new native revision. Matching `download` and `preview` artifacts
share those bytes; PDF revisions retain the traceability JSON artifact and ZIP sources retain
their original `source` artifact. The revision preserves approved Markdown values and model
identity and records source revision, job, input/result digests, template, and options. Failed,
cancelled, expired, or stale generations leave the prior revision intact.
`GET /api/v1/composer/drafts/{id}/revisions` lists immutable published revisions; `GET
.../revisions/{revision_id}` reads one, and `GET
.../revisions/{revision_id}/artifacts/{source|download|preview|traceability}` returns bytes
from that exact revision when that artifact kind exists.
`GET .../revisions/{revision_id}/diff?from_revision_id={prior_id}` reports complete bounded
line changes for two owned Markdown downloads or frozen approved Markdown associated with a
generated native revision. Its `scope` distinguishes artifact text from approved source text;
`metadata_changes` separately names output, template, presentation option, or component version
changes. Native Office structure is not semantically diffed. Safety bounds return an explicit
`unavailable` reason, and line numbers are one-based with no partial changes.
`POST .../revisions/{revision_id}/restore` with `If-Match` and `Idempotency-Key` makes a new
copy-forward revision with matching artifacts and no provider call.

All Composer responses with private data are non-cacheable. Unknown or other users' drafts and
revisions return the same not-found behavior. Provider failures use sanitized errors and leave
retained drafts, revisions, and existing exports available. Connection, personal-permission,
draft, message, proposal, revision, and generation lists accept `limit` (1–100, default 50) and `offset`
(nonnegative) and return both in the response for bounded pagination.
Message, proposal, question, and revision lists also accept `order=asc|desc` (default `asc`).
Use `order=desc&limit=100&offset=0` to retrieve the newest bounded page, then increase
`offset` for older pages. Equal timestamps use ID as a stable tie-break; revisions use
their unique number then ID. Invalid orders return 422. Draft and revision lists return
compact metadata; fetch one ID to read complete draft text or frozen revision values.

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

# Versioned template API

A template has a stable UUID, an owner UUID derived from the authenticated account, a name,
a description, and an `active` or `archived` status. The domain model is frozen, repository ports
do not expose owner reassignment, and both SQLite and PostgreSQL reject direct owner changes.
Template-version object keys derive from stable UUIDs rather than owner
names, template names, or uploaded filenames.

Application code creates identities through `TemplateService.create` and a `TemplateCreate` input
that deliberately has no owner field. The service always copies the authenticated actor UUID into
the immutable owner field before persistence, including when a structurally compatible object tries
to carry a different owner UUID. Direct repository insertion exists only as a persistence boundary
and is not an authenticated creation use case.

## Visibility and search

Every active template is visible to every authenticated user. An archived template is visible
only to its owner and global administrators. Search applies this visibility predicate before
returning results and supports independent filters for normalized name, normalized description,
owner UUID, and status. Unicode NFKC normalization and case folding happen before persistence so
SQLite and PostgreSQL produce the same contains-search behavior. Results use a deterministic
normalized-name and UUID order and return an explicit total, offset, and caller-selected positive
page size. The API schema defines the accepted page-size range.

## Preferences and fallback

Each account has at most one preferred template. Setting or clearing a preference and setting the
singleton system fallback are recorded in the content-free audit trail. Setting a preference or
fallback transactionally requires an active, published template. Selection resolves an active user
preference first, then an active system fallback, and otherwise returns no template. An archived
preference remains recorded but is ignored during resolution. Archive and restoration do not
silently change ownership or preference history.

Only an administrator may set the system fallback. Owner/administrator mutation authorization
records actor, owner, target, operation, and whether the action is an administrator intervention.

## HTTP lifecycle and concurrency

Authenticated clients use `/api/v1/templates` to search or create templates. Creation and content
replacement accept a multipart DOCX plus one or more `expected_fonts` declarations. Before a
version can become visible, the service invokes the complete activation boundary: bounded
OpenXML and relationship checks, required Pandoc styles, active-content exclusions, declared-font
resolution against the pinned manifest, a blank Pandoc conversion using the candidate as
`reference.docx`, and an isolated LibreOffice rewrite. The immutable version row records declared
fonts, resolved substitutions, and the successful validation-stage trace. Missing or unsupported
font declarations and engine failures return sanitized validation errors and publish neither
metadata nor bytes. All safety ceilings and engine paths/timeouts are required
`MD_CONVERTER_TEMPLATE_*` settings; operators retain approval of their production values.

Every identity response carries an `ETag` of the form
`"template-<template UUID>-<revision>"`. Metadata updates, replacements, restorations, archive, and
deletion require that exact value in `If-Match`. A missing precondition returns `428`; a stale,
malformed, or disallowed mutation returns `412`. Concurrent replacements may validate and stage
independently, but only one compare-and-swap transaction publishes a new current version. The
losing unpublished object is removed.

Content routes use stable download names, the DOCX media type, `nosniff`, and a SHA-256 ETag. Every
download, restore, and worker resolution recomputes both byte length and SHA-256 before returning
content; a mismatch is a sanitized service-integrity failure rather than corrupted output. They
never reflect an uploaded filename. Active content and every immutable prior version are visible
to all authenticated users; archived content and history remain visible only to the owner and
global administrators.

Replacement always creates the next immutable version. Restoration reads a historical object and
creates a new copy-forward version recording `restored_from_version_id`; it never rewrites
history. Conversion submission either omits both template identifiers to request Pandoc's native
reference document, or locks and verifies one active, current, published template/version pair in
the same transaction that freezes those identifiers on the job. Production workers are
assembled through `build_template_conversion_worker`, which always installs
`FrozenTemplateJobProcessor`; it uses `TemplateService.resolve_frozen_version` to give the
conversion processor exactly those validated bytes after later replacements or restorations.

## Authorization, audit, archive, and deletion

Only the immutable owner or a global administrator may rename, update a description, replace,
restore, archive, or delete. Every mutation writes a content-free audit record with actor, owner,
operation, target, version when applicable, timestamp, and administrator-intervention flag in the
same database transaction as the metadata change. Administrator fallback selection is audited in
the same transaction as the singleton update.

Deletion requires an archived identity and its current ETag. It is rejected while a preference,
system fallback, or conversion job references the identity. The repository first commits a durable
`deleting` tombstone, then object deletions use idempotent store operations, and only a fully cleaned
identity is removed. Creation and replacement likewise reserve hidden `pending` rows before bytes
are written and publish the current pair only after the object succeeds. Pending publications carry
a unique fencing token and caller-configured lease expiry. Reconcilers atomically claim only expired
rows, and finalization, abort, and retry release require the current token, so multiple replicas
cannot clean up a live upload. Application startup retries those stale claims and deletion
tombstones, so a process or object-store failure cannot expose a partial version or permanently lose
cleanup work. Archive preserves history and preferences; selection resolution ignores archived
identities.

SQLite/filesystem and PostgreSQL/S3 share the service and repository contracts. Filesystem writes
use fsync plus atomic replacement; S3 and filesystem keys are stable immutable version UUIDs.
Database constraints and triggers enforce owner/pair integrity, immutable version evidence,
current-version membership, active/current job submission, and deletion restrictions in addition
to the application-level transactions.

The owner and administrator browser interface is described in
[administration-ui.md](administration-ui.md). Functional, storage-boundary, browser, and final-image
tests cover the lifecycle in both profiles.

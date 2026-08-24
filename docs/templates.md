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
page size. T14 does not choose a product page-size limit.

## Preferences and fallback

Each account has at most one preferred template. Setting a preference or the singleton system
fallback transactionally requires an active template. Selection resolves an active user
preference first, then an active system fallback, and otherwise returns no template. An archived
preference remains recorded but is ignored during resolution, allowing T15 to define archive and
restoration behavior without silently changing ownership or preference history.

Only an administrator may set the system fallback. Owner/administrator mutation authorization
records actor, owner, target, operation, and whether the action is an administrator intervention.

## HTTP lifecycle and concurrency

Authenticated clients use `/api/v1/templates` to search or create templates. Creation accepts a
multipart DOCX and validates bounded OpenXML structure, required Pandoc styles, relationships,
active-content exclusions, and the approved font contract before publishing version 1. The safety
ceilings are configurable `MD_CONVERTER_TEMPLATE_*` settings; T18 retains approval of production
values.

Every identity response carries an `ETag` of the form
`"template-<template UUID>-<revision>"`. Metadata updates, replacements, restorations, archive, and
deletion require that exact value in `If-Match`. A missing precondition returns `428`; a stale,
malformed, or disallowed mutation returns `412`. Concurrent replacements may validate and stage
independently, but only one compare-and-swap transaction publishes a new current version. The
losing unpublished object is removed.

Content routes use stable download names, the DOCX media type, `nosniff`, and a SHA-256 ETag. They
never reflect an uploaded filename. Active content and every immutable prior version are visible
to all authenticated users; archived content and history remain visible only to the owner and
global administrators.

Replacement always creates the next immutable version. Restoration reads a historical object and
creates a new copy-forward version recording `restored_from_version_id`; it never rewrites
history. `TemplateService.resolve_frozen_version` gives a processor the exact `template_id` and
`template_version_id` frozen on a conversion job after later replacements.

## Authorization, audit, archive, and deletion

Only the immutable owner or a global administrator may rename, update a description, replace,
restore, archive, or delete. Every mutation writes a content-free audit record with actor, owner,
operation, target, version when applicable, timestamp, and administrator-intervention flag in the
same database transaction as the metadata change. Administrator fallback selection is audited in
the same transaction as the singleton update.

Deletion requires an archived identity and its current ETag. It is rejected while a preference,
system fallback, or conversion job references the identity. After the guarded metadata transaction
succeeds, object deletions use idempotent store operations; an object-store failure is returned as a
sanitized service error and later orphan reclamation remains part of T18 cleanup policy. Archive
preserves history and preferences; selection resolution ignores archived identities.

SQLite/filesystem and PostgreSQL/S3 share the service and repository contracts. Filesystem writes
use fsync plus atomic replacement; S3 and filesystem keys are stable immutable version UUIDs.
Database publication failure compensates by deleting an unpublished object.

T16 and T17 retain browser interfaces. T20/T21 retain final rootless-image E2E for both profiles;
T15 supplies functional ASGI and real SQLite/filesystem plus PostgreSQL/RustFS boundary coverage
without claiming that later runtime proof.

# Template identity and selection foundations

T14 defines template identity independently of the content and version workflows delivered by
T15. A template has a stable UUID, an owner UUID derived from the authenticated account, a name,
a description, and an `active` or `archived` status. The domain model is frozen, repository ports
do not expose owner reassignment, and both SQLite and PostgreSQL reject direct owner changes.
Future template-version object keys must continue to derive from stable UUIDs rather than owner
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
returns a `TemplateAuthorization` context containing actor, owner, target, operation, and whether
the action is an administrator intervention. T15 must persist that context atomically with its
sensitive version or metadata mutation. T14 deliberately does not add audit storage or pretend
that authorization alone is a completed audit record.

## Deliberate T14 boundary

There are no template HTTP routes, content uploads, downloads, version rows, `ETag` handling,
replacement, restoration, archive/delete commands, or template Web pages in T14. Those behaviors
belong to T15 through T17. Consequently T14 introduces no user-visible or operational workflow to
exercise against the final rootless image; final-image E2E is not applicable here rather than
deferred or waived. Domain, functional service, shared SQLite/PostgreSQL contracts, real database
constraints, concurrency, restart, and failure behavior remain blocking T14 validation.

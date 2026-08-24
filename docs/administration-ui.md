# Template and account administration interface

The authenticated interface is available at `/templates`. Unauthenticated requests redirect to
`/login`. The initial HTML is server-rendered, has a restrictive self-only content security policy,
and loads one external native JavaScript module. User-controlled names and identity text are placed
into the DOM with text nodes rather than interpreted markup.

## Template library

Every authenticated user can browse and download active templates. Each card identifies its owner,
status, name, and description. Search covers those displayed fields, the **My templates** filter
restricts the result to the signed-in owner, and the browser follows every page from the paginated
API rather than silently truncating the library.

Users can make any visible active template their preferred template or clear their preference.
The conversion page resolves that preference first and uses the system fallback only when no active
preference applies.

Owners and global administrators receive lifecycle controls for a template:

- rename and update the description;
- download the current DOCX;
- replace content after complete activation validation;
- inspect and download immutable historical versions;
- restore a historical version by creating a new copy-forward version;
- archive an active template;
- permanently delete an archived template when the API's reference guards allow it.

Create and replace forms accept a `.docx` file and comma-separated expected fonts. The browser
performs only immediate extension, emptiness, and configured-size checks. The server remains
authoritative for OpenXML, font, engine, ownership, status, and storage validation.

Every concurrent lifecycle mutation sends the revision-derived identity ETag through `If-Match`.
A stale page therefore receives the stable precondition error instead of overwriting a newer
change. Archive and permanent deletion require explicit browser confirmation. All permission
checks are repeated in the service after authentication; hiding owner controls from other users is
only a presentation aid.

## Administrator users tab

Only an administrator sees the local-accounts section, and every underlying endpoint independently
requires the administrator role. It lists and filters accounts by username and supports account
creation, deactivation, reactivation, and password reset. Account status changes and password
resets revoke the affected sessions through the authentication service. The interface never
receives password hashes, session tokens, or authentication versions.

## Errors and accessibility

The page has an assertive live error region and a polite live result list. Expected API failures use
their stable English message; an invalid or non-JSON response becomes a generic English failure and
is never reflected into markup. Forms use labels, native controls, headings, and buttons. Downloads
retain the API's generated names, `nosniff`, content digest, and authorization behavior.

## Verification and sequencing

`npm run test:web` executes native JavaScript unit tests and independently blocks line, branch, and
function coverage below 90% across both browser modules. T17 also exercises the assembled HTTPS
application over real SQLite and filesystem boundaries with two ordinary users and an
administrator. A pinned-Chromium browser scenario runs those identities through template creation,
download, metadata changes, replacement, version history and restoration, preference changes,
archive/deletion, server-side denial, account creation and search, status changes, and password
reset.

The hardened final image and its two deployable profiles belong to T20/T21. Consequently, the T17
Chromium scenario uses the validated rootless toolchain image around the current application rather
than claiming final-image E2E. T20/T21 must repeat the primary administration workflows and relevant
authorization, failure, recovery, and concurrency cases against the final image in both profiles.
This is explicit sequencing debt, not a waiver.

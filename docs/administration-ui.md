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

Account creation and password reset can require renewal at the next sign-in. Administrators can
also add or cancel that requirement independently from an account card; changing it revokes
existing sessions. A required user authenticates with the current password, chooses and confirms a
new password on the dedicated page, and then signs in again with the new password.

## Errors and accessibility

The page has an assertive live error region and a polite live result list. Expected API failures use
their stable English message; an invalid or non-JSON response becomes a generic English failure and
is never reflected into markup. Forms use labels, native controls, headings, and buttons. Downloads
retain the API's generated names, `nosniff`, content digest, and authorization behavior.

## Verification

`npm run test:web` executes native JavaScript unit tests and independently blocks line, branch, and
function coverage below 90% across both browser modules. Functional tests exercise the ASGI application through
an HTTPS test origin over real SQLite and filesystem boundaries and separately verifies owner
representation, search, authorization, and storage failures against live PostgreSQL and RustFS.
A pinned-Chromium browser scenario runs two ordinary users and an administrator against a loopback
HTTP server through template creation and invalid uploads, download, metadata changes including a
stale `If-Match`, replacement, version history and restoration, preference changes, guarded
archive/deletion, CSRF and revoked-session denial, account creation and search, status changes, and
password reset. It also checks that duplicate form submission does not create duplicate mutations.
The final-image browser workflow additionally requires password renewal, proves that the current
password must succeed before the restricted page is shown, confirms that normal application routes
remain unavailable, renews the password, and verifies the required fresh login.

The browser suite requires the repository Python environment, the pinned Puppeteer dependency, and
the pinned Chrome version installed by CI. Its committed CI-equivalent invocation is:

```bash
npm ci --prefix spikes/toolchain --omit=dev --ignore-scripts
MD_CONVERTER_TEST_PUPPETEER="$PWD/spikes/toolchain/node_modules/puppeteer-core/lib/puppeteer/puppeteer-core.js" \
MD_CONVERTER_TEST_CHROMIUM=/usr/bin/google-chrome-stable \
npm run test:web-browser
```

The final-image E2E suite exercises the primary administration workflows and relevant authorization,
failure, recovery, and concurrency cases in both storage profiles. Deployment-specific TLS and
rootless controls are described in [container-deployment.md](container-deployment.md).

# Local authentication

## Provisioning and startup

Public registration is not exposed. Startup requires the initial administrator through
`MARKWEAVE_INITIAL_ADMIN_USERNAME` and `MARKWEAVE_INITIAL_ADMIN_PASSWORD`. Invalid, absent,
or blank values stop application construction with a generic configuration error that excludes
field values. Administrators use `/api/v1/admin/users` to create accounts, activate or deactivate
them, list them, and reset passwords.

The bootstrap operation is atomic and idempotent within the selected repository adapter. It never
changes an existing administrator password at application restart. SQLite and PostgreSQL preserve
accounts and sessions across process restarts and implement the same storage-neutral contract.

### Startup CSV provisioning

Set `MARKWEAVE_USER_PROVISIONING_FILE` to an absolute path for a strict UTF-8 CSV available
inside the API container. The file must be a regular file, must not be a symbolic link, and must
use this exact header and lowercase Boolean values:

```csv
username,password,role,active,password_change_required
Alice,replace-this-temporary-password,user,true,true
Operations admin,replace-this-administrator-password,admin,true,false
```

Markweave parses the complete file before changing an account. Empty files, unknown or reordered
columns, blank usernames or passwords, invalid roles or Booleans, malformed UTF-8/CSV, and
duplicate usernames after Unicode normalization stop startup with the content-free
`Invalid user provisioning file` error. Display and normalized usernames are limited to 255
characters, and usernames and passwords cannot contain Unicode control characters, so SQLite and
PostgreSQL accept the same validated input.

After validation, Markweave hashes every password with the configured Argon2id profile and applies
the complete batch in one database transaction. Missing normalized usernames are created. Existing
user identifiers remain stable, while display name, password, role, active state, and renewal
requirement are replaced; the authentication version advances and prior sessions are revoked.
PostgreSQL serializes concurrent API startups with a transaction-scoped advisory lock. Reapplying
the file deliberately reapplies every password and revokes sessions, even when its bytes did not
change.

The CSV contains plaintext credentials. Supply it through the deployment secret mechanism, mount
it read-only, and exclude it from images, source control, backups, and logs. Markweave never copies
or deletes the source file. Remove the setting or rotate the file when continuous startup
reconciliation is not wanted. Readiness is not exposed if provisioning fails.

Usernames are displayed after surrounding whitespace is removed. Uniqueness and login use Unicode
NFKC normalization, surrounding-whitespace removal, and case folding. Values such as `Alice`,
`ALICE`, and compatible full-width spellings therefore collide.

## Passwords and sessions

Passwords use Argon2id. The configurable defaults are `m=19456 KiB`, `t=2`, and `p=1`:

- `MARKWEAVE_ARGON2_MEMORY_COST`
- `MARKWEAVE_ARGON2_TIME_COST`
- `MARKWEAVE_ARGON2_PARALLELISM`

Unknown, inactive, and wrong-password login attempts return the same English error. Unknown and
inactive accounts still perform a dummy Argon2 verification. Every failed verification, including
an invalid or obsolete hash, is completed to exactly two verification work units using the current
Argon2 profile without wall-clock sleeps. A current-profile candidate contributes its real
verification as one unit; legacy and malformed candidates receive two current-profile dummy units.
An obsolete hash is upgraded only after successful password verification and a compare-and-set
check of the account security version. Successful current verification and successful legacy
verification plus rehash each perform one current-profile unit.

Sessions use opaque CSPRNG tokens; `MARKWEAVE_SESSION_TOKEN_BYTES` defaults to 32 bytes and
cannot be lower than 16 bytes. Only SHA-256 token digests are stored server-side. With no persisted
administrator override, standard users expire after 30 idle minutes and administrators after 15
idle minutes. An administrator can atomically configure the system-wide pair through
`/api/v1/admin/session-policy`: standard users accept whole minutes from 5 through 300 and
administrators accept whole minutes from 5 through 60. `MARKWEAVE_SESSION_ABSOLUTE_SECONDS`
defaults to 8 hours and remains an operator-owned hard ceiling regardless of the selected idle
duration. Updates that exceed the configured absolute lifetime are rejected without changing the
policy or audit. The deprecated `MARKWEAVE_SESSION_IDLE_SECONDS` input remains accepted for 0.x
configuration compatibility, emits a startup warning, and does not control effective policy. Login rotates any present session, logout
revokes it, and account deactivation or password reset revokes every session for that account.

FastAPI reads the current policy and the account's current effective role during every session
validation. It compares the resulting deadline with the session's last activity, its previously
stored idle deadline, and its absolute deadline. Tightening a duration or changing a role therefore
applies to already issued cookies immediately. Relaxing a duration can extend only a session that
is still valid; expiry, logout, account security changes, password renewal, and absolute expiry are
never reversed. Successful validation advances activity and stores a new deadline bounded by the
absolute lifetime.

Each account carries a monotonically increasing authentication version. Password reset,
deactivation, and reactivation increment it atomically. Login verifies a snapshot and then uses a
repository compare-and-set operation before issuing a session containing the accepted version.
Authentication compares both versions. Consequently, a concurrent reset cannot be overwritten by
a stale successful verification, and a session created after a concurrent security change is
immediately unusable even if physical session deletion raced. The adapters implement the
compare-and-set and security-version update transactionally in SQLite and PostgreSQL; generic
non-atomic password/account saves are not part of the port.

The cookie name defaults to `md_converter_session` and is configurable with
`MARKWEAVE_SESSION_COOKIE_NAME`. It is always `HttpOnly`, `Secure`, `SameSite=Lax`, and scoped to
`/`. Successful JSON login returns a separate, session-bound CSRF token once. Every authenticated
mutation requires it in `X-CSRF-Token`; a token from another session is rejected.

An administrator can require password renewal when creating or resetting an account, or toggle the
requirement independently. The user first completes normal login with the current password, then
receives a restricted session and is directed to `/change-password`. That session can access only
session inspection, logout, and password renewal. Renewal is session-bound and CSRF-protected,
requires matching nonblank new-password fields, stores the new Argon2id hash, clears the requirement
atomically, revokes the restricted session, and returns the user to login with the new password.
The commit compares the authenticated account version, active state, and renewal requirement, so a
stale request cannot overwrite a concurrent administrator reset or account mutation.

Both login POST routes reject an `Origin` that differs from the request origin before credentials
are evaluated. An exact same-origin value is allowed, as is absence of `Origin` for non-browser API
clients. Deployment proxies must preserve the external scheme and host so Uvicorn constructs the
same origin seen by the browser. This policy prevents an attacker site from logging a victim's
browser into the attacker's account.

## HTTP surface

- `GET /login` and `POST /login`: browser login and redirect to the conversion interface
- `POST /api/v1/login`, `POST /api/v1/logout`, `GET /api/v1/session`: session lifecycle
- `GET /change-password`, `POST /change-password`, `POST /api/v1/password`: password renewal
- `GET /api/v1/admin/users`, `POST /api/v1/admin/users`: account list and creation
- `PATCH /api/v1/admin/users/{id}/active`: activation and deactivation
- `POST /api/v1/admin/users/{id}/password`: administrative password reset
- `PATCH /api/v1/admin/users/{id}/password-change-required`: renewal requirement
- `GET`, `PUT /api/v1/admin/session-policy`: read or atomically replace both role idle durations
- `GET /health/live`, `GET /health/ready`: cheap liveness and readiness probes

Every successful administrator account creation, deactivation, reactivation, password reset, and
idle-session policy update is committed atomically with a content-free immutable audit record.
Policy evidence contains the actor, old pair, new pair, resulting revision, operation, and time;
it contains no cookie, token, password, or credential. `GET /api/v1/audit` merges those records
deterministically with template audits; failed, stale, or unauthorized requests create no audit.
- `GET /docs`, `GET /openapi.json`: interactive and machine-readable API contracts
- `GET /convert`: authenticated server-rendered conversion interface
- `GET /templates`: authenticated template interface and administrator-only account tab

Browser and JSON login responses set the opaque session cookie as HttpOnly. They also set the
session-bound CSRF value in the Secure, SameSite=Lax `__Host-md_converter_csrf` cookie so the external
same-origin browser modules can copy it into `X-CSRF-Token`. The `__Host-` prefix prevents a
subdomain from replacing that cookie; the server still verifies the value against the digest stored
with the session for every mutation.

There is intentionally no signup endpoint.

Expected API failures use the stable English envelope
`{"error":{"code":"...","message":"..."}}`. Request validation never returns Pydantic's raw
error objects or submitted values, so malformed payloads, passwords, and invalid path identifiers
are not reflected. OpenAPI declares this envelope for validation, authentication, administration,
and readiness failures, including the real readiness `503` response.

The final-image entrypoint resolves its internal ASGI factory as
`markweave.app:create_app`; this module path is a container runtime detail, not part of the supported
Python API. Uvicorn is included as the runtime server. Deploy it behind the profile's TLS endpoint
because authentication cookies are always secure. When a proxy terminates TLS, set
`MARKWEAVE_PUBLIC_ORIGIN` to the exact browser-visible scheme, host, and optional port. Forwarded
headers remain untrusted. Without that setting, Origin validation uses the direct ASGI request URL.
See [container deployment](container-deployment.md).

The normal login page sends `Referrer-Policy: same-origin`, which preserves the browser's
same-origin form origin without disclosing referrers cross-origin. The explicit loopback-only
`quickstart-simple.sh up --insecure` evaluation mode disables login-origin validation for temporary
SSH-tunnel tests. It is not an authentication deployment option and must never be exposed to a
network or used in production.

## Next.js migration boundary

The approved frontend migration does not change this authentication contract. After T64, browser
pages come from the same-origin Next.js process, but login, session inspection, logout, password
renewal, expiry, revocation, authorization, cookies, CSRF validation, and Origin validation remain
direct FastAPI operations. Next.js application code may neither read nor forward the HttpOnly
session cookie. The public router enforces this boundary by removing the complete `Cookie` header
from every request selected for the frontend, for every method and including named pages, assets,
and unknown catch-all paths. It removes every `Set-Cookie` field from every frontend response
regardless of method, status, or content type. It applies neither transformation to exact
`/api/v1`, `/api/v1/**`, or public operational routes, so FastAPI continues to receive browser
cookies and issue or clear all session and CSRF cookies directly. Browser JavaScript continues to
read only the `__Host-md_converter_csrf` cookie and copy it to `X-CSRF-Token` for mutations.

The frontend renders no authoritative authenticated state on the server. It loads the principal
from `/api/v1/session`, treats `401` and restricted-session responses as authoritative, and never
extends a session with a client timer. HTML and authenticated data remain `no-store`. The complete
login, renewal, expiry, navigation, race, accessibility, and safe-error parity inventory is in
[the reviewed Next.js migration architecture](nextjs-migration-architecture.md).

## Verification inventory

Unit, functional ASGI, real Argon2id integration, and hardened final-image E2E cover:

- successful administrator login, account creation, user login, session inspection, and logout;
- unknown, wrong-password, inactive-account, missing-session, expired-session, and revoked-session
  failures with stable non-enumerating responses;
- non-administrator account-management denial;
- missing, hostile, cross-session, and replayed CSRF token denial;
- session rotation at login and all-session revocation after deactivation and password reset;
- strict startup CSV validation, atomic create/update, concurrent reapplication, and session
  revocation in both persistence profiles and both final rootless images;
- restricted-session password renewal, CSRF and confirmation failures, administrator requirements,
  and forced relogin with the new password;
- startup failure for absent or invalid bootstrap secrets without secret or hash leakage;
- liveness and readiness behavior in both standalone and distributed profiles.
- role-specific policy defaults, inclusive bounds, strict whole-minute HTTP validation, stale
  writes, restart and backup/restore persistence, immediate tightening and role changes, and
  non-revival after expiry or revocation.

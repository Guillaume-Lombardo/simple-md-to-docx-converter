# Local authentication

## Provisioning and startup

Public registration is not exposed. Startup requires the initial administrator through
`MD_CONVERTER_INITIAL_ADMIN_USERNAME` and `MD_CONVERTER_INITIAL_ADMIN_PASSWORD`. Invalid, absent,
or blank values stop application construction with a generic configuration error that excludes
field values. Administrators use `/api/v1/admin/users` to create accounts, activate or deactivate
them, list them, and reset passwords.

The bootstrap operation is atomic and idempotent within the selected repository adapter. It never
changes an existing administrator password at application restart. SQLite and PostgreSQL preserve
accounts and sessions across process restarts and implement the same storage-neutral contract.

Usernames are displayed after surrounding whitespace is removed. Uniqueness and login use Unicode
NFKC normalization, surrounding-whitespace removal, and case folding. Values such as `Alice`,
`ALICE`, and compatible full-width spellings therefore collide.

## Passwords and sessions

Passwords use Argon2id. The configurable defaults are `m=19456 KiB`, `t=2`, and `p=1`:

- `MD_CONVERTER_ARGON2_MEMORY_COST`
- `MD_CONVERTER_ARGON2_TIME_COST`
- `MD_CONVERTER_ARGON2_PARALLELISM`

Unknown, inactive, and wrong-password login attempts return the same English error. Unknown and
inactive accounts still perform a dummy Argon2 verification. Every failed verification, including
an invalid or obsolete hash, is completed to exactly two verification work units using the current
Argon2 profile without wall-clock sleeps. A current-profile candidate contributes its real
verification as one unit; legacy and malformed candidates receive two current-profile dummy units.
An obsolete hash is upgraded only after successful password verification and a compare-and-set
check of the account security version. Successful current verification and successful legacy
verification plus rehash each perform one current-profile unit.

Sessions use opaque CSPRNG tokens; `MD_CONVERTER_SESSION_TOKEN_BYTES` defaults to 32 bytes and
cannot be lower than 16 bytes. Only SHA-256 token digests are stored server-side. Idle and absolute
lifetimes default to 30 minutes and 8 hours and are configured with
`MD_CONVERTER_SESSION_IDLE_SECONDS` and `MD_CONVERTER_SESSION_ABSOLUTE_SECONDS`. Login rotates any
present session, logout revokes it, and account deactivation or password reset revokes every
session for that account.

Each account carries a monotonically increasing authentication version. Password reset,
deactivation, and reactivation increment it atomically. Login verifies a snapshot and then uses a
repository compare-and-set operation before issuing a session containing the accepted version.
Authentication compares both versions. Consequently, a concurrent reset cannot be overwritten by
a stale successful verification, and a session created after a concurrent security change is
immediately unusable even if physical session deletion raced. T12 adapters must implement the
compare-and-set and security-version update transactionally in SQLite and PostgreSQL; generic
non-atomic password/account saves are not part of the port.

The cookie name defaults to `md_converter_session` and is configurable with
`MD_CONVERTER_SESSION_COOKIE_NAME`. It is always `HttpOnly`, `Secure`, `SameSite=Lax`, and scoped to
`/`. Successful JSON login returns a separate, session-bound CSRF token once. Every authenticated
mutation requires it in `X-CSRF-Token`; a token from another session is rejected.

Both login POST routes reject an `Origin` that differs from the request origin before credentials
are evaluated. An exact same-origin value is allowed, as is absence of `Origin` for non-browser API
clients. Deployment proxies must preserve the external scheme and host so Uvicorn constructs the
same origin seen by the browser. This policy prevents an attacker site from logging a victim's
browser into the attacker's account.

## HTTP surface

- `GET /login` and `POST /login`: browser login and redirect to the conversion interface
- `POST /api/v1/login`, `POST /api/v1/logout`, `GET /api/v1/session`: session lifecycle
- `GET /api/v1/admin/users`, `POST /api/v1/admin/users`: account list and creation
- `PATCH /api/v1/admin/users/{id}/active`: activation and deactivation
- `POST /api/v1/admin/users/{id}/password`: administrative password reset
- `GET /health/live`, `GET /health/ready`: cheap liveness and readiness probes
- `GET /docs`, `GET /openapi.json`: interactive and machine-readable API contracts
- `GET /convert`: authenticated server-rendered conversion interface

Browser and JSON login responses set the opaque session cookie as HttpOnly. They also set the
session-bound CSRF value in the Secure, SameSite=Lax `__Host-md_converter_csrf` cookie so the external
same-origin conversion module can copy it into `X-CSRF-Token`. The `__Host-` prefix prevents a
subdomain from replacing that cookie; the server still verifies the value against the digest stored
with the session for every mutation.

There is intentionally no signup endpoint.

Expected API failures use the stable English envelope
`{"error":{"code":"...","message":"..."}}`. Request validation never returns Pydantic's raw
error objects or submitted values, so malformed payloads, passwords, and invalid path identifiers
are not reflected. OpenAPI declares this envelope for validation, authentication, administration,
and readiness failures, including the real readiness `503` response.

The ASGI factory is `md_converter:create_app`; Uvicorn is included as the runtime server. Deploy it
behind the profile's TLS endpoint because authentication cookies are always secure.

## Deferred final-image verification

The project manager approved a T06 exception for final-image E2E because the rootless image and its
two runtime profiles do not exist before T20/T21. Unit, functional ASGI, and real Argon2id
integration coverage remain blocking in T06. T20/T21 must repeat this exact inventory against the
hardened rootless image:

- successful administrator login, account creation, user login, session inspection, and logout;
- unknown, wrong-password, inactive-account, missing-session, expired-session, and revoked-session
  failures with stable non-enumerating responses;
- non-administrator account-management denial;
- missing, hostile, cross-session, and replayed CSRF token denial;
- session rotation at login and all-session revocation after deactivation and password reset;
- startup failure for absent or invalid bootstrap secrets without secret or hash leakage;
- liveness and readiness behavior in both standalone and distributed profiles.

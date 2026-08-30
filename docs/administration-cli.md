# Administration, audit, and health CLI

The `markweave` administration family uses only the documented HTTP API. User and
audit commands require an authenticated administrator profile. Health, readiness,
and metrics commands may instead use `--url` because those endpoints are public in
the current server contract; when `--url` is omitted, the selected profile supplies
only its service URL and no session credential is sent.

## User administration

```text
markweave users list [--profile NAME]
markweave users create --username USER [--require-password-change] [--force] [--profile NAME]
markweave users activate USER_ID [--force] [--profile NAME]
markweave users deactivate USER_ID [--force] [--profile NAME]
markweave users reset-password USER_ID [--require-password-change] [--force] [--profile NAME]
markweave users require-password-change USER_ID [--clear] [--force] [--profile NAME]
```

Every mutation asks for explicit confirmation. `--force` records that confirmation
for automation and is required with `--non-interactive`. User creation and password
reset still require a secure terminal because the new password and its confirmation
are read without echo. Password options are rejected, and passwords are never read
from the environment, generated, persisted, or displayed. There is no approved
one-time password-output contract.

Creation and reset can require renewal at the next login. The dedicated renewal
command requires it by default; `--clear` cancels the requirement. Activation,
deactivation, resets, and renewal changes retain the server's authorization,
authentication-version, session-revocation, and audit behavior.

## Audit pagination

```text
markweave audit [--offset N] [--limit N] [--profile NAME]
```

The offset is non-negative and the limit is between 1 and 100. Results retain the
server's newest-first order. Human output is stable tab-separated content-free
metadata. `--json` returns `items`, `offset`, and `limit` without adding usernames,
passwords, document data, or other fields absent from the API.

## Service inspection

```text
markweave health live [--url URL | --profile NAME]
markweave health ready [--url URL | --profile NAME]
markweave health metrics [--url URL | --profile NAME]
```

Liveness and readiness preserve the server's stable JSON status and error envelope.
A readiness failure exits with status 1 and the safe `not_ready` diagnostic. Human
metrics output is the exact Prometheus text with one normalized trailing newline;
JSON output places that text in the `metrics` field. Responses are bounded to one
MiB, redirects are not followed, TLS remains mandatory except for the documented
literal loopback evaluation URLs, and network failures expose no response body,
credential, or traceback.

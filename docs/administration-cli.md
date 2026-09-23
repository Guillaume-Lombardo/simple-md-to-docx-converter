# Administration, audit, and health CLI

The `markweave` administration family uses only the documented HTTP API. User and
audit commands require an authenticated administrator profile. Health, readiness,
and metrics commands may instead use `--url` because those endpoints are public in
the current server contract; when `--url` is omitted, the selected profile supplies
only its service URL and no session credential is sent.

## Composer connections

Composer connection commands use the authenticated HTTP API and the same named
profiles as the other remote command families:

```text
markweave composer capabilities [--profile NAME]
markweave composer connections list [--offset N] [--limit N] [--profile NAME]
markweave composer connections show CONNECTION_ID [--profile NAME]
markweave composer connections create --name NAME --endpoint HTTPS_URL \
  --scope {instance,personal} --identity-mode {shared,individual} \
  [--permitted-model MODEL] [--allowed-user USER_ID] [--with-credentials] \
  [--api-key-file PATH] [--client-certificate-file PATH] \
  [--client-private-key-file PATH] [--internal-ca-file PATH] \
  [--profile NAME]
markweave composer connections update CONNECTION_ID [metadata options] \
  --etag ETAG [--profile NAME]
markweave composer connections enable CONNECTION_ID --etag ETAG [--profile NAME]
markweave composer connections disable CONNECTION_ID --etag ETAG [--profile NAME]
markweave composer connections models CONNECTION_ID [--profile NAME]
markweave composer connections select-model CONNECTION_ID --model MODEL \
  --etag ETAG [--profile NAME]
markweave composer connections test CONNECTION_ID [--profile NAME]
markweave composer connections credentials rotate CONNECTION_ID \
  --etag ETAG [--api-key-file PATH] [--client-certificate-file PATH] \
  [--client-private-key-file PATH] [--internal-ca-file PATH] [--profile NAME]
markweave composer connections credentials revoke CONNECTION_ID \
  --etag ETAG [--force] [--profile NAME]
markweave composer connections revoke CONNECTION_ID \
  --etag ETAG [--force] [--profile NAME]
```

Creation always leaves the connection disabled. The safe setup order is to create
it, write credentials, enable it with the latest returned ETag, discover the
permitted models, select one with the latest ETag, and then run the bounded test.
Disabled connections do not contact their provider. `show`, mutation, and JSON
output expose only credential-presence flags; they never return secret values.
Personal connections require `--identity-mode individual`. Instance connections
may use either shared or individual identity.

`create --with-credentials` and `credentials rotate` accept an API key through a
non-echoing terminal prompt, or any credential through its `--*-file` option.
Certificate, private-key, and CA files preserve standard multiline PEM content.
Credential files must be regular, owned by the current user, and inaccessible to
other users (`chmod 600`). Paths may appear in command arguments, but secret values
cannot. The CLI reads the active credential byte limit from Composer capabilities
and refuses credential submission when that limit is unavailable. File-based rotation
works with `--non-interactive`; creation also requires `--with-credentials`. Omitted
values remain absent during creation or unchanged during
rotation, and at least one value must be supplied. The CLI does not read Composer
secrets from environment variables, write them to profiles, or include them in output.
Credential input is checked before creating the connection. If the later credential
request fails, the CLI reports the ID of the disabled connection so the operator can
read its current ETag and retry rotation without creating a duplicate.

Administrators manage the explicit grant required before an account, including an
administrator account, can create personal connections:

```text
markweave composer personal-permissions list [--offset N] [--limit N] [--profile NAME]
markweave composer personal-permissions show USER_ID [--profile NAME]
markweave composer personal-permissions grant USER_ID --etag ETAG \
  [--force] [--profile NAME]
markweave composer personal-permissions revoke USER_ID --etag ETAG \
  [--force] [--profile NAME]
```

Grant revocation, credential revocation, and connection revocation prompt for
confirmation. Automation combines global `--non-interactive` with `--force`. Every
conditional mutation requires the exact opaque ETag returned by the preceding read
or mutation so concurrent changes fail safely.

The installed client also covers the owner-scoped T89 draft, conversation,
proposal, and immutable revision foundations:

```text
markweave composer drafts list [--offset N] [--limit N] [--profile NAME]
markweave composer drafts show DRAFT_ID [--profile NAME]
markweave composer drafts create SOURCE [--title TITLE] [--content MARKDOWN] [--profile NAME]
markweave composer drafts handoff JOB_ID [--title TITLE] [--profile NAME]
markweave composer drafts save DRAFT_ID --title TITLE --content MARKDOWN \
  --etag ETAG [--profile NAME]
markweave composer messages list DRAFT_ID [--offset N] [--limit N] [--profile NAME]
markweave composer messages create DRAFT_ID --content TEXT --etag ETAG \
  --idempotency-key KEY [--profile NAME]
markweave composer model-steps start DRAFT_ID CONNECTION_ID \
  {--content-file PATH|--stdin} --max-output-tokens N --etag ETAG \
  --idempotency-key KEY [--force] [--profile NAME]
markweave composer model-steps status DRAFT_ID STEP_ID [--profile NAME]
markweave composer model-steps cancel DRAFT_ID STEP_ID [--force] [--profile NAME]
markweave composer proposals list DRAFT_ID [--offset N] [--limit N] [--profile NAME]
markweave composer proposals show DRAFT_ID PROPOSAL_ID [--profile NAME]
markweave composer proposals decide DRAFT_ID PROPOSAL_ID \
  --state {accepted,edited,rejected} [--value TEXT] --etag ETAG [--profile NAME]
markweave composer revisions capture DRAFT_ID --etag ETAG \
  --idempotency-key KEY [--profile NAME]
markweave composer revisions list DRAFT_ID [--offset N] [--limit N] [--profile NAME]
markweave composer revisions show DRAFT_ID REVISION_ID [--profile NAME]
markweave composer revisions download DRAFT_ID REVISION_ID \
  --kind {download,preview} --output PATH [--force] [--profile NAME]
markweave composer revisions restore DRAFT_ID REVISION_ID --etag ETAG \
  --idempotency-key KEY [--profile NAME]
```

List offsets are non-negative and limits are between 1 and 100; JSON output
preserves the server's `offset` and `limit`. Source creation accepts a non-empty,
regular `.md`, `.docx`, `.pptx`, or `.pdf` file, rejects symlinks and special
files, and sends only its basename. The CLI reads the active Composer upload limit
from service capabilities before loading the source and fails closed if the limit is
unavailable. Conversion handoff remains owner-scoped at the
service. Message creation, source capture, and revision restore require a visible
ASCII idempotency key of at most 128 characters together with the current draft
ETag. The key is safe operation metadata and may be supplied on the command line.

Model-step start first reads the authorized connection and takes its exact endpoint
and selected model. It reads user-authored text from a regular, current-user-only
file (`chmod 600`) or standard input. The text is never passed as a command argument,
read from an environment variable, or included in normal command output. In an
interactive terminal, the CLI displays the destination, model, and exact text for
explicit confirmation. Automation must use `--non-interactive --force`; piped stdin
also requires `--force` because stdin cannot then receive a confirmation. The CLI
uses the service's current model-request and output-token limits and fails closed
when either is unavailable. The service checks the draft ETag, idempotency key, and
connection identity again before transmitting. `status` and `cancel` operate on the
durable step without a live model connection; cancellation requires confirmation or
`--force`. Failed and cancelled steps retain content-free state for review. Model-step
HTTP errors are reported with fixed CLI messages so provider errors cannot echo the
submitted text. Step status and error codes are limited to documented values before
the CLI displays them: `running`, `completed`, `failed`, or `cancelled`; a failed
step has a bounded error code, and only a completed step has a proposal ID.
`capacity_exhausted` identifies an admitted step that could not obtain an execution
slot; the operator can retry with a new idempotency key after capacity returns.

Revision artifact downloads use the authenticated artifact endpoint and write the
response bytes through the CLI's atomic, owner-only destination boundary. Existing
files are preserved unless `--force` is explicit. The client validates the server's
`nosniff` and exact revision headers before publishing any downloaded bytes.

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

## Session-policy inspection

```text
markweave session-policy get [--profile NAME]
```

The human and `--json` forms include effective role durations, the absolute lifetime ceiling,
revision, authoritative per-role minimum/default/maximum bounds, and minute granularity. Clients
must consume these fields instead of duplicating selectable values.

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

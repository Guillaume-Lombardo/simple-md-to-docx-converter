# Command-line interface

`markweave` is the supported command-line program. The initial T31 release fixes
its command names and shared behavior; command families become available through
their assigned tickets.

## Command registry

```text
markweave [--json] [--non-interactive] [--timeout SECONDS] COMMAND

login | logout | whoami | password change
convert | jobs {list,show,wait,cancel,download,manifest,reverse}
templates {list,search,show,create,download,update,replace,archive,delete,versions,version-download,restore,preferred,fallback}
composer {capabilities,policy,connections,personal-permissions,drafts,messages,model-steps,proposals,revisions,authors,fill-templates,fill-plans}
users {list,create,activate,deactivate,reset-password,require-password-change}
audit | health {live,ready,metrics}
serve | worker | doctor | migrate
backup | restore
```

Final containers use this same registry directly: `serve` is the default image command and
distributed worker containers select `worker`. Operational and remote-client command overrides are
passed to `markweave` unchanged after the container runtime preflight.

## Process contract

`--help` and `--version` write to stdout and exit `0`. A successful command writes
only its result to stdout. Expected command errors write one safe error to stderr;
they never include a traceback. Unexpected failures are reduced to the stable
`internal_error` message. The CLI does not print secrets, passwords, session
values, or profile contents.

Composer connection setup accepts private credential file paths, including multiline
PEM material. Revision downloads validate the revision identity and `nosniff`
response headers before replacing a local file. See
[Administration CLI](administration-cli.md#composer-connections) for the full command
forms and security rules.

Composer drafts accept scanned `.md`, `.zip`, `.docx`, `.pptx`, and `.pdf` sources. After reviewing
a successful 2md result, `markweave composer drafts handoff-reversion JOB_ID` imports its
owner-authorized Markdown or ZIP bytes with a fresh scan and immutable source trace. After reviewing
a proposal, `markweave composer proposals publish DRAFT_ID PROPOSAL_ID --etag ETAG
--idempotency-key KEY` publishes approved Markdown as a new immutable revision. Use
`markweave composer revisions publish-draft DRAFT_ID --etag ETAG --idempotency-key KEY`
to publish exact current saved Markdown, including a validated ZIP draft's retained image
assets. Revision `source` artifacts can be downloaded with `--kind source`.
Use
`markweave composer revisions diff DRAFT_ID TO_REVISION_ID --from-revision FROM_REVISION_ID`
for bounded line changes. Native Office proposals are reviewable but cannot be published as
document edits until a qualified structured editor is available.

The author directory and typed Word filling use separate Composer command families:

```text
markweave composer authors {list,show,create,update,grant,revoke}
markweave composer fill-templates {list,show,create,replace,versions,version,download,grant,revoke}
markweave composer fill-plans {list,show,create,update,approve,publish,regenerate}
```

Author fields, typed schemas, values, and provenance are read from private current-user-only JSON
files through `--fields-file`, `--schema-file`, `--values-file`, and `--provenance-file`. Template
creation and replacement also take a local DOCX path. The CLI bounds these inputs using service
capabilities and rejects duplicate JSON keys and non-finite values. Use the exact ETag returned by
`show` or `list` with `--etag` for conditional mutations and an `--idempotency-key` for fill plan
decisions and publication. `fill-plans show` displays durable missing or ambiguous questions;
resolve them through `update` before `approve`. `publish` creates an immutable DOCX revision;
`regenerate` uses the frozen approved inputs and makes no model request. See each command's
`--help` for positional IDs and options.

For a model step, repeat `--author AUTHOR_ID` to select authorized author entries. The CLI
requests an exact prompt preview using the draft `--etag`, checks its digest and author versions,
and shows the complete text for interactive approval before it starts the step. `--force` retains
the existing explicit approval behavior for scripts. The start request binds the preview digest
and exact author versions; a changed or revoked entry fails before model transmission. To resume
after answering a durable question, start a fresh step with `--answered-question-id QUESTION_ID`
and a new ETag and idempotency key. Use `--intent question` to request another question; the
default intent is a proposal. Model text still comes only from `--content-file` or `--stdin`.

Administrators can inspect and replace the Composer destination policy through the
authenticated API:

```text
markweave composer policy show --profile admin
markweave composer policy resolve https://llm.example/v1 --profile admin
markweave composer policy set --enable --allowed-destination https://llm.example:443 \
  --allowed-network 192.0.2.0/24 --if-match '"1"' --profile admin
markweave composer policy set --disable --if-match '"2"' --profile admin
```

`show` returns the policy mode, current allowlists, editability, and ETag. `resolve`
returns the exact destination and addresses proposed for approval. `set` sends a
complete replacement: repeat `--allowed-destination` and `--allowed-network` for
each value, or omit them for empty lists. It requires either `--enable` or
`--disable` and the current ETag in `--if-match`; a concurrent change fails
without overwriting it. In operator-managed mode, the service fixes the
destination and network lists, so supply their current values when changing only
the enabled state. All mutations use the stored session and CSRF value.

| Exit status | Meaning |
| --- | --- |
| `0` | Success |
| `1` | Expected command failure, interruption, or sanitized unexpected failure |
| `2` | Invalid or incomplete invocation |
| `3` | A pre-registered command is unavailable in this release |

`--json` selects compact JSON. Successful JSON goes to stdout and error envelopes
go to stderr, so data and diagnostics remain separately consumable. Human output
is the default. `--timeout SECONDS` accepts only a positive finite value and is
passed to the selected command; no global timeout default is imposed by T31.
`--non-interactive` tells implemented commands to fail instead of prompting.

Remote command families use only the documented HTTP API. Authentication uses
non-echoing password prompts and owner-only XDG profile persistence; passwords are
never command arguments. Runtime and recovery commands are the only families
allowed to access runtime or storage services directly.

## Reverse-conversion jobs

Reverse conversion extends the stable `jobs` family without adding a backend or object-store
shortcut to the installed client:

```text
markweave jobs reverse capabilities --profile work
markweave jobs reverse submit report.docx --idempotency-key report-42 --profile work
markweave jobs reverse submit edited-deck.pptx --extraction slides --profile work
markweave jobs reverse submit edited-deck.pptx --extraction marp --no-include-notes --profile work
markweave jobs reverse list --limit 50 --profile work
markweave jobs reverse show JOB_UUID --profile work
markweave --timeout 300 jobs reverse wait JOB_UUID --poll-interval 2 --profile work
markweave jobs reverse cancel JOB_UUID --profile work
markweave jobs reverse download JOB_UUID ./report.md --profile work
```

Submission first reads the authenticated capabilities endpoint. The client accepts only an
extension advertised by that response, rejects a non-regular, empty, symlinked, or oversized file
before submission, and still leaves content detection and malware scanning to the server. It sends
only the source basename, because the owner-visible job contract retains the safe original stem;
it never sends a local directory path. `--retries` repeats only ambiguous network failures and
requires the same explicit idempotency key.

For a `.pptx` advertised by the capability response, `--extraction slides` requests
slide-oriented Markdown and `--extraction marp` requests the documented Marp output. The connected
service advertises the defaults for presenter notes and images; use `--no-include-notes` or
`--no-include-images` only for a structured PowerPoint mode. The default `--extraction anydoc`
keeps the existing document extraction behavior and requires both options to remain enabled. When
an option is omitted, the CLI
uses the connected service's advertised default. The CLI freezes all three values
with the idempotency key when retrying, so a retry cannot change the original request.

Reverse listing, status, cancellation, and result download remain owner-only even for global
administrators. Waiting requires the global positive `--timeout`. Downloads use the same private,
atomic, no-symlink destination boundary as forward results; the caller chooses `.md` or `.zip`
from the job's `result_mode`, and existing files are preserved unless `--overwrite` is explicit.

`capabilities` displays the server's versioned format and admission contract. `--json` emits compact
JSON on success; human-readable output is the default. Reverse command failures use the CLI process
contract above, and service errors include their stable code and correlation identifier. Waiting
reports a timeout if the job does not finish within `--timeout`; terminal failure, cancellation, or
expiration is reported as an error. Use separate named profiles for separate services or accounts;
each profile retains its own session credentials.

The advertised extensions and upload limit come from the service. Submission checks the local file
and then relies on server-side scanning and content detection. The workflow uses a local engine,
without OCR or hosted Firecrawl fallback. PDF text is extracted without its images or layout;
scanned or image-only PDFs fail with `needs_ocr`. Reverse conversion remains experimental; see the
[reverse qualification evidence](evidence/t73-reverse-conversion-qualification.md).

## Authentication profiles

`login`, `logout`, `whoami`, and `password change` use the documented HTTPS API.
Start a profile with the exact HTTPS service URL, then select it by name when
needed:

```text
markweave login --url https://converter.example --username alice --profile work
markweave whoami --profile work
markweave password change --profile work
markweave logout --profile work
```

The profile defaults to `default`. Login prompts for a password through the
terminal's non-echoing path; it has no password option and rejects password-like
arguments. `--non-interactive` fails rather than reading any prompt. The profile
keeps only the HTTPS base URL, the opaque session-cookie pair, and its CSRF value
in `$XDG_STATE_HOME/markweave/profiles` (or `~/.local/state` when unset). Each
file is atomically replaced with mode `0600`; the profile directory is owner-only.
Passwords are never written, displayed, or accepted from environment variables.

TLS verification is always enabled. The sole evaluation exception is a literal
loopback URL, `http://127.0.0.1` or `http://[::1]`, for the rootless final-image
workflow; `localhost` and all other HTTP hosts are rejected. The default session
cookie name is `md_converter_session`; use `--session-cookie-name` only when the
remote service has explicitly configured a different session-cookie name.

`password change` is available only for a restricted password-renewal session. It
prompts for the current password, new password, and confirmation, verifies the
current password through a fresh restricted session, sends the CSRF-protected
renewal request, removes the local profile, and requires a fresh login.

## Templates, versions, and preferences

Template commands use the authenticated HTTP API associated with `--profile`;
they never open the service database, object store, or local runtime. Active
templates are visible to every authenticated account. Archived identities and
their immutable versions remain visible only to their owner and administrators,
and the service remains authoritative for every owner or administrator mutation.

Discovery and immutable downloads use explicit paths:

```text
markweave templates list --limit 50 --profile work
markweave templates search --name finance --status active --profile work
markweave --json templates show TEMPLATE_UUID --profile work
markweave templates download TEMPLATE_UUID --output finance.docx --profile work
markweave templates versions TEMPLATE_UUID --profile work
markweave templates version-download TEMPLATE_UUID VERSION_UUID --output finance-v1.docx --profile work
```

Downloads require `--output`, validate the service's SHA-256 ETag before writing,
and use an atomic same-directory replacement. An existing path is preserved unless
`--force` is supplied. A local upload must be a non-empty regular file; symlinks
and other special files are rejected, and its local filename is never included in
the multipart request.

Creation and replacement require every expected font as a repeated `--font`
option:

```text
markweave templates create --name Finance --description Quarterly \
  --file reference.docx --font Calibri --font Cambria --font "Courier New" --profile work
markweave templates replace TEMPLATE_UUID --file reference-v2.docx \
  --font Calibri --font Cambria --font "Courier New" --profile work
```

`show`, `create`, `update`, `replace`, `restore`, and `archive` include the current
identity `etag` in JSON output. Conditional mutations accept that exact value with
`--etag`. When it is omitted, the CLI performs a fresh visible-identity read and
sends the returned ETag in `If-Match`; it never submits an unconditional mutation.
A stale explicit ETag therefore produces the service's conflict response without
silently retrying or overwriting another update.

```text
markweave templates update TEMPLATE_UUID --name "Finance 2027" \
  --description "Approved 2027 styles" --etag '"template-TEMPLATE_UUID-3"' --profile work
markweave templates restore TEMPLATE_UUID VERSION_UUID --etag '"template-TEMPLATE_UUID-4"' --profile work
markweave templates archive TEMPLATE_UUID --etag '"template-TEMPLATE_UUID-5"' --profile work
markweave templates delete TEMPLATE_UUID --etag '"template-TEMPLATE_UUID-6"' --profile work
```

Archive and permanent deletion prompt for confirmation. Automation must combine
the global `--non-interactive` option with the command's `--force` flag. Deletion
still requires an archived identity and is rejected by the service while a user
preference, system fallback, or conversion job references any immutable version.

Each user can set or clear their own preferred template. Only an administrator can
set the singleton system fallback:

```text
markweave templates preferred --template-id TEMPLATE_UUID --profile work
markweave templates preferred --clear --profile work
markweave templates fallback TEMPLATE_UUID --profile admin
```

Authenticated read commands expose the same authoritative runtime metadata used by browser
clients. `markweave conversion-options --profile work` reports the configured conversion upload
limit and resolved immutable template/version/source. `markweave templates context --profile
work` reports the current preference, system fallback, and configured template archive limit.
Both support the global `--json` output for automation. `markweave session-policy get` and update
output also includes the exact operator-configured absolute lifetime in seconds.

These commands preserve the authenticated actor carried by the stored session, so
audit attribution and administrator-intervention evidence are identical to the web
and direct API workflows. HTTP authorization failures and validation failures use
the service's safe error code and message; uploaded bytes, local filenames, session
state, and CSRF state are never rendered.

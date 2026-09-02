# Command-line interface

`markweave` is the supported command-line program. The initial T31 release fixes
its command names and shared behavior; command families become available through
their assigned tickets.

## Command registry

```text
markweave [--json] [--non-interactive] [--timeout SECONDS] COMMAND

login | logout | whoami | password change
convert | jobs {list,show,wait,cancel,download,manifest}
templates {list,search,show,create,download,update,replace,archive,delete,versions,version-download,restore,preferred,fallback}
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

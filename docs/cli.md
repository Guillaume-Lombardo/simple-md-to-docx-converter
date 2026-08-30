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

`python -m markweave.runtime` remains the package-internal compatibility path for
the existing container worker modes until T36 and T38 migrate them to this CLI.

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

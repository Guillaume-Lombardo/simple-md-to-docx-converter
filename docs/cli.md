# Command-line interface

`markweave` is the supported command-line program. The initial T31 release fixes
its command names and shared behavior; each listed command currently reports that
it is unavailable until its assigned command-family ticket implements it.

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

Remote command families will use only the documented HTTP API. T32 will add
non-echoing password prompts and owner-only XDG profile persistence; passwords are
never command arguments. Runtime and recovery commands are the only families
allowed to access runtime or storage services directly.

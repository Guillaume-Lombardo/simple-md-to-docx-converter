# Markweave Web application

This directory contains the Next.js presentation application introduced by T60, the authentication
shell implemented by T61, and published as Markweave's browser interface by T64. FastAPI remains
the authentication and API authority. The frontend process has no backend credentials or
persistence, and browser code calls relative `/api/v1` URLs directly.

The browser loads its current principal from `/api/v1/session`. Login, logout, and password
renewal go directly to FastAPI through the same origin. The application never reads the HttpOnly
session cookie; it copies only the readable CSRF cookie into authenticated mutation requests.
There is no session polling or client security countdown: FastAPI decides expiry, and one
authoritative `401` clears stale browser state without replaying a mutation. Fixed navigation
destinations prevent open redirects. The shell displays the effective role-specific inactivity
duration returned by session inspection, including administrator changes.

The experimental `/revert` workspace reads its versioned upload ceiling and format hints from the
authenticated FastAPI capabilities endpoint. It submits and polls reverse jobs through relative
same-origin URLs, keeps an idempotency key stable after an ambiguous submission result, and never
replays a mutation automatically. Reverse conversion is local, CPU-only, and low-compute; it does
not provide OCR or use a hosted fallback. T73 owns the final successful two-profile reverse browser
workflow against the complete release image set.

If Revert reports that its service or configuration is unavailable before a file is selected,
check the reverse-service deployment, not the document uploaded in Convert. The two workspaces
keep separate source selections. The default 0.6.2 Compose quickstart does not configure the
optional reverse service; see the [reverse configuration](../docs/configuration.md) before enabling
it. File-type validation occurs only when submitting a document in Revert.

The `2docx` (`/convert`) and `2md` (`/revert`, Experimental) tabs are independent workflows: neither transfers its selected file, submission,
job status, or errors to the other. Navigating between them never submits a file. Each upload goes
only to its own API endpoint, and late responses from a departed workspace cannot update the
current one.

The `templates` tab continues to open `/templates`; its permissions are unchanged.

Use the reviewed Node.js 24.19.0, Corepack 0.36.0, and pnpm 11.25.0 workspace
toolchain from the repository root:

```bash
pnpm install --frozen-lockfile --ignore-scripts
pnpm --filter @markweave/web run bindings:check
pnpm --filter @markweave/web run check
pnpm --filter @markweave/web run build
pnpm --filter @markweave/web run test:production
```

Regenerate both the production bindings and test fixture from the canonical contract with
`pnpm --filter @markweave/web run bindings:generate`. Never edit files under either `src/api/generated/` or
`tests/fixtures/generated/` manually.

The custom production server listens for pages on port 3000 and internal probes on port 3001. The
rootless smoke test is `bash web/scripts/run-rootless-smoke.sh` from the repository root. It builds
the digest-pinned UBI image and checks an arbitrary UID, read-only root, empty capabilities, bounded
resources, page serving, and both private probes. T64 owns the completed production cutover and
rollback contract; later frontend-only development must not alter production routing.

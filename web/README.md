# Markweave Web application

This directory contains the unpublished Next.js presentation application introduced by T60 and
the authentication shell implemented by T61. The FastAPI-rendered pages remain the production
browser interface until T64. This process has no backend credentials or persistence and browser
code calls relative `/api/v1` URLs directly.

The browser loads its current principal from `/api/v1/session`. Login, logout, and password
renewal go directly to FastAPI through the same origin. The application never reads the HttpOnly
session cookie; it copies only the readable CSRF cookie into authenticated mutation requests.
There is no session polling or client security countdown: FastAPI decides expiry, and one
authoritative `401` clears stale browser state without replaying a mutation. Fixed navigation
destinations prevent open redirects. The shell displays the effective role-specific inactivity
duration returned by session inspection, including administrator changes.

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
resources, page serving, and both private probes. Production routing is deliberately not changed
by T60.

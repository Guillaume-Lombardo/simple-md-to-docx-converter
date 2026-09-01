# Markweave Web foundation

This directory contains the unpublished Next.js presentation foundation introduced by T60. The
FastAPI-rendered pages remain the production browser interface until T64. This process has no
backend credentials or persistence and browser code calls relative `/api/v1` URLs directly.

Use the reviewed Node.js 24.19.0 and npm 11.17.0 toolchain:

```bash
npm ci --ignore-scripts
npm run bindings:check
npm run check
npm run build
npm run test:production
```

Regenerate both the production bindings and test fixture from the canonical contract with
`npm run bindings:generate`. Never edit files under either `src/api/generated/` or
`tests/fixtures/generated/` manually.

The custom production server listens for pages on port 3000 and internal probes on port 3001. The
rootless smoke test is `bash web/scripts/run-rootless-smoke.sh` from the repository root. It builds
the digest-pinned UBI image and checks an arbitrary UID, read-only root, empty capabilities, bounded
resources, page serving, and both private probes. Production routing is deliberately not changed
by T60.

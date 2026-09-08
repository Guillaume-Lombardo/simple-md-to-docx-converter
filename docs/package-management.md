# JavaScript package management

The root browser-test package and `web/` form one pnpm workspace with one
`pnpm-lock.yaml`. `spikes/toolchain` is deliberately outside that workspace and continues to use
its reviewed npm lock and exact Mermaid production graph.

## Reviewed bootstrap

JavaScript application and test work uses Node.js `24.19.0`, Corepack `0.36.0`, and pnpm
`11.25.0`. The versions were selected from their official npm registry metadata on 2026-09-03.
The bootstrap downloads the immutable Corepack tarball URL
`https://registry.npmjs.org/corepack/-/corepack-0.36.0.tgz`, verifies its published integrity
`sha512-SiiJsBhZqdBiPHTEl6OT3sASrRrKIcYTQMsVGXx6EE/gM8WFMwYjeIX8Tt8RiU4Iv2J6LbT8KpGfCOsBpRWB/w==`,
and installs that local verified file. The root `packageManager` value binds pnpm's registry bytes
with SHA-224 `c69bc375107d8eef668fbe1ebab8b3a34253dc594dff6a0a36d8a16c`. Network access is disabled after
that explicit activation, so a missing package-manager artifact fails instead of being fetched
implicitly.

```bash
scripts/javascript/bootstrap-pnpm.sh "$PWD/.pnpm-tools"
export PATH="$PWD/.pnpm-tools/bin:$PATH"
export COREPACK_HOME="$PWD/.pnpm-tools/corepack-home" COREPACK_ENABLE_NETWORK=0
pnpm install --frozen-lockfile --ignore-scripts
pnpm run workspace:check
```

The root overrides preserve the exact npm-baseline transitive versions. Five exact WASM fallback
packages are explicit development dependencies because pnpm otherwise omits dependencies of the
CPU-specific optional package from its cross-platform lock. This keeps the package/version set
identical to the two retired npm locks. Install scripts remain disabled.

CI caches only pnpm's content-addressable store. Keys contain the runner OS, exact Node and pnpm
versions, and the root lock digest. Pull requests and merge queues restore caches but cannot write
them; only a trusted push to this repository's `main` may save one. A cache miss always falls back
to a frozen install.

The frontend builder uses the repository root as its build context, installs from the frozen root
lock, and uses `pnpm deploy --prod --legacy` to copy a portable production graph. The runtime image
receives only that graph and the application build; Corepack, pnpm, and their caches remain in the
discarded builder.

## Isolated Mermaid toolchain

Do not migrate or update `spikes/toolchain` as part of workspace maintenance. Continue to verify
its lock digest and execute:

```bash
npm ci --prefix spikes/toolchain --omit=dev --ignore-scripts
```

## Rollback and benchmark evidence

A T67 rollback reverses the complete, reviewed T67 candidate series from its exact npm parent. It
must restore both npm locks,
the `npm@11.17.0` frontend manager metadata, npm CI caches and commands, and the frontend's `web/`
build context while removing every pnpm/Corepack workspace surface. Before merging a rollback,
run the rehearsal with the exact candidate and the direct npm parent of its first T67 commit on
Node.js `24.19.0` and npm `11.17.0`:

```bash
scripts/javascript/rehearse-npm-rollback.sh '<T67-candidate>' '<T67-migration-commit>^'
```

The last pre-migration lock digests are root
`7fc4db9135c474c8fe4f48dc60028a10df9904fb4d918f728f6fe3f19fca1061` and frontend
`3dbff3f758ee4367dc5e7f70889d269798a4c87092c38dc418a200ae124285b1`. Historical release-evidence
recovery selects `pnpm-lock.yaml` when present at the release source SHA and otherwise binds the
old `web/package-lock.json`, so retained npm-era releases remain recoverable.

Hosted benchmark evidence must record the `ubuntu-24.04` runner image, Node version, exact command,
three cold and three warm samples, cache archive size, workspace `node_modules` and store disk use,
frontend build time, and final frontend image size for both the npm parent and pnpm candidate.
Keep raw step logs with the pull request. A material regression stops delivery until a reviewer
explicitly approves it; this project does not invent a threshold after observing results.
The T67 pull request's frontend job runs `scripts/javascript/benchmark-package-managers.sh` against
the immutable npm baseline and reviewed pnpm candidate, then retains its environment, timing, disk,
compressed-cache, image-size, manifest/lock digest, and raw command output for 30 days. The step is
restricted to the repository-owned T67 branch and cannot burden later frontend pull requests.

A local rootless Podman diagnostic (not a substitute for hosted evidence) built the npm baseline
at `1,061,525,142` bytes and the target-platform pnpm candidate at `1,033,797,849` bytes. The
candidate passed the arbitrary-UID/read-only-root smoke test, contained `next`, excluded TypeScript
from its production graph, and contained neither Corepack nor pnpm. The first deliberately
cross-platform deploy experiment was rejected because it produced a `2,705,797,855`-byte image;
the final configuration keeps cross-platform integrity records in the lock while deploying only
the builder's target-platform production graph.

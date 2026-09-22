# JavaScript package management

The root browser-test package and `web/` form one pnpm workspace with one
`pnpm-lock.yaml`. `toolchain/document-engines` is deliberately outside that workspace and continues to use
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

Do not migrate or update `toolchain/document-engines` as part of workspace maintenance. Continue to verify
its lock digest and execute:

```bash
npm ci --prefix toolchain/document-engines --omit=dev --ignore-scripts
```

## Rollback and benchmark evidence

The T67 npm-to-pnpm migration is complete. Its one-time rollback rehearsal, baseline lock digests,
benchmark requirements and measured image sizes are retained in the
[historical migration record](evidence/release-migration-history.md#t67-package-manager-migration).
T80 removed the migration-specific CI steps; current installations use the reviewed bootstrap above.

Historical release-evidence recovery selects `pnpm-lock.yaml` when present at the release source
SHA and otherwise binds the old `web/package-lock.json`. Retained npm-era releases remain
recoverable without changing the current workspace or rebuilding their images.

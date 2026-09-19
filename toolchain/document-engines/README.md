# Document engine toolchain

This directory contains production build inputs and their validation harnesses: the isolated
Mermaid npm graph, Chromium seccomp policy, fonts, licenses, and engine validation fixtures.
The application Containerfile, Compose profiles and CI consume these files directly.

T80 moved this directory from `spikes/toolchain` without changing engine versions or lockfile
bytes. The path now reflects its production role. The historical T00/T67 records and migration
rehearsal scripts retain their original paths when referring to immutable old commits.

This npm project remains outside the root pnpm workspace. Install its frozen production graph
from the repository root with:

```bash
npm ci --prefix toolchain/document-engines --omit=dev --ignore-scripts
```

Keep the sandbox enabled and preserve the reviewed hashes, font/license inventory and independent
lockfile. Validation scripts in this directory resolve their own location; run them according to
[the engine validation guide](../../docs/t00-toolchain-validation.md).

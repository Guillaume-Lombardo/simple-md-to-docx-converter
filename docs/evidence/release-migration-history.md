# Release and migration history

Historical snapshots moved from the active guides on 2026-09-22. Their instructions and
completion claims describe the release named in each section, not the current deployment.
Use the [release process](../releasing.md), [upgrade guide](../upgrading.md), and
[capability matrix](../index.md#conversion-capabilities) for current instructions.

Version `0.3.0` was the first approved public release. T64 completed parity and its one-time
pre-removal rollback rehearsal; the `0.6.1` continuation source satisfied that gate.

## Release incidents and receipts through 0.7.3

The historical skipped-container exception covers the failed `0.6.0` cutover publication:
PyPI and the exact GitHub tag/Release exist at the reviewed `0.6.0` base source, Compose remains on
the `0.5.2` digest verified against its publication receipt and anonymous GHCR manifest bytes, and
no container staging artifact was created. Both backend and frontend repositories must return the
exact bounded structured `MANIFEST_UNKNOWN` response for the requested `0.6.0` tag. The check uses
anonymous pull first. Only an exact anonymous `DENIED` response from the historical frontend
repository may fall back to the ephemeral GitHub Actions identity with `packages: read`; backend
denial never does. Credentials are populated only for a trusted push or a pull request whose head
repository exactly matches this repository. Fork and merge-group validation therefore fail closed
when this one-time exception is needed and require successful trusted same-repository pull-request
validation. A missing credential, authenticated denial, existing private tag, malformed, oversized,
or unrelated response does not prove absence. Only the normal protected `0.6.1` pending transition
may pass that state. It performs a new ordinary paired release; it does not rebuild or recover
`0.6.0`.

That exception is now historical: protected release run `33725900729` published the paired `0.6.1`
images from source `78cb86d450e940a3190591de62ee0ebade216d8b`, and the separate adoption change pins both verified
registry digests in Compose, the quickstarts, and the durable cutover evidence. Normal fully aligned
public-release checks apply after adoption.

The second bounded exception covers the incomplete `0.7.2` release. Its PyPI package and final
GitHub tag/Release are bound to source `b28256486af4cf6d58c3aa06a27815c321df57f9`.
Automatic run `35723245369` built the three images and passed their supply-chain checks and the
complete standalone qualification. Distributed broker-restart qualification then rejected a
fault-injection observer that selected a different synthetic attempt. The strict identity guard
failed closed before crash injection. This is a harness defect, not evidence of a successful
release qualification; the exact runtime interleaving was not retained.

Registry login, publication and pre-mutation artifact retention were skipped. The run retained
only `python-release-v0.7.2`; no staged image bytes are available for the recovery dispatch.
Do not rebuild or republish `0.7.2`. The approved continuation corrects the harness and permits
only the exact `0.7.2` to `0.7.3` pending transition. Its checks bind the failed run, source, Python
artifact, tag/Release and absence of container evidence; verify the current `0.7.1` public receipts;
and require structured absence of both the version and source tags in all three GHCR repositories.
An authorization denial alone never proves absence. Only the frontend and reverse-attempt
repositories may use the authenticated fallback with the existing ephemeral upstream identity;
backend anonymous denial rejects the exception. The fallback still requires the exact missing-
manifest response. Untrusted or unverifiable states fail closed. A successful `0.7.3` release must publish
one fresh matched three-image set and adopt its actual public receipts before unrelated work.

Protected release run `35747017469` completed that continuation from source
`84e35fed521c61d34823d18767f47eb87253d4eb`. Its schema-2 manifest binds the published backend
digest `sha256:c91f97d7c299ad84811876e52ae52350d1ad3b91bd190b4fffb801634729bc83`, frontend digest
`sha256:edee507cf70d15681bae0fe7f9d0755b607d557725fd06e6bd43771350dba9ba`, and reverse-attempt digest
`sha256:39f4a68358029977b6ec5ac6cb26fab88ac10e518016b42e8389abd54f517e61` to that source and version
`0.7.3`. Anonymous requests for each role's version and source tags returned the matching digest,
and public PyPI verification passed. The follow-up repository pin adoption change adopts the exact
published receipts; deployment-specific verification remains a separate required step. The
incomplete `0.7.2` history above is unchanged.

The `0.6.2` patch followed that normal paired-release path. Protected release run `34648944379`
published the Python artifacts and paired images from source
`d7188c4fd3d9c7d4f1d82995850b3827f09e3a83`. The separate adoption change pins the exact retained
backend digest `sha256:30c9fa538e7c4eb56b1b1434cd14251ee82b3a3962ad1a072fe5564a118330ae`
and frontend digest `sha256:8908a28dea630ef520eb60828d6717c46fdc445c8878e4bacc6b239ccb32c2e7`
in Compose and both quickstarts.

Protected release run `35454910249` published `0.6.3` from source
`5e789c11600d429997c037b4d24eea399821a2ed` after PR #232 passed all required checks.
The backend receipt records
`sha256:6560d86e4ca33327a5f454530c8b6a2fadb20b55af6373aea01378b00920bb4e`, and the
frontend receipt records
`sha256:bb60b8b72259d738c4eb93c29cf54aef9a8cf2f875e5851ebdc063ded5c99fa5`.
Both anonymous registry manifests, receipt hashes, frontend lockfile binding and GitHub
provenance match the release source. Compose and both quickstarts adopt these exact digests;
the quickstart instructions use the released `md 2 docx` and `template docx` navigation labels.

## PowerPoint-era release adoption

## 0.6.4 adoption before the 0.7.0 transition

The published 0.6.4 pair is adopted while preparing 0.7.0 to correct the previously
stale 0.6.3 Compose references. Both release receipts identify source
`7e3d4eeb4ad8b1346d9b4c855999624d6e2e0436`. Backend registry digest:
`sha256:89a1eb87a87735441feb4d8dfee46bc597e948873ccd1808065f7d92acde19d5`.
Frontend registry digest:
`sha256:9d113f3d7614e7e1dcbda45834b476179d9a8113909ccc96b9e3134e3a9f3b9e`.
The ordinary pending-transition gate verifies these exact public receipts and
anonymous registry manifests; no unpublished 0.7.0 image is pinned.

## 0.7.0 publication and image adoption

[PR #244](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/pull/244)
merged source `d0c150f625cce9e51102904ab9289721d6dd7054` after independent review and
all required checks. [Release run 35519480791](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/actions/runs/35519480791)
successfully published the exact verified wheel/sdist and paired images for
[0.7.0](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/releases/tag/v0.7.0).

The backend receipt records
`sha256:92037e2937878f02039c8e41942cf8542000a46037c1488297a13aa554983c69`;
the frontend receipt records
`sha256:67167d78393725c964804d9b1e62d35fe4a7460f2e2afec411bdf1f8349ea4d1`.
Both receipts, the release manifest and frontend lockfile bind to the same source.
Their attached checksum manifests were verified, and anonymous GHCR/PyPI alignment
is required before adopting them in Compose and both quickstarts. The final rootless
pair passed both storage-profile acceptance suites before publication; the workflow
published those staged bytes without rebuilding and attached SBOM/provenance evidence.

This release includes editable PowerPoint generation, native Pandoc defaults without
a template, typed presentation templates, stable progress/download geometry and the
Markweave logo/favicon. Slide-oriented reverse Markdown/Marp extraction remains T83.
The forward enum expansion is the explicit 0.6.4-to-0.7.0 exception documented in
[PowerPoint compatibility](../powerpoint.md).

Light CI now separates rapid checks, two complementary Python partitions and aggregate
coverage. Final hosted partitions passed in 8m13s/7m29s, with aggregation in 14s;
all 4,042 selected cases were accounted for. Local aggregate coverage was 90.10% of
branches and 95.86% of changed application lines. Trusted main successfully populated
both the lock-keyed uv cache and the Next.js compiler cache; PRs only restore them.


## 0.7.1 publication and image adoption

[PR #247](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/pull/247)
merged the responsibility-based application decomposition as
`5dd3328a17d12279d1c7c11a8c96b68cb062ecb3`. Independent CodeRabbit review found no
actionable issues at the exact submitted head. All 17 PR checks and the
[main CI run](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/actions/runs/35537766132)
passed, including document engines and final-image E2E for both storage profiles.

[Automatic release 35537766223](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/actions/runs/35537766223)
completed successfully and published [0.7.1](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/releases/tag/v0.7.1)
to PyPI and the paired backend/frontend GHCR repositories. A clean install from
public PyPI reports 0.7.1. The tag, release manifest, receipt hashes, attached
evidence checksums and frontend lockfile all bind to the same reviewed source.

The backend registry receipt records `sha256:10c84da1e783e86f56163c6bb639008436d6878462ac63e62af8a28129599a93`;
the frontend receipt records `sha256:dad65e90b56923fb38df3fc985b51fb20ef40422ff8ccd2aae2ece1b17515082`.
Compose, both quickstarts and the published-image E2E use these exact digests.
The release tested the exact pair in both storage profiles before publishing it
without rebuilding, then attached SBOMs and provenance attestations. No API,
database, dependency or quickstart-command migration is required for this patch.

Post-publication checks confirm anonymous PyPI/GHCR alignment. The real rootless
Podman insecure quickstart E2E passed with this published pair, verifying running
container digests, browser/API routing, host validation, teardown, restart and
preserved evaluation state. Documentation, quickstart and public-alignment tests
passed (96). The disposable E2E services and volumes were cleaned up.

[Adoption PR #249](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/pull/249)
merged as `355b92c298b92c9cacfda37e353eaacf5ef73c35` after independent review and
[all required checks](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/actions/runs/35539772814)
passed. Public release alignment passed again on this exact main commit. T84 is
complete: every application Python module remains below 1,000 lines, with a
maximum of 989, and public package/image defaults agree on 0.7.1.

## CLI entrypoint release transition

For the `0.5.0` transition, Compose also catches up
   from `0.3.5` to the already-published immutable `0.4.0` image while retaining its existing
   `embedded-worker` command. This bounded correction restores the required base/PyPI/Compose
   alignment before publication. The completed post-publication phase pins `0.5.0` from its
   retained receipt and advances the public role to `markweave serve`.

## Incomplete 0.7.2 container release

The `0.7.2` Python package was published, but its container qualification failed before images
were published or retained. It is not a complete container release; never invent `0.7.2` image
digests or mix package and image versions. The matched schema-2 `0.7.3` backend, frontend, and
reverse-attempt images are published from one source and have verified public receipts. Existing
deployments should keep their verified `0.7.1` configuration until they adopt the repository's
exact `0.7.3` pins and complete deployment-specific verification.

## Next.js cutover and legacy rollback

A release containing the T64 cutover adds a separately published frontend
image but remains one Markweave release. Before rollout, verify the exact
matched backend and frontend registry digests, the pair-binding release
receipt, the previous backend digest containing the legacy interface, and the
reviewed previous and target routing manifests. Mixed frontend/backend versions
are unsupported even when their HTTP schemas appear compatible.

Cut over only after the previous profile-consistent backup and the complete
two-profile evidence against the exact published final bytes is available. The
final backend bytes are built only after parity and rollback rehearsal complete
and the candidate source has removed the legacy renderer; they are not rebuilt
after acceptance. If routing or the frontend
fails before any persistent transition, stop admission and restore the previous
routing manifest and previous backend release with its legacy pages. If a
database migration or persistent data change has started, restore the matching
pre-cutover database and object backup into isolated targets before switching
traffic back. In either case, require frontend-route or legacy-page availability,
FastAPI readiness, login, one authorized workflow, and representative stable
object/download checks before declaring rollback complete. The detailed route
and rehearsal contract is in
[the Next.js migration architecture](../nextjs-migration-architecture.md).

## Earlier recovery identities

The former `0.6.4` candidate and `0.6.1`
rollback references document an earlier backend/frontend pair transition. Keep that record as
historical release evidence; those versions do not select a current release or public digest for
T73.

## Retired runtime entrypoint guidance

`python -m markweave.runtime` remains a package-internal compatibility path for
existing container entrypoints until T38 migrates them to the supported CLI.

## PowerPoint 0.7.0 API exception

The user explicitly approved the 0.6.4 to 0.7.0 response enum expansion on
2026-09-20. Conversion responses and history can now return `pptx` and
`pptx-bundle` in `output`. Clients that exhaustively validate the former
`docx|pdf|both` set must regenerate their bindings or accept these two values
before upgrading. Existing document requests and output formats remain supported.

The OpenAPI gate still classifies this as incompatible. A narrowly scoped
exception accepts only these exact old/new versions and enum sets; unrelated
changes and later transitions remain blocking. The exception is visible in CI
output and is not a general permission to widen response enums.

## T67 package-manager migration

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
The completed T67 pull request's frontend job ran `scripts/javascript/benchmark-package-managers.sh` against
the immutable npm baseline and reviewed pnpm candidate, then retained its environment, timing, disk,
compressed-cache, image-size, manifest/lock digest, and raw command output for 30 days. T80 removes those branch-specific workflow steps and their manual input after the migration.
The historical scripts and immutable baseline identifiers remain available for evidence review;
they intentionally reference the old `spikes/toolchain` path at those historical commits.

A local rootless Podman diagnostic (not a substitute for hosted evidence) built the npm baseline
at `1,061,525,142` bytes and the target-platform pnpm candidate at `1,033,797,849` bytes. The
candidate passed the arbitrary-UID/read-only-root smoke test, contained `next`, excluded TypeScript
from its production graph, and contained neither Corepack nor pnpm. The first deliberately
cross-platform deploy experiment was rejected because it produced a `2,705,797,855`-byte image;
the final configuration keeps cross-platform integrity records in the lock while deploying only
the builder's target-platform production graph.

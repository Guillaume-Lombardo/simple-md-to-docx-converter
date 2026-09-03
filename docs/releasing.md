# Release process

Version `0.3.0` is the first approved public release. `project.version` in `pyproject.toml` is the
authoritative PEP 440 version, and every release tag is derived as `v<project.version>`. The source
and `markweave` distribution are Apache-2.0 licensed, the public import is `markweave`, and the
container repository is `ghcr.io/guillaume-lombardo/md-converter`.

A protected pull-request merge to `main` is the sole human release gate. When that merge changes
`project.version`, the automatic workflow compares the exact before and head commits from the
trusted push. A `pyproject.toml` edit that leaves the version and the positive integer
`tool.markweave.release.attempt` unchanged is a successful no-op. After an infrastructure failure
leaves a release run impossible to rerun and creates no tag, GitHub Release, PyPI version, or GHCR
version tag, a protected recovery pull request may increment `attempt` by exactly one to retry the
same final version. The next version transition must reset it to `1`. Decreases, skipped attempt
numbers, and stale attempts on a new version fail closed. Pull requests, forks, tags, and GitHub
Release events cannot start publication. Manual dispatch cannot publish Python artifacts; it is
reserved for the container-only recovery described below.

The read-only CI workflow also verifies the public release identity through fixed HTTPS endpoints.
In the normal state, `project.version`, the latest PyPI version, the Compose image tag, the
published GitHub tag and Release receipt, and the anonymous GHCR manifest digest must all agree;
the Compose reference must include that immutable digest. The only exception is an exact version
transition on a pull request, merge-group candidate, or trusted `main` push: the base project,
PyPI, and Compose versions must still agree, the new project version must be higher, and all public
evidence for the base version must remain valid. Scheduled, Release, and manual runs never receive
this exception. A later revision at the unchanged new version also fails until the published image
has been adopted.

The sole historical skipped-container exception covers the failed `0.6.0` cutover publication:
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

## GitHub and PyPI trust configuration

Keep the GitHub Actions environment `pypi` without required reviewers, wait timers, deployment
restrictions, or environment secrets. It is only the OIDC Trusted Publisher identity boundary.
Configure the PyPI Trusted Publisher with these exact values:

- PyPI project name: `markweave`
- GitHub owner: `Guillaume-Lombardo`
- GitHub repository: `simple-md-to-docx-converter`
- Workflow: `release.yml`
- Environment: `pypi`

No PyPI token belongs in GitHub. The `markweave` project now has the active publisher created by its
first trusted upload. If the repository, workflow, environment, or PyPI publisher identity changes,
update and verify both ends before merging a version transition. The `md-converter` GHCR package is
public; verify anonymous pull access after every release and do not grant the package to unrelated
repositories.

## Prepare a version release

1. Change only the intended release version in `project.version` and the matching application
   version source. Use canonical final public PEP 440 syntax. Pre-releases, development releases,
   local versions, epochs, invalid spellings, version downgrades, and mismatched application
   versions and transitions with equal PEP 440 precedence fail closed. Reset
   `tool.markweave.release.attempt` to `1`. For the `0.5.0` transition, Compose also catches up
   from `0.3.5` to the already-published immutable `0.4.0` image while retaining its existing
   `embedded-worker` command. This bounded correction restores the required base/PyPI/Compose
   alignment before publication. The completed post-publication phase pins `0.5.0` from its
   retained receipt and advances the public role to `markweave serve`.
2. Open a pull request and require the complete `CI / gate`, independent review, and protected
   merge to `main`. Release versions are derived dynamically and are not hardcoded in the
   workflows.
3. After merge, the workflow verifies the exact `github.event.before` and `github.sha` pair. It
   stops if `v<version>` already exists, a GitHub Release already uses that tag, or PyPI already has
   that exact `markweave` version.
4. The workflow builds the wheel and sdist once from the reviewed main SHA, validates bounded
   metadata and integrity, and installs the exact wheel in a clean Python 3.14 environment. Only
   those verified files are transferred to the `pypi` environment job.
5. A minimal `contents: write` job atomically creates the tag ref at the exact reviewed SHA before
   publishing the matching GitHub Release. A failed-job rerun accepts a partially created tag or
   Release only after verifying its exact SHA, tag, target, draft, and prerelease state. The PyPI
   job then rechecks that the version is still unpublished and uploads the verified files with
   PEP 740 attestations through OIDC.
6. The reusable container workflow checks out the same SHA, derives both image tags from the
   detected version, and runs the rootless final-image, paired E2E, and Critical-vulnerability
   gates. It serializes each image once into a private `dir:` transport and uploads the complete
   backend/frontend staging artifact before any registry mutation. Authenticated preflight checks
   every source and version tag for both roles before the first copy, accepting only an absent tag
   or the exact intended digest. Skopeo then copies those exact staged bytes and verifies every
   remote digest. A later copy failure may leave a partial pair, but the retained staging artifact
   is sufficient for the bounded recovery path below; neither normal publication nor recovery
   rebuilds an image. The workflow then generates provenance and attaches both SBOM sets,
   publication receipts, and paired evidence to the verified Release identity. Because job
   credentials are isolated, the attestation job performs its own ephemeral GHCR login immediately
   before pushing provenance; it does not reuse or persist the publication job's credentials.

After the version pull request is merged, suspend unrelated integrations and monitor the automatic
release workflow on `main` to a terminal result. If publication succeeds, immediately open the
follow-up pull request from the resulting evidence: copy the exact `registry_manifest_digest` from
`registry-publication.json` into the Compose `version@sha256:...` reference, verify it anonymously
against GHCR, perform any release-owned public command migration, and run the documented
standalone and distributed quickstarts against that exact digest. The follow-up must restore full
alignment before unrelated work is integrated. If publication fails, investigate or use the
bounded recovery path below; never infer a digest or point Compose at an unpublished tag.

If GitHub loses a run before creating any job, first prove that every external release surface is
absent and attempt the normal, forced, and platform-advised cancellation or rerun paths. Record the
orphaned run ID. If GitHub still cannot close or rerun it, increment
`tool.markweave.release.attempt` by exactly one in a protected pull request without changing the
version. Keep the existing workflow concurrency group: a recovered pending run is serialized with
the retry, while atomic tag creation and exact-SHA verification prevent two source SHAs from both
passing publication if the platform revives the orphan unexpectedly.

The release orchestrator and reusable container workflow use the same trusted push context; they do
not depend on a Release event. Tags and Releases created with `GITHUB_TOKEN` therefore cannot cause
a duplicate publication run. Container evidence attachment verifies the tag and Release SHA before
using `--clobber`, making a retry of that attachment idempotent. Any pre-existing tag, Release, or
PyPI version blocks a fresh run rather than being silently reused. Investigate partial external
state before authorizing any manual recovery. Recovery must not rebuild either container: registry
serialization is not guaranteed to be byte-reproducible across hosted Podman versions. Run
`container-release.yml` from `main` with the exact existing version, `v<version>` tag, reviewed
source SHA, and the ID of a failed source run whose `build-and-publish` job successfully retained
the exact pre-mutation staging artifact before the job failed.

Before download, recovery verifies the source run belongs to this upstream repository and exact
workflow, ran from trusted `main` at a descendant of the release source and an ancestor of the
current trusted `main` workflow SHA, reached the successful pre-mutation staging step, and has one
bounded non-expired artifact with matching repository/run metadata. It downloads by immutable
artifact ID, not name alone. It then validates the exact regular-file set, closed checksum bundle,
OCI archives and metadata relationships, publication receipts, release version/tag/source, and the
state of both public GHCR digests. The recovery job has scoped `packages: write` permission because
it may need to publish the missing role from the retained bytes; it preflights both roles before
copying, accepts an already-correct role idempotently, and rejects any conflicting digest. Only
after both exact digests are public does it transfer the unchanged evidence into the recovery run.
Separate jobs attest those exact public digests and attach the evidence to the already verified
Release. Recovery has no OIDC permission, never enters the PyPI environment, and cannot invoke
either build or Python publication.

The dispatch shape is:

```bash
gh workflow run container-release.yml --ref main \
  -f artifact-run-id=FAILED_RUN_ID \
  -f version=VERSION \
  -f tag=vVERSION \
  -f source-sha=RELEASE_SOURCE_SHA
```

Before dispatch, re-query the run and artifact to prove that retention has not expired. Afterward,
verify the attestation and all Release attachments before treating recovery as complete. The
`0.3.0` evidence recovery was completed successfully; its historical run and artifact identifiers
are recorded in `tickets/T22-finalize-cicd.md`, not presented here as a reusable current command.

Every GHCR conflict observed during authenticated
preflight fails before a copy. Repository and workflow concurrency prevent the automation from
racing itself. GHCR does not provide a relied-upon conditional manifest creation primitive here,
so another principal with `packages: write` could still change a tag in the narrow interval between
inspection and copy; do not grant that permission outside the release workflow.

The retained OCI archive and registry serialization can have different manifest digests for the
same local image. `image-metadata.json` remains internally bound to `image.oci.tar` and its SBOM;
`registry-publication.json` records that archive digest beside the exact registry digest, reviewed
source SHA, and version. Provenance uses the registry digest produced by the staged `dir:` transport.

## Post-publication verification

For released version `<version>`:

- verify `https://pypi.org/project/markweave/<version>/` shows Apache-2.0 metadata, the exact wheel
  and sdist, and attestations;
- install the wheel into fresh Python 3.14 environments for the base package and every supported
  extra, then verify `from markweave import __version__`, the exact version, and the installed CLI;
- verify GHCR tag `<version>` resolves to the workflow digest and is anonymously pullable;
- verify provenance identifies this repository and reviewed main SHA;
- verify the GitHub tag `v<version>` targets that same SHA and every release evidence file is
  attached to its published GitHub Release.

Preserve workflow URLs and immutable digests in the release record. Do not publish, replace, or
delete external release state outside this protected automation without explicit approval.

## Frontend publication after T64

The release workflow publishes the backend and frontend as one evidence-bound pair without
replacing the established trust model. The frontend package identity is
`ghcr.io/guillaume-lombardo/md-converter-web`; it shares the Markweave version, source SHA,
`v<version>` tag, GitHub Release, and protected human gate with the backend but has its own registry
manifest digest, SBOMs, scan report, archive-to-registry receipt, and provenance.

One release is deployable only when the PyPI artifact and both image receipts agree on version and
source SHA, both public digests are anonymously readable, and the release evidence manifest binds
the pair plus the frontend lockfile digest. T64 completes parity and the rollback rehearsal before
removing the legacy renderer from candidate source. The `0.6.1` continuation source satisfies that
gate; the release workflow builds and serializes each final image once, runs the complete rootless acceptance matrix
against those exact staged bytes, and publish the same bytes. It must not test one image and rebuild
another after legacy removal. If publication is partial, recover the missing image/evidence from
the retained exact staged bytes without rebuilding, or fail the release. Never pair an older
frontend with a newer backend, infer a digest, or use a mutable tag as rollback identity.

The post-publication adoption pull request pins both exact public manifest digests in Compose,
quickstarts, and deployment evidence before unrelated integration resumes. The existing GHCR
preflight, exact-copy verification, narrow external-writer race disclosure, permissions,
concurrency, provenance, attachment, and anonymous verification rules apply separately to each
package. See [the reviewed migration architecture](nextjs-migration-architecture.md) for the image
baseline, staged cutover, and release-level rollback contract.

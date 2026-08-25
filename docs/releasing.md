# Release process

Version `0.3.0` is the first approved public release. `project.version` in `pyproject.toml` is the
authoritative PEP 440 version, and every release tag is derived as `v<project.version>`. The source
and `markweave` distribution are Apache-2.0 licensed, the public import is `markweave`, and the
container repository is `ghcr.io/guillaume-lombardo/md-converter`.

A protected pull-request merge to `main` is the sole human release gate. When that merge changes
`project.version`, the automatic workflow compares the exact before and head commits from the
trusted push. A `pyproject.toml` edit that leaves the version unchanged is a successful no-op. Pull
requests, forks, tags, and GitHub Release events cannot start publication. Manual dispatch cannot
publish Python artifacts; it is reserved for the container-only recovery described below.

## One-time GitHub and PyPI configuration

Keep the GitHub Actions environment `pypi` without required reviewers, wait timers, deployment
restrictions, or environment secrets. It is only the OIDC Trusted Publisher identity boundary.
Configure the PyPI Trusted Publisher with these exact values:

- PyPI project name: `markweave`
- GitHub owner: `Guillaume-Lombardo`
- GitHub repository: `simple-md-to-docx-converter`
- Workflow: `release.yml`
- Environment: `pypi`

No PyPI token belongs in GitHub. The first upload may use a pending publisher; later uploads use the
normal publisher created from it. The first container push may create a private GHCR package. Set
the `md-converter` package visibility to public before announcing the release and verify anonymous
pull access. Do not grant the package to unrelated repositories.

## Prepare a version release

1. Change only the intended release version in `project.version` and the matching application
   version source. Use canonical final public PEP 440 syntax. Pre-releases, development releases,
   local versions, epochs, invalid spellings, version downgrades, and mismatched application
   versions fail closed. A more explicit canonical spelling such as `0.3` to `0.3.0` remains a
   valid transition even though the two parsed PEP 440 versions have equal precedence.
2. Open a pull request and require the complete `CI / gate`, independent review, and protected
   merge to `main`. The first approved transition is `0.2.0` to `0.3.0`; future versions are not
   hardcoded in the workflows.
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
6. The reusable container workflow checks out the same SHA, derives the image tag from the detected
   version, and runs the rootless final-image and Critical-vulnerability gates. It serializes the
   image once into a private `dir:` transport, verifies the registry manifest bytes against
   Podman's digest file, and uses Skopeo to copy those exact staged bytes to the `source-<SHA>` and
   version tags. Authenticated preflight accepts only an absent tag or that same digest, and each
   copy is followed by an exact remote digest check. It then generates provenance and attaches the
   SBOM, publication receipt, and evidence to the verified Release identity. Because job
   credentials are isolated, the attestation job performs its own ephemeral GHCR login immediately
   before pushing provenance; it does not reuse or persist the publication job's credentials.

The release orchestrator and reusable container workflow use the same trusted push context; they do
not depend on a Release event. Tags and Releases created with `GITHUB_TOKEN` therefore cannot cause
a duplicate publication run. Container evidence attachment verifies the tag and Release SHA before
using `--clobber`, making a retry of that attachment idempotent. Any pre-existing tag, Release, or
PyPI version blocks a fresh run rather than being silently reused. Investigate partial external
state before authorizing any manual recovery. A container-only recovery must run the
`container-release.yml` workflow from `main` with the exact existing version, `v<version>` tag, and
reviewed source SHA. Before building or writing GHCR state, it checks out that source and verifies
its project version, tag target, and the complete published Release identity (`draft=false` and
`prerelease=false`). It never enters the PyPI environment or Python publication workflow. The
recovery unsets `SOURCE_DATE_EPOCH` only for the historical source build script so that its own
explicit deterministic timestamp remains unambiguous across supported Podman versions.

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
- install the wheel into a fresh Python 3.14 environment and verify
  `from markweave import __version__, create_app`, the exact version, and callable factory;
- verify GHCR tag `<version>` resolves to the workflow digest and is anonymously pullable;
- verify provenance identifies this repository and reviewed main SHA;
- verify the GitHub tag `v<version>` targets that same SHA and every release evidence file is
  attached to its published GitHub Release.

Preserve workflow URLs and immutable digests in the release record. Do not publish, replace, or
delete external release state outside this protected automation without explicit approval.

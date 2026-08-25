# Release process

Version `0.3.0` is the first approved public release. The authoritative PEP 440 version is
`project.version` in `pyproject.toml`; the corresponding Git tag and GitHub Release tag are exactly
`v0.3.0`. Publication is triggered only when that GitHub Release is published. Creating or pushing a
tag, opening a pull request, using the merge queue, or manually dispatching a workflow cannot
publish Python or container artifacts.

The source and `markweave` Python distribution are licensed under Apache-2.0. Its documented public
import is also `markweave`. The public container image is
`ghcr.io/guillaume-lombardo/md-converter:0.3.0`. Release workflows pin every action by a full commit
SHA and serialize each release without cancelling an upload in progress.

## One-time GitHub and PyPI configuration

Create the GitHub Actions environment `pypi` in repository settings without required reviewers,
wait timers, deployment-branch restrictions, or environment secrets. It exists only as the OIDC
Trusted Publisher identity boundary. Publishing the GitHub Release is the sole human gate. This
removes a second approval, so restrict GitHub release creation to trusted maintainers and treat the
repository guard in the workflow as security-critical.

Before the first release, create a PyPI pending Trusted Publisher with these exact values:

- PyPI project name: `markweave`
- GitHub owner: `Guillaume-Lombardo`
- GitHub repository: `simple-md-to-docx-converter`
- Workflow: `release.yml`
- Environment: `pypi`

The pending publisher does not reserve the project name. No PyPI token belongs in GitHub. The
release workflow rechecks both the PyPI JSON and Simple Index endpoints immediately before upload;
anything other than two `404` responses stops the first publication.

The first container push may create a private GHCR package despite the public-image policy. In the
package settings, change `md-converter` visibility to public before announcing the release, then
verify that an anonymous client can pull the immutable `0.3.0` tag. Do not grant the package access to
unrelated repositories.

## Prepare and publish `v0.3.0`

1. Require a clean `main` commit whose complete `CI / gate` run passed, including both rootless E2E
   profiles and the final-container domain. The approved complete suite runs Sunday at 03:17 UTC,
   gives each heavy job 45 minutes, and runs at most two heavy matrix jobs concurrently.
2. Confirm `project.version`, `markweave.__version__`, the OpenAPI version, and conversion
   traceability all report `0.3.0`.
3. Create the signed or protected tag `v0.3.0` at that reviewed commit and draft a GitHub Release from
   the tag. Do not publish it until the `pypi` environment and pending publisher above exist.
4. Publish the GitHub Release. The Python workflow builds the wheel and sdist once, validates their
   bounded metadata and integrity, installs the exact wheel in a clean Python 3.14 environment, and
   transfers only those verified distributions to the protected upload job. The PyPI action uploads
   those bytes with PEP 740 attestations through OIDC.
5. The container workflow rebuilds and rootless-smoke-tests the pinned final image, rejects every
   Critical vulnerability for release, pushes the immutable GHCR tag, generates an image provenance
   attestation, and attaches CycloneDX/SPDX SBOMs plus vulnerability and identity evidence to the
   GitHub Release.

## Post-publication verification

After the first successful PyPI upload, verify that `https://pypi.org/project/markweave/` shows
version `0.3.0`, Apache-2.0 metadata, both wheel and sdist, and attestations. Confirm that the pending
publisher became a normal publisher with the same owner, repository, workflow, and environment.
Install the wheel into a fresh Python 3.14 environment and run:

```python
from markweave import __version__, create_app

assert __version__ == "0.3.0"
assert callable(create_app)
```

Verify the GHCR `0.3.0` digest matches the workflow output, the provenance attestation verifies for
the repository identity, the package is publicly pullable without authentication, and every release
evidence file is attached to the GitHub Release. Preserve the workflow URLs and immutable digests in
the release record.

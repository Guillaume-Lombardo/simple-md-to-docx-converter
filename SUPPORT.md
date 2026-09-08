# Support policy

## Get help

Use [GitHub Discussions](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/discussions)
for installation, upgrade, configuration, command-line, API, document-conversion,
and template questions. Use [GitHub Issues](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/issues)
for reproducible product defects that are not security-sensitive. Search the
repository documentation and existing Discussions or issues before posting.
Include the Markweave version or container digest, operating system, selected
storage profile, relevant command or endpoint, expected behavior, actual behavior,
and a minimal sanitized reproduction.

Security vulnerabilities do not belong in public Discussions or issues. Follow
the private reporting instructions in [SECURITY.md](SECURITY.md).

## Deployment and operations

Use the repository's deployment and operations guides for:

* installation, development, and dependency updates: [local development](docs/local-development.md);
* rootless image, container, Kubernetes, and configuration guidance:
  [container deployment](docs/container-deployment.md) and
  [configuration reference](docs/configuration.md);
* standalone and distributed persistence: [storage profiles](docs/storage-profiles.md);
* recovery and restore evidence: [recovery](docs/recovery.md); and
* conversion input, output, and template behavior: [conversion jobs](docs/jobs.md)
  and [word templates and fonts](docs/word-templates-fonts.md).

The documented standalone and distributed profiles are the supported application
topologies. Operators remain responsible for TLS termination, secrets management,
network policy, capacity limits, backup storage, restore drills, monitoring, and
their host or orchestrator. The local Compose quickstarts are evaluation profiles,
not production deployment guidance. Do not claim OpenShift compatibility: its
required target-cluster validation remains deferred.

## Safe information to share

Public questions and bug reports may include a minimal synthetic Markdown input,
sanitized error category, non-secret setting names and values, package versions,
container digests, and logs only after removing document content, filenames,
absolute paths, user identifiers, secrets, and credentials.

Do not post or attach customer documents, production backups, passwords, session
cookies, API tokens, private keys, secret environment-variable values, database
dumps, or raw production logs. Do not share a hostile document or exploit sample
publicly. Use the private security process if that material is necessary to assess
a suspected vulnerability.

## Support boundaries

The project can help explain the documented Markweave release, API, CLI,
configuration, storage profiles, conversion behavior, and upgrade paths. It cannot
operate a user's infrastructure, inspect private documents or backups, recover
lost data, provide access to third-party services, or guarantee compatibility with
custom images, unreviewed plugins, modified source trees, unsupported versions, or
undocumented deployment configurations.

For upstream defects, include a minimal sanitized reproduction and relevant
versions. Maintainers may identify the appropriate upstream project, but upstream
support and remediation remain that project's responsibility.

# Security policy

## Report a vulnerability privately

Do not open a public issue for a suspected vulnerability. Use GitHub's private
[security advisory reporting form](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/security/advisories/new).
If that form is unavailable, open a GitHub Discussion asking a maintainer for a
private reporting channel; do not include vulnerability details in the discussion.

Give the maintainers enough safe information to reproduce and assess the report:

* a concise description of the observed behavior and potential impact;
* the affected Markweave release or image digest, deployment profile, and relevant
  non-secret configuration names and values after redaction;
* a minimal, non-destructive reproduction or proof of concept; and
* dependency, operating-system, container-image, or document-engine versions when
  they are relevant.

Never post or attach passwords, session cookies, API credentials, private keys,
access tokens, secret configuration values, backups, production logs, or customer
documents. Do not attempt to access other users' data, disrupt a service, or test
against a system you do not own or have permission to assess. If reproducing the
problem requires a document, provide a sanitized minimal fixture only through the
private channel after a maintainer requests it.

## Supported releases

Security fixes are provided for the newest published Markweave release in the
active `0.x` release line. Older releases, unreleased commits, development
installations, forks, and modified container images are not supported release
targets. Users should upgrade to the newest published release before requesting a
fix or mitigation, unless doing so would make investigation unsafe.

| Release | Security-fix status |
| --- | --- |
| Newest published release in the active `0.x` line | Supported |
| Older published releases and all other versions | Upgrade required |

The project may publish a new patch release, container image, configuration
mitigation, or operational workaround, according to the severity and practical
exposure. Critical confirmed vulnerabilities receive urgent triage and mitigation
without waiting for the normal weekly dependency and container-vulnerability
review. A fix remains subject to the project's normal validation and release
controls.

## Triage and coordinated disclosure

Maintainers review private reports, confirm the affected scope, and keep
investigation details private while a fix or mitigation is prepared. They may ask
for clarification through the private thread and will communicate the planned next
update there. Please allow time for validation across the supported package and
container release paths before public disclosure.

Do not disclose the vulnerability publicly, publish an exploit, or open a public
issue until the maintainers and reporter have coordinated the disclosure. Once a
fix or mitigation is ready, the project will publish a security advisory or release
notes with the affected scope, remediation, and acknowledgement where appropriate.
If a reporter plans disclosure on a deadline, include that deadline in the private
report so it can be discussed safely.

## Dependency and container reports

Report known vulnerabilities in Python dependencies, operating-system packages,
the UBI base image, Pandoc, Chromium, Mermaid, LibreOffice, fonts, or the published
container image through the same private process when Markweave's packaging or
runtime configuration may expose users. Include the advisory identifier, affected
version or image digest, and why the project is affected. Do not assume an advisory
is exploitable without checking the deployed profile and configuration.

Markweave reviews engine, base-image, operating-system package, font, and
transitive-dependency vulnerabilities at least weekly. Critical vulnerabilities
are triaged urgently and are rebuilt or mitigated without waiting for that review;
the project records compatibility-regression and rollback evidence for the change.

## Scope

This policy covers the published `markweave` Python distribution, the source
repository, project-controlled release artifacts, and documented Markweave
container images and deployment configurations. It includes vulnerabilities in
upload handling, document conversion, authorization, secrets handling, generated
files, dependency supply chain, and the documented runtime boundaries.

Third-party services, unsupported customizations, local operating-system or
orchestrator configuration outside the documented deployment contract, and
vulnerabilities solely in an upstream project may be outside the project's direct
fix scope. Report a suspected interaction anyway; maintainers will confirm whether
Markweave is affected and, when appropriate, coordinate with the responsible
upstream project.

Markweave's documented rootless Podman and local k3s security properties are
validated separately. OpenShift compatibility must not be assumed: the required
target-cluster proof remains deferred.

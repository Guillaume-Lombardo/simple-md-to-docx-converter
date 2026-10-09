---
ticket: T96
linear_id: G1L-616
linear_url: https://linear.app/g1lom/issue/G1L-616/t96-approve-the-2026-10-scope-changes-in-the-product-specification
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T96 - Approve the 2026-10 scope changes in the product specification

## Objective

Record the owner decisions that the accepted 2026-10-09 findings require before any implementation contradicts a fixed decision (section 2), a functional or input contract (sections 3-5), or an exclusion (section 14) of the product specification.

Created from the repository analysis accepted by the owner on 2026-10-09 (section: decision gate for the accepted findings).

## Acceptance criteria

- D1 (T106): amend the section 2 CLI boundary to allow an explicit local conversion command that runs the existing pipeline without the HTTP API, and define its malware-scanning, engine-version and traceability contract.
- D2 (T104): amend the sections 3.1 and 4 input contract to accept a multi-file or folder upload of Markdown plus local images, normalized into the existing approved archive boundary.
- D3 (T105): amend section 5 to permit a closed, sanitized inline HTML allowlist (for example `<br>`, `<sub>`, `<sup>`, `<kbd>`) while every other raw HTML node is still rejected.
- D4 (T111, T118): remove automatic table of contents and PDF/A from the section 14 exclusions and define them as optional, template- or job-level options.
- D5 (T112): approve or reject a pinned `pandoc-crossref` binary matched to the approved Pandoc version, or choose a bundled internal filter.
- D6 (T113): approve citeproc use, the bibliography input contract and administrator-managed CSL styles, including CSL style licensing.
- D7 (T114): replace the section 2 'no API token' decision with scoped, revocable, audited API tokens and define their CSRF exemption.
- D8 (T115): extend the section 2 initial-authentication decision to OIDC single sign-on and group-to-role mapping; record whether SAML/LDAP stay out of scope.
- D9 (T116): define the outbound-network policy for webhook delivery (dedicated dispatcher, destination allowlist, SSRF protections); document engines stay isolated.
- D10 (T117): approve an MCP surface, its transport (local stdio wrapper and/or remote HTTP) and its authentication.
- D11 (T122): approve HTML, EPUB and ODT as additional output families.
- D12 (T124): decide whether an administrator-approved Pandoc Lua filter catalogue is acceptable despite the section 5 'disable user filters' rule, and its sandbox.
- D13 (T126): amend the AGENTS.md language rule for user-interface copy if the browser interface is internationalized.
- D14 (T127): decide whether Linux/ARM64 becomes a supported image platform.
- D15 (T108): record the reverse-conversion feature freeze.
- Update section 2, 3, 4, 5, 8 and 14 text and the section 13 table for every approved decision; record every rejected decision and cancel or re-scope its ticket in Linear and in the local mirror.

## Dependencies

- None.

## Blocking points

- Every decision is an owner decision; no implementation agent may resolve one.
- Decisions D1-D15 can be approved independently; each dependent ticket is unblocked as soon as its own decision is recorded on main.

## Progress

- 2026-10-09: Created in Linear and mirrored here from the owner-accepted repository analysis.
  Not started.
- 2026-10-09: The owner reviewed recommendations for D1-D15 and approved them. D1-D10 and D15 are
  approved as recommended; D11 approves HTML and ODT and defers EPUB; D12 rejects the Lua filter
  catalogue (T124 cancelled); D13 and D14 are deferred (T126 and T127 stay in the backlog). The
  decisions are recorded in new specification section 3.7, with the matching section 2, 3.4, 4, 5,
  7, 8, 13 and 14 amendments, and the dependent ticket mirrors and Linear issues are updated.
  Documentation tests pass. T96 stays In Progress until the change is verified on main.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-616.

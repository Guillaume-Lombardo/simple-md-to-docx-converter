---
ticket: T99
linear_id: G1L-618
linear_url: https://linear.app/g1lom/issue/G1L-618/t99-consolidate-compose-variants-with-profiles
status: Backlog
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T99 - Consolidate Compose variants with profiles

## Objective

Reduce the ten root Compose files to a smaller set that uses Compose profiles and documented overrides, without changing any deployed behavior.

Created from the repository analysis accepted by the owner on 2026-10-09 (section: what to change, item 6, and what to remove, compose files).

## Acceptance criteria

- Inventory every Compose file, its consumers (quickstart scripts, E2E harness, CI, docs) and its security posture (trusted upstream, Podman, Next.js, Composer, test).
- Replace mutually exclusive variants with profiles or a minimal set of overrides; keep the loopback-only, ClamAV-by-default and trusted-upstream restrictions intact.
- Update `scripts/quickstart*.sh`, the E2E harness, CI and every guide.
- Prove equivalence by comparing `docker compose config` / `podman compose config` output for every supported mode before and after.
- Both final-image E2E profiles and the quickstart tests pass.

## Dependencies

- None.

## Blocking points

- Must not land while a published release and the public Compose pin differ (AGENTS.md release rule); schedule outside a release-adoption window.

## Progress

- 2026-10-09: Created in Linear and mirrored here from the owner-accepted repository analysis.
  Not started.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-618.

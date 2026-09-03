---
ticket: T74
linear_id: G1L-543
linear_url: https://linear.app/g1lom/issue/G1L-543/t74-validate-relectio-github-integration-with-a-temporary
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T74 - Validate Relectio with a temporary GitHub pilot

## Objective

Exercise the Relectio GitHub App integration on a small, synthetic documentation-only
pull request. This temporary pilot belongs to the external Relectio FORGE-012 validation.
It changes no Markweave behavior and is not intended for merge.

## Acceptance criteria

- Open one dedicated draft pull request with no product-code changes.
- Observe App 4763464 publishing against the current pull-request commit.
- Record completion and restart/replay evidence without duplicate publication in FORGE-012.
- Close the temporary pull request without merging after collecting the evidence.

## Dependencies

- Relectio FORGE-012 and its deployed GitHub publication fix FORGE-012E4.
- Existing repository CI and contribution rules remain unchanged.

## Implementation boundary

Only this synthetic Markdown ticket is added. No source code, dependency, workflow,
policy, product specification, credential, or runtime configuration is modified.

## Quality requirements

The real integration pilot is the validation target. Product tests are not claimed as
executed; the draft remains subject to its ordinary GitHub checks. All local validation
for this pilot runs on codex-dev, not on the workstation.

## Progress

- 2026-09-03: Created the matching Linear issue before preparing this dedicated pilot.
- Current review and publication outcomes are pending, not asserted as successful.

## Synchronization

The Relectio orchestrator owns Linear synchronization, durable FORGE-012 evidence,
and eventual cleanup. This temporary unmerged ticket must not be marked Done as a
product delivery.

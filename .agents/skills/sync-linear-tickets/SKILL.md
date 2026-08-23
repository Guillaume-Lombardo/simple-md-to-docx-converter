---
name: sync-linear-tickets
description: Keep the Markdown to DOCX and PDF Converter project synchronized between Linear issues and repository ticket mirrors in tickets/*.md. Use when selecting, starting, updating, blocking, completing, splitting, or adding project work; when scope, status, priority, dependencies, acceptance criteria, or progress changes; and before publishing or merging work associated with a ticket.
---

# Synchronize Linear tickets

## Identify the ticket

1. Read `AGENTS.md`, `docs/product-specification.md`, and the matching `tickets/Txx-*.md` file.
2. Use the `linear_id` from the file to fetch the Linear issue. Never match only by title.
3. Confirm that the issue belongs to the Linear project `Markdown to DOCX and PDF Converter` and team `G1lom`.
4. Compare title, status, priority, scope, acceptance criteria, dependencies, and progress before starting work.
5. Stop and report the conflict when both surfaces changed incompatibly; do not guess which edit wins.

## Start or update work

1. Treat Linear as the operational source for status, assignment, and current coordination.
2. Treat `tickets/*.md` as the reviewable repository mirror for scope, acceptance criteria, dependencies, and durable progress notes.
3. Before implementation, set the Linear issue to `In Progress` and update the local `status` field and progress section in the same work branch.
4. Keep the ticket identifier in the branch subject, commit context, and pull-request description when applicable. Follow the branch naming rules in `AGENTS.md`.
5. When work is blocked, record the blocking reason and dependency in both places. Use Linear blocking relationships for ticket-to-ticket dependencies.
6. When scope or acceptance criteria change, update both surfaces before continuing implementation.

## Add work

1. Confirm that no existing Linear issue or local ticket already covers the work.
2. Allocate the next unused `Txx` code from the repository sequence.
3. Create the Linear issue in the existing project and capture its real identifier and URL.
4. Create `tickets/Txx-<slug>.md` with the same title, objective, acceptance criteria, dependencies, initial status, Linear identifier, and URL.
5. Add blocking relationships in Linear and matching dependency codes in Markdown.
6. Update the product specification when the new work changes the normative delivery plan.

## Complete work

1. Verify every acceptance criterion and canonical repository check, including required integration and E2E coverage; record only explicitly approved exceptions.
2. Update the local progress section with the delivered result, tests, pull request, and limitations.
3. Update Linear with the same outcome and mark it `Done` only after the change is verified on `main`.
4. Re-fetch the Linear issue and compare it with the merged local ticket before declaring synchronization complete.

## Guardrails

- Never create a duplicate project or issue to resolve a synchronization conflict.
- Never invent Linear identifiers, URLs, states, or dependency relationships.
- Never mark a ticket complete because code was merely committed or a pull request was opened.
- Never overwrite richer acceptance criteria with a shorter summary.
- Keep all committed ticket content and Linear content in English.
